"""A node home for the transcript pull over the real node layer (runbook
A12): the 8B owner-turn fixtures (`nodes/reference/fixtures/owner_turns/`)
laid out as a home's `~/.claude/projects` and `~/.codex/sessions`, with the
fixture owner's home (`/Users/owner`) rewritten to the new home and every
timestamp moved so that the newest one is `newest_at`.

The live gateway e2e needs it: the reference node's readers then run on the
wall clock (their 7-day bound and the server's future-turn rule both follow
real time), so the fixed fixture dates would age out of the window."""

import json
import os
from datetime import datetime
from pathlib import Path

from nodes.reference import owner_turns

FIXTURE_HOME = "/Users/owner"
TARGETS = {"claude": Path(".claude"), "codex": Path(".codex")}


def _shift(value, delta):
    at = owner_turns.parse_instant(value)
    return owner_turns.format_instant(at + delta) if at is not None else value


def _instants(lines):
    for line in lines:
        for value in (line.get("timestamp"), (line.get("payload") or {}).get("timestamp")):
            at = owner_turns.parse_instant(value) if isinstance(value, str) else None
            if at is not None:
                yield at


def _render(line, delta, home):
    text = json.dumps(line, ensure_ascii=False).replace(
        json.dumps(FIXTURE_HOME)[1:-1], json.dumps(home)[1:-1]
    )
    line = json.loads(text)
    if isinstance(line.get("timestamp"), str):
        line["timestamp"] = _shift(line["timestamp"], delta)
    payload = line.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("timestamp"), str):
        payload["timestamp"] = _shift(payload["timestamp"], delta)
    return json.dumps(line, ensure_ascii=False)


def owner_turn_home(base, *, newest_at: datetime) -> Path:
    """Create `base/home` (see the module docstring); returns its realpath."""
    home = Path(base) / "home"
    home.mkdir(parents=True, exist_ok=True)
    real_home = os.path.realpath(home)
    files = []
    for family, target in TARGETS.items():
        root = owner_turns.FIXTURES_DIR / family
        for path in sorted(root.rglob("*.jsonl")):
            files.append((target / path.relative_to(root), path.read_text(encoding="utf-8")))
    parsed = []
    for _relative, text in files:
        for raw in text.splitlines():
            try:
                parsed.append(json.loads(raw))
            except ValueError:
                continue
    delta = newest_at - max(_instants(line for line in parsed if isinstance(line, dict)))
    for relative, text in files:
        out = []
        for raw in text.splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                out.append(raw)  # an unparseable line stays as it is
                continue
            out.append(_render(line, delta, real_home) if isinstance(line, dict) else raw)
        target = Path(real_home) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
    return Path(real_home)
