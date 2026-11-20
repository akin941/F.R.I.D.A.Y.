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


# Phase 6 D4 / ADR-011: speech engines run out of process (speaches or the
# deploy/voice/ sidecar on the host) behind an OpenAI-compatible HTTP
# server, so no speech library is ever imported ANYWHERE in backend/ --
# ai_core included.
SPEECH_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(faster_whisper|ctranslate2|piper|onnxruntime|whisper)\b",
    re.MULTILINE,
)


def test_no_speech_engine_imports_anywhere_in_backend():
    violations = []
    for path in BACKEND_DIR.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if SPEECH_IMPORT_RE.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(BACKEND_DIR)))
    assert violations == [], (
        f"Speech engine imports in backend/: {violations} — speech engines run "
        "out of process behind the gateway (Phase 6 D4, ADR-011)."
    )


def test_speech_guard_pattern_matches_what_it_claims():
    for line in (
        "import faster_whisper",
        "from piper import PiperVoice",
        "import onnxruntime as ort",
        "from whisper import load_model",
        "  import ctranslate2",
    ):
        assert SPEECH_IMPORT_RE.search(line), line
    assert not SPEECH_IMPORT_RE.search("import whisperish")
    assert not SPEECH_IMPORT_RE.search("# whisper is out of process")
