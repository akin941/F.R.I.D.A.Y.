"""Committed Docker Compose files (Phase 4 branch 1): nothing published
outside loopback, no secret literal. Plain-text checks -- no YAML
dependency -- over every compose file in deploy/docker/."""

import os
import re
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[3] / "deploy" / "docker"
COMPOSE_FILES = sorted(DOCKER_DIR.glob("compose*.yml"))

# Values that used to be committed (or are publicly known fixtures) and
# must never come back into a compose file.
FORBIDDEN_LITERALS = [
    "dev-insecure-key",
    "friday-dev-secret",
    "e2e-fixture-bot-token",
    "e2e-fixture-webhook-secret",
    "MINIO",
    "minio",
]

_SECRET_KEY_LINE = re.compile(
    r"^\s*-?\s*([A-Z0-9_]*(PASSWORD|SECRET|_KEY|TOKEN|REDISCLI_AUTH)[A-Z0-9_]*)\s*[:=]\s*(.+)$"
)
# A URL whose password is a literal rather than a `${...}` interpolation.
_URL_WITH_LITERAL_PASSWORD = re.compile(r"[a-z]+://[^\s/@\"'${}:]*:(?!\$\{)([^\s/@\"'$]+)@")


def _lines(path):
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


def test_compose_files_exist():
    names = {path.name for path in COMPOSE_FILES}
    assert {"compose.yml", "compose.daily.yml"} <= names


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_published_port_is_loopback_only(path):
    in_ports = False
    ports_indent = 0
    published = []
    for line in _lines(path):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "ports:":
            in_ports, ports_indent = True, indent
            continue
        if in_ports:
            if stripped.startswith("- ") and indent >= ports_indent:
                published.append(stripped[2:].strip().strip("\"'"))
                continue
            if stripped and indent <= ports_indent:
                in_ports = False
    for mapping in published:
        assert mapping.startswith("127.0.0.1:"), f"{path.name}: {mapping!r} is not loopback-only"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_no_committed_secret_literal(path):
    text = "\n".join(_lines(path))
    for literal in FORBIDDEN_LITERALS:
        assert literal not in text, f"{path.name} contains {literal!r}"
    for line in _lines(path):
        match = _SECRET_KEY_LINE.match(line)
        if match:
            value = match.group(3).strip().strip("\"'")
            assert value.startswith("${"), f"{path.name}: {match.group(1)} is a literal"
        assert not _URL_WITH_LITERAL_PASSWORD.search(line), f"{path.name}: {line!r}"


def test_secret_checks_catch_a_literal():
    """The two checks above really fail on the shapes they guard against."""
    assert _SECRET_KEY_LINE.match('      DJANGO_SECRET_KEY: "abc"').group(3) == '"abc"'
    assert _URL_WITH_LITERAL_PASSWORD.search("postgres://friday:friday@postgres:5432/friday")
    assert _URL_WITH_LITERAL_PASSWORD.search("redis://:hunter2@redis:6379/0")
    assert not _URL_WITH_LITERAL_PASSWORD.search("redis://:${REDIS_PASSWORD:?x}@redis:6379/0")
    assert not _URL_WITH_LITERAL_PASSWORD.search(
        '"postgres://friday:${POSTGRES_PASSWORD:?run `make secrets`}@postgres:5432/friday"'
    )


def test_env_example_next_to_compose_lists_the_required_secrets():
    text = (DOCKER_DIR / ".env.example").read_text()
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert re.search(rf"^#? ?{key}=\s*$", text, re.MULTILINE), key


# --- Phase 13b branch 2 (brief D1): the interim Mac server stack -------------
# `make up-mac-server` layers these files, in this order. The checks below
# parse them (PyYAML, merge keys resolved) and merge the keys that matter
# here the way Compose does; one more test lets the real `docker compose
# config` merge them when the CLI is installed (`config` needs no daemon,
# and none is contacted).

MAC_SERVER_FILES = (
    "compose.yml",
    "compose.daily.yml",
    "compose.home.yml",
    "compose.local-tls.yml",
    "compose.mac.yml",
)
BUCKET_PREFIXES = ("RESTIC_", "AWS_")


