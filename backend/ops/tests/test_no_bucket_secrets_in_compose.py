"""Phase 13b branch 2 (brief D2, ADR-017 decision 6 and amendment 2): the
off-site bucket's credentials never enter a container. No compose file in
the repository passes a `RESTIC_*` or `AWS_*` variable to any service (in
`environment`, `env_file` or a raw interpolation), and no service reads an
env file that holds them. The names live only in the host's restic env
file (`deploy/home/restic.env.example`) and, as names with empty values
under a host-only heading, in `.env.example`. Parsed YAML, no Docker."""

import re
from pathlib import Path

import pytest
import yaml

from ops.tests.test_compose_no_public_ports import compose_files

REPO = Path(__file__).resolve().parents[3]
BUCKET_PREFIXES = ("RESTIC_", "AWS_")
R2_NAMES = (
    "RESTIC_REPOSITORY",
    "RESTIC_PASSWORD_FILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
)
_BUCKET_NAME = re.compile(r"\b(?:RESTIC|AWS)_[A-Z0-9_]+")

if not (REPO / "deploy" / "docker").is_dir():  # a backend-only image, not a checkout
    pytest.skip("no repository checkout around the backend", allow_module_level=True)


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _environment_names(environment):
    if isinstance(environment, dict):
        return list(environment)
    return [str(entry).split("=", 1)[0] for entry in environment or []]


def bucket_problems(document):
    """`service: name` for every bucket variable one parsed compose document
    hands to a service."""
    problems = []
    for service, spec in (document.get("services") or {}).items():
        spec = spec or {}
        for name in _environment_names(spec.get("environment")):
            if name.startswith(BUCKET_PREFIXES):
                problems.append(f"{service}: {name}")
        env_files = spec.get("env_file") or []
        if isinstance(env_files, str | dict):
            env_files = [env_files]
        for entry in env_files:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if "restic" in str(path).lower():
                problems.append(f"{service}: env_file {path}")
    return problems


@pytest.mark.parametrize("path", compose_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_compose_service_gets_a_bucket_variable(path):
    assert bucket_problems(_load(path)) == []


@pytest.mark.parametrize("path", compose_files(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_compose_file_names_a_bucket_variable_outside_comments(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0]
        assert not _BUCKET_NAME.search(code), f"{path.name}: {line.strip()}"


@pytest.mark.parametrize(
    "service",
    [
        {"environment": {"AWS_SECRET_ACCESS_KEY": "${AWS_SECRET_ACCESS_KEY}"}},
        {"environment": ["RESTIC_PASSWORD=x"]},
        {"environment": ["AWS_ACCESS_KEY_ID"]},
        {"env_file": "/etc/friday-backup/restic.env"},
        {"env_file": [{"path": "~/.config/friday-backup/restic.env", "required": False}]},
    ],
)
def test_the_guard_refuses_a_bucket_variable(service):
    assert bucket_problems({"services": {"web": service}}) != []


def test_the_guard_accepts_the_non_secret_repository_identifier():
    document = {
        "services": {
            "web": {"environment": {"HOME_BACKUP_OFFSITE_REPOSITORY": "${X:-}"}},
            "worker": {"environment": ["HOME_SERVER_PLATFORM=macos"]},
        }
    }
    assert bucket_problems(document) == []


def test_the_compose_env_template_holds_no_bucket_variable():
    """deploy/docker/.env feeds compose interpolation: its template never
    suggests a bucket variable, even commented out."""
    text = (REPO / "deploy" / "docker" / ".env.example").read_text(encoding="utf-8")
    for name in R2_NAMES:
        assert not re.search(rf"^#?\s*{name}=", text, re.MULTILINE), name


def test_env_example_lists_the_r2_names_empty_under_a_host_only_heading():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    heading = text.index("host-only -- never set in deploy/docker/.env")
    for name in R2_NAMES:
        match = re.search(rf"^# {name}=(.*)$", text, re.MULTILINE)
        assert match, name
        assert match.group(1) == "", name
        assert match.start() > heading, name
        # Never an uncommented (set) value in the template.
        assert not re.search(rf"^{name}=", text, re.MULTILINE), name


def test_the_host_template_lists_the_r2_names_only():
    lines = (REPO / "deploy" / "home" / "restic.env.example").read_text(encoding="utf-8")
    settings = [
        line for line in lines.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    assert settings == [f"{name}=" for name in R2_NAMES]
