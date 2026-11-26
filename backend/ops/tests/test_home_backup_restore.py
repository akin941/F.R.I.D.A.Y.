"""Phase 13 branch 7 (brief D14): the home server's backup and restore
drill scripts, run for real against this test run's Postgres.

`deploy/home/friday-backup.sh` dumps the test database (with node audit
chain rows committed) through a throwaway age key into a LOCAL restic
repository (no network, never a bucket) and writes `status.json`;
`deploy/home/friday-restore-drill.sh` restores the dump into a scratch
database, runs `migrate --check` and `verify_node_audit_chain` there and
writes `drill.json`. A tampered dump makes the drill fail with
`result: fail`. The `home_*` preflight rows read both files.

Needs `age`, `age-keygen`, `restic`, `pg_dump`, `pg_restore` and `psql` on
PATH. Where they are missing the module is skipped -- except in the
dedicated `home-ops` CI job, which sets `FRIDAY_HOME_OPS=1` (a missing tool
is then a failure) and installs them from apt."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from django.conf import settings as django_settings
from django.db import connection

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from nodes import bus
from nodes.audit_chain import append
from nodes.models import NodeInvocation
from nodes.protocol import sha256_hex
from nodes.services import pair_node
from ops import preflight
from ops.preflight import FAIL, PASS
from workspaces.models import Workspace

REPO = Path(__file__).resolve().parents[3]
HOME_DIR = REPO / "deploy" / "home"
BACKUP = HOME_DIR / "friday-backup.sh"
DRILL = HOME_DIR / "friday-restore-drill.sh"
TOOLS = ("age", "age-keygen", "restic", "pg_dump", "pg_restore", "psql", "bash", "flock")
REQUIRED = os.environ.get("FRIDAY_HOME_OPS") == "1"
_MISSING = [tool for tool in TOOLS if shutil.which(tool) is None]

if not BACKUP.is_file():  # a backend-only image, not a checkout
    pytest.skip("no deploy/home checkout around the backend", allow_module_level=True)
if _MISSING and not REQUIRED:
    pytest.skip(
        f"home-ops tools not installed ({', '.join(_MISSING)}); the home-ops CI job runs this",
        allow_module_level=True,
    )

pytestmark = pytest.mark.django_db(transaction=True)

STATUS_KEYS = {"v", "finished_at", "dump", "bytes", "sha256", "restic_snapshot"}
DRILL_KEYS = {"v", "drilled_at", "dump", "result"}
_DUMP_NAME = re.compile(r"friday-\d{8}T\d{6}Z\.dump\.age")
CHAIN_ROWS = 3


def test_the_tools_are_installed():
    assert _MISSING == []


def _run(argv, env, *, check=True, timeout=300):
    completed = subprocess.run(  # noqa: S603 -- our own scripts, fixed argv
        argv, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"{argv[0]} exited {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _db():
    return django_settings.DATABASES["default"]


def _url(name):
    db = _db()
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST'] or 'localhost'}:"
        f"{db['PORT'] or 5432}/{name}"
    )


def _base_env(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PG", "RESTIC_", "FRIDAY_", "AWS_"))
    }
    env["RESTIC_CACHE_DIR"] = str(tmp_path / "restic-cache")
    return env


@pytest.fixture
def home(tmp_path):
    """The runbook's layout under tmp_path: backup dir, an age key pair (the
    PUBLIC key in the server's config, the private one kept aside like the
    owner's password manager), a local restic repository, a secrets file."""
    backup_dir = tmp_path / "backups"
    config = tmp_path / "etc"
    backup_dir.mkdir()
    config.mkdir()
    env = _base_env(tmp_path)
    key = tmp_path / "owner" / "friday-backup.agekey"
    key.parent.mkdir()
    _run(["age-keygen", "-o", str(key)], env)
    public = _run(["age-keygen", "-y", str(key)], env).stdout.strip()
    assert public.startswith("age1")
    (config / "age.pub").write_text(public + "\n")
    (config / "restic.pass").write_text("throwaway-test-restic-password\n")
    repo = tmp_path / "restic-repo"
    (config / "restic.env").write_text(
        f"RESTIC_REPOSITORY={repo}\nRESTIC_PASSWORD_FILE={config / 'restic.pass'}\n"
    )
    _run(
        ["restic", "init", "--quiet"],
        {
            **env,
            "RESTIC_REPOSITORY": str(repo),
            "RESTIC_PASSWORD_FILE": str(config / "restic.pass"),
        },
    )
    secrets_file = tmp_path / "docker.env"
    secrets_file.write_text("NODE_SIGNING_KEY=not-a-real-key\nDJANGO_SECRET_KEY=not-real\n")
    db = _db()
    backup_env = {
        **env,
        "FRIDAY_REPO_DIR": str(REPO),
        "FRIDAY_BACKUP_DIR": str(backup_dir),
        "FRIDAY_AGE_RECIPIENTS": str(config / "age.pub"),
        "FRIDAY_RESTIC_ENV": str(config / "restic.env"),
        "FRIDAY_ENV_FILE": str(secrets_file),
        "FRIDAY_PG_DUMP_MODE": "direct",
        "PGHOST": db["HOST"] or "localhost",
        "PGPORT": str(db["PORT"] or 5432),
        "PGUSER": db["USER"],
        "PGPASSWORD": db["PASSWORD"],
        "PGDATABASE": db["NAME"],
    }
    return {
        "dir": backup_dir,
        "key": key,
        "repo": repo,
        "config": config,
        "env": env,
        "backup_env": backup_env,
        "secrets": secrets_file,
    }