def _yaml(name):
    import yaml

    with open(DOCKER_DIR / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _env_dict(environment):
    if isinstance(environment, dict):
        return {key: "" if value is None else str(value) for key, value in environment.items()}
    pairs = {}
    for entry in environment or []:
        key, _, value = str(entry).partition("=")
        pairs[key] = value
    return pairs


def merged_mac_server_stack():
    """`{service: {"environment": {...}, "ports": [...]}}` over the
    up-mac-server files: later files override environment keys and add
    ports, as Compose merges them."""
    services = {}
    for name in MAC_SERVER_FILES:
        for service, spec in (_yaml(name).get("services") or {}).items():
            spec = spec or {}
            merged = services.setdefault(service, {"environment": {}, "ports": []})
            merged["environment"].update(_env_dict(spec.get("environment")))
            merged["ports"] += [str(port) for port in spec.get("ports") or []]
    return services


def _make_variable(makefile, name):
    return re.search(rf"^{name} := (.+)$", makefile, re.MULTILINE).group(1)


def test_the_make_target_layers_the_mac_server_files_in_order():
    makefile = (DOCKER_DIR.parents[1] / "Makefile").read_text(encoding="utf-8")
    assert "up-mac-server: secrets local-tls" in makefile
    assert "$(COMPOSE_MAC_SERVER) up -d --build" in makefile
    expanded = (
        _make_variable(makefile, "COMPOSE_MAC_SERVER")
        .replace("$(COMPOSE_HOME)", _make_variable(makefile, "COMPOSE_HOME"))
        .replace("$(COMPOSE_DAILY)", _make_variable(makefile, "COMPOSE_DAILY"))
        .replace("$(COMPOSE)", _make_variable(makefile, "COMPOSE"))
    )
    assert re.findall(r"-f deploy/docker/(\S+)", expanded) == list(MAC_SERVER_FILES)


def test_the_mac_override_declares_macos_and_publishes_nothing():
    services = _yaml("compose.mac.yml")["services"]
    assert set(services) == {"web", "worker"}
    for service, spec in services.items():
        assert spec["environment"] == {"HOME_SERVER_PLATFORM": "macos"}, service
        assert set(spec) == {"environment"}, service  # no ports, volumes, network_mode


def test_the_mac_server_stack_is_macos_on_web_and_loopback_only():
    services = merged_mac_server_stack()
    for service in ("web", "worker"):
        environment = services[service]["environment"]
        assert environment["HOME_SERVER_PLATFORM"] == "macos", service
        assert "HOME_BACKUP_OFFSITE_REPOSITORY" in environment, service
        assert "HOME_FDE_CONFIRMED_ON" in environment, service
    web = services["web"]["environment"]
    assert web["HOME_BACKUP_STATUS_PATH"] == "/run/friday-home/status.json"
    # The loopback TLS proxy for this Mac's own node is part of the stack.
    assert services["local-tls"]["ports"] == ["127.0.0.1:8443:8443"]
    for service, spec in services.items():
        for port in spec["ports"]:
            assert port.startswith("127.0.0.1:") and port.count(":") == 2, (service, port)
        for name in spec["environment"]:
            assert not name.startswith(BUCKET_PREFIXES), (service, name)


def test_the_home_stack_without_the_mac_override_stays_linux():
    environment = {}
    for name in MAC_SERVER_FILES[:3]:
        web = (_yaml(name).get("services") or {}).get("web") or {}
        environment.update(_env_dict(web.get("environment")))
    assert environment["HOME_SERVER_PLATFORM"] == "${HOME_SERVER_PLATFORM:-linux}"


def test_docker_compose_config_merges_the_mac_server_stack(tmp_path):
    """The real merge, when the Compose CLI is installed: dummy secrets, no
    deploy/docker/.env read (`--env-file` names an empty file), no daemon."""
    import json
    import shutil
    import subprocess

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("no docker CLI on this host")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "DOCKER_CONFIG": str(tmp_path / "docker-config"),
        "DJANGO_SECRET_KEY": "dummy",
        "POSTGRES_PASSWORD": "dummy",
        "REDIS_PASSWORD": "dummy",
        "FRIDAY_BFF_SHARED_SECRET": "dummy",
    }
    if (
        subprocess.run(  # noqa: S603 -- fixed argv
            [docker, "compose", "version"], env=env, capture_output=True, check=False
        ).returncode
        != 0
    ):
        pytest.skip("no docker compose plugin on this host")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    argv = [docker, "compose", "--env-file", str(empty)]
    for name in MAC_SERVER_FILES:
        argv += ["-f", str(DOCKER_DIR / name)]
    completed = subprocess.run(  # noqa: S603 -- fixed argv
        [*argv, "config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    services = json.loads(completed.stdout)["services"]
    assert services["web"]["environment"]["HOME_SERVER_PLATFORM"] == "macos"
    assert services["worker"]["environment"]["HOME_SERVER_PLATFORM"] == "macos"
    assert services["web"]["environment"]["HOME_BACKUP_OFFSITE_REPOSITORY"] in ("", None)
    for service, spec in services.items():
        for port in spec.get("ports") or []:
            assert port.get("host_ip") == "127.0.0.1", (service, port)
        for name in spec.get("environment") or {}:
            assert not name.startswith(BUCKET_PREFIXES), (service, name)
