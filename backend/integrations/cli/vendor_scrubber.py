"""Regenerate `_scrubber_vendored.py` from `memory/ingestion/scrubber.py`
(Phase 8A brief D6).

`friday-hook` runs on the owner's Mac outside the Django project, so it
carries a vendored copy of the ingestion scrubber: the same rules applied
before an event ever leaves the machine. The copy is the source module's
text, byte for byte, under a fixed header; CI
(`integrations/tests/test_friday_hook.py`) fails when they differ. After any
scrubber change run:

    uv run python -m integrations.cli.vendor_scrubber

Reads and writes text only -- it never imports `memory` (integrations'
contracts stay as they are).
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SOURCE = BACKEND_DIR / "memory" / "ingestion" / "scrubber.py"
TARGET = Path(__file__).resolve().with_name("_scrubber_vendored.py")

HEADER = (
    "# GENERATED FILE -- do not edit. Vendored copy of\n"
    "# backend/memory/ingestion/scrubber.py for friday-hook (Phase 8A D6).\n"
    "# Regenerate: uv run python -m integrations.cli.vendor_scrubber\n"
    "# CI checks it is identical to the source module (test_friday_hook.py).\n"
)


def render():
    return HEADER + SOURCE.read_text(encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    expected = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != expected:
            print("integrations/cli/_scrubber_vendored.py is stale; regenerate it.")
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