@pytest.fixture
def chain():
    """An owner, a paired node and committed chain rows in the test DB."""
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    raw_code, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    node = pair_node(raw_code=raw_code, node_name="MacBook", protocol_versions=[1]).node
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    for index in range(CHAIN_ROWS):
        append(
            workspace=workspace,
            node=node,
            capability="git.status",
            capability_version=1,
            args_sha256=sha256_hex({"repo_path": f"~/work/{index}"}),
            status=NodeInvocation.Status.OK,
            channel="web",
            trace_id=f"trace-{index}",
            nonce="A" * 43,
            output_sha256=sha256_hex({"branch": "main"}),
            output_bytes=17,
            duration_ms=12,
        )
    yield node
    bus.clear_presence(node.pk)


@pytest.fixture
def scratch_db():
    """Empty scratch databases on the same server, dropped afterwards."""
    made = []

    def make():
        name = f"{_db()['NAME']}_drill_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(_url(_db()["NAME"]), autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        made.append(name)
        return name

    yield make
    for name in made:
        with psycopg.connect(_url(_db()["NAME"]), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _backup(home, *, check=True):
    return _run(["bash", str(BACKUP)], home["backup_env"], check=check)


def _drill(home, scratch_name, *, dump=None, check=True):
    env = {
        **home["env"],
        "FRIDAY_REPO_DIR": str(REPO),
        "FRIDAY_BACKUP_DIR": str(home["dir"]),
        "FRIDAY_DRILL_MODE": "direct",
        "FRIDAY_DRILL_DATABASE_URL": _url(scratch_name),
        # The restored copy is checked with the dev settings the owner's
        # checkout uses (not the test settings, which add a test database).
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
    }
    argv = ["bash", str(DRILL), str(home["key"])]
    if dump is not None:
        argv.append(str(dump))
    return _run(argv, env, check=check, timeout=600)


def _status(home):
    return json.loads((home["dir"] / "status.json").read_text())


def _drill_status(home):
    return json.loads((home["dir"] / "drill.json").read_text())


def _rows(settings, home):
    settings.HOME_BACKUP_STATUS_PATH = str(home["dir"] / "status.json")
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(home["dir"] / "drill.json")
    return {row.name: row for row in preflight.check_home()}


def _scratch_count(name, sql):
    with psycopg.connect(_url(name)) as conn:
        return conn.execute(sql).fetchone()[0]


def test_backup_then_drill_round_trip(home, chain, scratch_db, settings):
    # --- backup ---------------------------------------------------------
    _backup(home)
    status = _status(home)
    assert set(status) == STATUS_KEYS
    assert status["v"] == 1
    assert _DUMP_NAME.fullmatch(status["dump"])
    dump = home["dir"] / status["dump"]
    raw = dump.read_bytes()
    assert status["bytes"] == len(raw) > 0
    assert status["sha256"] == hashlib.sha256(raw).hexdigest()
    # Encrypted before it touched the disk: an age file, no pg_dump header.
    assert raw.startswith(b"age-encryption.org/v1\n")
    assert b"PGDMP" not in raw
    for path in home["dir"].iterdir():
        assert b"PGDMP" not in path.read_bytes(), path.name
        assert not path.name.endswith(".part"), path.name
    # The secrets file was encrypted too; its plaintext is nowhere here.
    env_copies = list(home["dir"].glob("env-*.age"))
    assert len(env_copies) == 1
    assert b"NODE_SIGNING_KEY" not in env_copies[0].read_bytes()
    # The status file names the restic snapshot that holds the dump.
    restic_env = {
        **home["env"],
        "RESTIC_REPOSITORY": str(home["repo"]),
        "RESTIC_PASSWORD_FILE": str(home["config"] / "restic.pass"),
    }
    snapshots = json.loads(_run(["restic", "snapshots", "--json"], restic_env).stdout)
    assert [s["short_id"] for s in snapshots] == [status["restic_snapshot"]]
    assert snapshots[0]["tags"] == ["friday"]
    listed = _run(["restic", "ls", "--json", "latest"], restic_env).stdout
    assert status["dump"] in listed
    # No secret and no path in the status file.
    text = (home["dir"] / "status.json").read_text()
    assert str(home["dir"]) not in text
    assert "not-a-real-key" not in text

    # --- drill ----------------------------------------------------------
    scratch = scratch_db()
    completed = _drill(home, scratch)
    assert "node audit chain OK: 3 row(s)" in completed.stdout
    drill = _drill_status(home)
    assert set(drill) == DRILL_KEYS
    assert (drill["v"], drill["dump"], drill["result"]) == (1, status["dump"], "pass")
    # The restored copy really holds the chain.
    assert _scratch_count(scratch, "select count(*) from nodes_nodeinvocation") == CHAIN_ROWS
    assert _scratch_count(scratch, "select count(*) from django_migrations") > 0

    # --- preflight reads both files ---------------------------------------
    rows = _rows(settings, home)
    assert rows["home_backup_fresh"].status == PASS
    assert status["dump"] in rows["home_backup_fresh"].detail
    assert rows["home_restore_drill_age"].status == PASS


@pytest.mark.parametrize("keep_status_sha", [False, True])
def test_a_tampered_dump_fails_the_drill(home, chain, scratch_db, settings, keep_status_sha):
    _backup(home)
    status = _status(home)
    dump = home["dir"] / status["dump"]
    raw = bytearray(dump.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    dump.write_bytes(bytes(raw))
    if keep_status_sha:
        # The status file is rewritten to match: age's MAC still refuses it.
        status["sha256"] = hashlib.sha256(bytes(raw)).hexdigest()
        (home["dir"] / "status.json").write_text(json.dumps(status))
    scratch = scratch_db()
    completed = _drill(home, scratch, check=False)
    assert completed.returncode != 0
    drill = _drill_status(home)
    assert (drill["dump"], drill["result"]) == (status["dump"], "fail")
    row = _rows(settings, home)["home_restore_drill_age"]
    assert row.status == FAIL
    assert "the last restore drill failed" in row.detail


def test_the_drill_refuses_a_non_empty_database(home, chain, settings):
    _backup(home)
    before = NodeInvocation.objects.count()
    # Point the drill at the live (test) database itself: refused untouched.
    completed = _drill(home, _db()["NAME"], check=False)
    assert completed.returncode != 0
    assert "not empty" in completed.stderr
    assert _drill_status(home)["result"] == "fail"
    assert NodeInvocation.objects.count() == before
    with connection.cursor() as cursor:
        cursor.execute("select count(*) from nodes_nodeinvocation")
        assert cursor.fetchone()[0] == before


def test_a_drill_without_any_dump_fails(home, scratch_db):
    completed = _drill(home, scratch_db(), check=False)
    assert completed.returncode != 0
    assert _drill_status(home) | {"drilled_at": "x"} == {
        "v": 1,
        "drilled_at": "x",
        "dump": "",
        "result": "fail",
    }


def test_the_drill_restores_an_explicit_dump_from_the_off_site_copy(home, chain, scratch_db):
    """The quarterly off-site drill: `restic restore` the dump elsewhere and
    pass it to the drill as DUMP."""
    _backup(home)
    status = _status(home)
    restored = home["dir"].parent / "offsite"
    restic_env = {
        **home["env"],
        "RESTIC_REPOSITORY": str(home["repo"]),
        "RESTIC_PASSWORD_FILE": str(home["config"] / "restic.pass"),
    }
    _run(["restic", "restore", "latest", "--tag", "friday", "--target", str(restored)], restic_env)
    copies = list(restored.rglob(status["dump"]))
    assert len(copies) == 1
    _drill(home, scratch_db(), dump=copies[0])
    assert _drill_status(home)["result"] == "pass"


def test_the_backup_refuses_a_private_key_on_the_server(home):
    (home["config"] / "age.pub").write_text(home["key"].read_text())
    completed = _backup(home, check=False)
    assert completed.returncode != 0
    assert "PRIVATE key" in completed.stderr
    assert not list(home["dir"].glob("*.age"))
    assert not (home["dir"] / "status.json").exists()


def test_a_failed_dump_leaves_no_status_and_no_partial_file(home):
    home["backup_env"]["PGDATABASE"] = "friday_no_such_database"
    completed = _backup(home, check=False)
    assert completed.returncode != 0
    assert not (home["dir"] / "status.json").exists()
    assert not [p.name for p in home["dir"].iterdir() if p.suffix in (".age", ".part")]


def test_a_second_backup_keeps_a_valid_status(home, chain):
    _backup(home)
    first = _status(home)
    _backup(home)
    second = _status(home)
    assert second["restic_snapshot"] != first["restic_snapshot"]
    assert (home["dir"] / second["dump"]).is_file()


@pytest.mark.skipif(shutil.which("shellcheck") is None and not REQUIRED, reason="no shellcheck")
def test_the_scripts_are_shellcheck_clean():
    _run(["shellcheck", "-x", str(BACKUP), str(DRILL)], dict(os.environ))
