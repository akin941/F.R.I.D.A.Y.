"""Static guard: provider SDK imports may only live under ai_core/ (ADR-003).

If this test fails, someone imported `anthropic` or `openai` outside the
gateway — that is a review-blocking architecture violation.
"""

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", "staticfiles", "media", "__pycache__", ".pytest_cache", ".ruff_cache"}
IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(anthropic|openai)\b", re.MULTILINE)


def test_provider_sdk_imports_only_in_ai_core():
    violations = []
    for path in BACKEND_DIR.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(BACKEND_DIR)
        if relative.parts[0] == "ai_core":
            continue
        if IMPORT_RE.search(path.read_text(encoding="utf-8")):
            violations.append(str(relative))
    assert violations == [], (
        f"Provider SDK imports outside ai_core/: {violations} — "
        "all LLM traffic must go through the gateway (ADR-003)."
    )
