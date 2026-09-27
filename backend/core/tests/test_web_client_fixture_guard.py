"""CI guard for the default-deny flip (Phase 4 branch 2, plan-review round 1
LOW): web-channel tests must authenticate through the one root-conftest
`web_client` fixture (a `WebSessionPrincipal`). A per-app helper built on a
bare `force_authenticate(user=...)` would silently turn its tests low-trust
(CHANNEL_UNKNOWN) -- or, worse, grow its own idea of what "web" means.

Plain-text scan, no imports of the scanned modules."""

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# The fixture itself, and the channel-matrix test that deliberately
# force-authenticates non-web principals to pin the default-deny rule.
_ALLOWED_FORCE_AUTHENTICATE = {
    Path("conftest.py"),
    Path("config/tests/test_channel_matrix.py"),
}
_OLD_HELPER_DEF = re.compile(r"^\s*def (auth_client|client_for)\s*\(", re.MULTILINE)
_OLD_HELPER_IMPORT = re.compile(r"\bimport\b[^\n]*\b(auth_client|client_for)\b")


def _python_sources():
    for path in BACKEND_DIR.rglob("*.py"):
        rel = path.relative_to(BACKEND_DIR)
        if rel.parts[0] == ".venv" or "migrations" in rel.parts:
            continue
        if rel == Path(__file__).resolve().relative_to(BACKEND_DIR):
            continue
        yield rel, path.read_text()


def test_no_module_force_authenticates_outside_the_shared_fixture():
    offenders = [
        str(rel)
        for rel, text in _python_sources()
        if "force_authenticate" in text and rel not in _ALLOWED_FORCE_AUTHENTICATE
    ]
    assert not offenders, (
        f"Use the root-conftest `web_client` fixture instead of force_authenticate: {offenders}"
    )


def test_no_module_defines_or_imports_the_old_per_app_helpers():
    offenders = [
        str(rel)
        for rel, text in _python_sources()
        if _OLD_HELPER_DEF.search(text) or _OLD_HELPER_IMPORT.search(text)
    ]
    assert not offenders, f"Old per-app web-client helper still present: {offenders}"


def test_guard_patterns_catch_the_old_shapes():
    assert _OLD_HELPER_DEF.search("def auth_client(user):\n")
    assert _OLD_HELPER_DEF.search("    def client_for(user):\n")
    assert _OLD_HELPER_IMPORT.search("from memory.tests.test_views import client_for")
    assert not _OLD_HELPER_DEF.search("def web_client(db):\n")
