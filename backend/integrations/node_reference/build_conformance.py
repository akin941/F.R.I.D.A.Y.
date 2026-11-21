"""Regenerate (or `--check`) the owner-turn conformance files (Phase 8B
branch 8, brief D13/D17):

- `fixtures/expected_owner_turns.json` -- the reference readers' output over
  the committed fixtures; the Swift readers (Mac session M1) must produce
  the same bytes for the same files;
- `conformance/owner_turns_cases.json` -- the argument, grant and
  result-frame cases every node must pass, each with its expected status.

    uv run python -m integrations.node_reference.build_conformance [--check]

Staged here until Phase 9's `build_node_conformance` and
`nodes/conformance/cases/v1/` exist (branch 3); the files then move there.
Standard library only.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from integrations.node_reference.owner_turns import (
    CAPABILITIES,
    FIXTURES_DIR,
    read_owner_turns,
)

HERE = Path(__file__).resolve().parent
EXPECTED_PATH = FIXTURES_DIR / "expected_owner_turns.json"
CASES_PATH = HERE / "conformance" / "owner_turns_cases.json"

# Every case runs at this instant over the committed fixtures.
NOW = "2026-11-21T00:00:00.000Z"
SINCE = "2026-11-20T00:00:00.000Z"
PREFIXES = ["/Users/owner/code", "/Users/owner/work/acme"]
# No result frame may ever carry these (assistant text, tool calls and
# results, meta/sidechain/compact/summary lines, markers, unknown kinds,
# rows outside the prefixes).
FORBIDDEN_PHRASES = [
    "ASSISTANT-ONLY-PHRASE",
    "TOOL-RESULT-PHRASE",
    "TOOL-MIXED-PHRASE",
    "META-PHRASE",
    "MARKER-PHRASE",
    "SIDECHAIN-PHRASE",
    "COMPACT-PHRASE",
    "SUMMARY-PHRASE",
    "IMAGE-PHRASE",
    "UNKNOWN-PHRASE",
    "EVENT-MSG-PHRASE",
    "DEVELOPER-PHRASE",
    "SCRATCH-PHRASE",
    "hunter2",
    "abcDEF123456789xyzTOKENvalue0001",
    "Zx81Kd02LmQpRt5UvWy7Ab9Cd3Ef4Gh",
]


def _args(**overrides):
    args = {"since": SINCE, "cwd_prefixes": PREFIXES, "max_sessions": 50, "max_turns": 200}
    args.update(overrides)
    return {k: v for k, v in args.items() if v is not ...}


def _refused(case_id, args, code="args_contract", granted=True):
    return {"id": case_id, "granted": granted, "args": args, "expect": "refused", "code": code}


def cases():
    out = []
    for capability in CAPABILITIES:
        tool = capability.split(".", 1)[0]
        rows = [
            _refused("not_granted", _args(), code="not_granted", granted=False),
            _refused("cwd_prefixes_missing", _args(cwd_prefixes=...)),
            _refused("cwd_prefixes_empty", _args(cwd_prefixes=[])),
            _refused("cwd_prefixes_too_many", _args(cwd_prefixes=[f"/p/{i}" for i in range(21)])),
            _refused("cwd_prefix_relative", _args(cwd_prefixes=["code/friday"])),
            _refused("cwd_prefix_duplicate", _args(cwd_prefixes=["/a", "/a"])),
            _refused("since_older_than_7_days", _args(since="2026-11-13T23:59:59.000Z")),
            _refused("since_malformed", _args(since="yesterday")),
            _refused("since_missing", _args(since=...)),
            _refused("max_turns_oversize", _args(max_turns=201)),
            _refused("max_turns_zero", _args(max_turns=0)),
            _refused("max_turns_bool", _args(max_turns=True)),
            _refused("max_sessions_oversize", _args(max_sessions=51)),
            _refused("unknown_arg", {**_args(), "include_assistant": True}),
            {
                "id": "since_exactly_7_days",
                "granted": True,
                "args": _args(since="2026-11-14T00:00:00.000Z"),
                "expect": "ok",
            },
            {
                "id": "owner_turns_only",
                "granted": True,
                "args": _args(),
                "expect": "ok",
                "forbidden_phrases": FORBIDDEN_PHRASES,
            },
            {
                "id": "max_turns_truncates",
                "granted": True,
                "args": _args(max_turns=1),
                "expect": "ok",
                "truncated": True,
                "turns": 1,
            },
            {
                "id": "max_sessions_truncates",
                "granted": True,
                "args": _args(max_sessions=1),
                "expect": "ok",
                "truncated": True,
                "sessions": 1,
            },
        ]
        for row in rows:
            row["id"] = f"{tool}.{row['id']}"
            row["capability"] = capability
            out.append(row)
    return {"now": NOW, "cases": out}


def _now():
    return datetime.fromisoformat(NOW.replace("Z", "+00:00")).astimezone(UTC)


def expected():
    return {
        "now": NOW,
        "args": _args(),
        "results": {
            capability: read_owner_turns(
                capability, _args(), root=FIXTURES_DIR, granted=True, now=_now()
            )["output"]
            for capability in CAPABILITIES
        },
    }


def _render(value):
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def outputs():
    return {EXPECTED_PATH: _render(expected()), CASES_PATH: _render(cases())}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if stale:
        print(f"stale owner-turn conformance files: {', '.join(stale)}; regenerate them.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
