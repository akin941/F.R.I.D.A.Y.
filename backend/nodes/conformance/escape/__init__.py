"""The `srt` escape suite (Phase 10 brief D11, ADR-014 decision 3).

`cases.json` is the suite as data; `runner` plays it. Each case is a tiny
script run as a mapped `test` command in `worktree` mode, with an
`attempt` describing what it tries (write outside the worktree, read a
denied path, reach the network or the node's hook socket, read a planted
token from the environment, exceed a limit).

Two checks per case:

- the **static check** (the fake sandbox): the generated sandbox policy
  (`nodes.reference.sandbox`) covers the attempt -- the write target is
  outside every writable root (or under a denied one), the read path is
  denied, the network has no allowed domain, no Unix socket and no local
  binding, the token is not in the environment, the limit is below what the
  case wants;
- the **run**: the script is executed by an executor (`fake`: a model of
  the policy; `srt`: the real sandbox, opt-in) and is contained when it
  never prints `FRIDAY-ESCAPED`, leaves no write target behind, cannot push
  output past 1 MiB or run past its wall time, and the worktree is gone with
  `git worktree list` unchanged afterwards.

In CI the suite runs against the fake sandbox. A Linux run under real `srt`
is local evidence only; the ADR-014 evidence is the Mac run (Seatbelt,
Mac session M3).
"""

import json
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parent / "cases.json"
MARKER = "FRIDAY-ESCAPED"


def load_cases():
    document = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    ids = [case["id"] for case in document["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate escape case id")
    return document["cases"]
