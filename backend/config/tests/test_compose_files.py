"""Committed Docker Compose files (Phase 4 branch 1): nothing published
outside loopback, no secret literal. Plain-text checks -- no YAML
dependency -- over every compose file in deploy/docker/."""

import re
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[3] / "deploy" / "docker"
COMPOSE_FILES = sorted(DOCKER_DIR.glob("compose*.yml"))

# Values that used to be committed (or are publicly known fixtures) and
# must never come back into a compose file.
FORBIDDEN_LITERALS = [
    "dev-insecure-key",
    "jarvis-dev-secret",
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
    assert _URL_WITH_LITERAL_PASSWORD.search("postgres://jarvis:jarvis@postgres:5432/jarvis")
    assert _URL_WITH_LITERAL_PASSWORD.search("redis://:hunter2@redis:6379/0")
    assert not _URL_WITH_LITERAL_PASSWORD.search("redis://:${REDIS_PASSWORD:?x}@redis:6379/0")
    assert not _URL_WITH_LITERAL_PASSWORD.search(
        '"postgres://jarvis:${POSTGRES_PASSWORD:?run `make secrets`}@postgres:5432/jarvis"'
    )


def test_env_example_next_to_compose_lists_the_required_secrets():
    text = (DOCKER_DIR / ".env.example").read_text()
    for key in ("DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert re.search(rf"^#? ?{key}=\s*$", text, re.MULTILINE), key
