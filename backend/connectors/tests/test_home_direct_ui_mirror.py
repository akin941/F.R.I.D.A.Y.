"""Phase 13b branch 1d (D7/D11): the frontend's copy of the closed reasons a
card shows (`frontend/src/lib/home-direct.ts`) names only real closed codes
and uses the backend's own Turkish text for each, so the card and the
fallback reply never disagree."""

import re
from pathlib import Path

from connectors import home_direct

MIRROR = Path(__file__).resolve().parents[3] / "frontend/src/lib/home-direct.ts"
ENTRY = re.compile(r'^\s*([a-z_]+): "([^"]*)",$', re.MULTILINE)

# The recommendation recorded in the 1c owner steps: the reasons that
# concern a flagged entity, plus `not_flagged` on an eligible player.
SHOWN = {
    home_direct.NOT_FLAGGED,
    home_direct.KILL_SWITCH_ENV,
    home_direct.KILL_SWITCH_OWNER,
    home_direct.RATE_LIMITED,
    home_direct.RATE_STORE_UNAVAILABLE,
    home_direct.VOLUME_UNKNOWN,
    home_direct.VOLUME_AT_MAX,
    home_direct.NOT_AVAILABLE,
    home_direct.PROVENANCE_MISMATCH,
    home_direct.TARGET_UNAVAILABLE,
}


def test_the_frontend_mirror_matches_the_closed_reason_texts():
    entries = dict(ENTRY.findall(MIRROR.read_text(encoding="utf-8")))
    assert set(entries) == SHOWN
    assert set(entries) <= home_direct.REASONS
    for code, text in entries.items():
        assert text == home_direct.REASON_TEXT[code], code
