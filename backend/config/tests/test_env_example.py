"""`.env.example` completeness (Phase 4 branch 1): every environment
variable the backend reads must be documented in the repo-root
`.env.example` (set, or as a commented-out default)."""

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = BACKEND_DIR.parent / ".env.example"

# env("NAME" / env.int("NAME" / env.bool(\n    "NAME" ... in settings.
_SETTINGS_READ = re.compile(r"\benv(?:\.\w+)?\(\s*\"([A-Z][A-Z0-9_]*)\"")
# os.environ.get("NAME" / os.environ["NAME"] / os.getenv("NAME" elsewhere.
_OS_READ = re.compile(r"os\.(?:environ\.get\(|environ\[|getenv\()\s*\"([A-Z][A-Z0-9_]*)\"")
_DOCUMENTED = re.compile(r"^#?\s?([A-Z][A-Z0-9_]*)=", re.MULTILINE)

# Not configuration: process plumbing set by Django itself / by git.
_NOT_CONFIG = {"DJANGO_SETTINGS_MODULE", "GIT_NO_REPLACE_OBJECTS"}


def _production_sources():
    for path in BACKEND_DIR.rglob("*.py"):
        parts = set(path.relative_to(BACKEND_DIR).parts)
        if parts & {".venv", "tests", "migrations"}:
            continue
        yield path


def _read_names():
    names = set()
    for path in (BACKEND_DIR / "config" / "settings").glob("*.py"):
        names |= set(_SETTINGS_READ.findall(path.read_text()))
    for path in _production_sources():
        names |= set(_OS_READ.findall(path.read_text()))
    return names - _NOT_CONFIG


def test_scanner_finds_settings_and_os_reads():
    # Guards the regexes themselves (multi-line env.int( calls included).
    names = _read_names()
    assert {"DJANGO_SECRET_KEY", "AGENT_APPROVAL_RESUME_RECOVERY_GRACE_SECONDS"} <= names
    assert {"JARVIS_OWNER_PASSWORD", "ANTHROPIC_MODEL_ID", "JARVIS_CLI_TOKEN"} <= names


def test_every_backend_env_var_is_documented_in_env_example():
    documented = set(_DOCUMENTED.findall(ENV_EXAMPLE.read_text()))
    missing = sorted(_read_names() - documented)
    assert not missing, f".env.example is missing: {missing}"


def test_env_example_has_no_stale_entries_for_removed_config():
    documented = set(_DOCUMENTED.findall(ENV_EXAMPLE.read_text()))
    for removed in ("GEMINI_API_KEY", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        assert removed not in documented
