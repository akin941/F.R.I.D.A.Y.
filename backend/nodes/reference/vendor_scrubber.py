"""Regenerate (or `--check`) `_scrubber_vendored.py` from
`memory/ingestion/scrubber.py` (Phase 9 brief D13 item 6, Phase 10 brief
D12).

The reference node runs outside the Django project and may import no
project package (`nodes` may not import `memory`, ADR-013), so it carries a
vendored copy of the ingestion scrubber: the same rules the server applies,
applied on the node before a byte of output leaves it. The copy is the
source module's text, byte for byte, under a fixed header; a test fails when
they differ. After any scrubber change run:

    uv run python -m nodes.reference.vendor_scrubber

Reads and writes text only -- it never imports `memory`.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SOURCE = BACKEND_DIR / "memory" / "ingestion" / "scrubber.py"
TARGET = Path(__file__).resolve().with_name("_scrubber_vendored.py")

HEADER = (
    "# GENERATED FILE -- do not edit. Vendored copy of\n"
    "# backend/memory/ingestion/scrubber.py for the reference node (Phase 10 D12).\n"
    "# Regenerate: uv run python -m nodes.reference.vendor_scrubber\n"
    "# CI checks it is identical to the source module (test_reference_scrub.py).\n"
)


def render():
    return HEADER + SOURCE.read_text(encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    expected = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != expected:
            print("nodes/reference/_scrubber_vendored.py is stale; regenerate it.")
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
