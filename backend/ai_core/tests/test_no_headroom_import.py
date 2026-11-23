"""Headroom stays out of the backend (Phase 8B branch 6, brief D11): it runs
only in the `compressor` sidecar. No `import headroom` anywhere under
`backend/`, and `litellm` (a second-door LLM library, ADR-003) never in
`backend/uv.lock` -- the Piper/speech-engine guard pattern."""

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", "node_modules", "__pycache__"}
HEADROOM_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(headroom|litellm)\b", re.MULTILINE)
_LOCK_PACKAGE_RE = re.compile(r'^name = "([^"]+)"$', re.MULTILINE)


def test_no_headroom_or_litellm_import_in_backend():
    violations = []
    for path in BACKEND_DIR.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if HEADROOM_IMPORT_RE.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(BACKEND_DIR)))
    assert violations == [], (
        f"headroom/litellm imports in backend/: {violations} -- Headroom runs only in "
        "the compressor sidecar (deploy/compressor/, brief D11)."
    )


def test_the_guard_pattern_matches_what_it_claims():
    for line in ("import headroom", "from headroom import compress", "  import litellm"):
        assert HEADROOM_IMPORT_RE.search(line), line
    assert not HEADROOM_IMPORT_RE.search("import headroomish")
    assert not HEADROOM_IMPORT_RE.search("# headroom runs in its sidecar")


def test_litellm_and_headroom_are_not_in_the_backend_lock():
    packages = set(_LOCK_PACKAGE_RE.findall((BACKEND_DIR / "uv.lock").read_text()))
    assert packages, "the lock scan found no package at all"
    assert "django" in packages
    assert not {"litellm", "headroom-ai"} & packages
