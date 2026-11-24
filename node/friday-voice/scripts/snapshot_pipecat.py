"""Write (or `--check`) `friday_voice/pipecat_surface.json`: the installed
Pipecat's version, the signatures of every name in
`friday_voice.pipecat_adapter.SURFACE` and the sha256 of the bundled
models (Phase 11 brief D7).

    uv run python scripts/snapshot_pipecat.py          # after a deliberate upgrade
    uv run python scripts/snapshot_pipecat.py --check  # CI: fail on any difference
"""

import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from friday_voice import pipecat_adapter  # noqa: E402

TARGET = PACKAGE_ROOT / "friday_voice" / "pipecat_surface.json"


def render() -> str:
    return json.dumps(pipecat_adapter.snapshot(), indent=2, sort_keys=True) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    expected = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != expected:
            print(
                "friday_voice/pipecat_surface.json differs from the installed Pipecat; "
                "upgrades go in a dedicated chore/p11-pipecat-<version> branch."
            )
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
