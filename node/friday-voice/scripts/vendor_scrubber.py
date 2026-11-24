"""Regenerate (or `--check`) `friday_voice/_scrubber_vendored.py` from
`backend/memory/ingestion/scrubber.py` (Phase 11 brief D16, the reference
node's `vendor_scrubber` pattern).

The voice child imports nothing from `backend/`, so it carries a vendored
copy of the ADR-012 ingestion scrubber for its stderr log: the source
module's text, byte for byte, under a fixed header. CI fails when they
differ. After any scrubber change run (from node/friday-voice/):

    uv run python scripts/vendor_scrubber.py

Reads and writes text only -- it never imports the backend.
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SOURCE = REPO_ROOT / "backend" / "memory" / "ingestion" / "scrubber.py"
TARGET = PACKAGE_ROOT / "friday_voice" / "_scrubber_vendored.py"

HEADER = (
    "# GENERATED FILE -- do not edit. Vendored copy of\n"
    "# backend/memory/ingestion/scrubber.py for the voice child (Phase 11 D16).\n"
    "# Regenerate: uv run python scripts/vendor_scrubber.py (in node/friday-voice)\n"
    "# CI checks it is identical to the source module (tests/test_log.py).\n"
)


def render():
    return HEADER + SOURCE.read_text(encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    expected = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != expected:
            print("friday_voice/_scrubber_vendored.py is stale; regenerate it.")
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
