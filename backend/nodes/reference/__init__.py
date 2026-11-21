"""The Python reference node, protocol v1 and v2 (Phase 9 brief D13, Phase
10 brief D4, D5, D11, D12).

The executable spec of the node side of the Friday Node protocol: the Swift
app (`node/friday-node`, Mac session M1/M2) must behave exactly like it, and
the conformance and escape suites (`nodes.conformance`) are run against it
here, so the compromised-server tests exist before the Mac app does.

Pure Python: it imports `nodes.protocol` and `nodes.capabilities` and the
standard library (plus `wsproto` for the socket transport), never Django or
any other project package (an import-linter contract and a subprocess
purity test enforce it).

- `local_policy`: the node-local policy (repo and command mappings, enabled
  recipes, the bundle-id list). Nothing in the protocol reaches it.
- `verifier_v2`: D5 checks 1-22 (1-21 are `nodes.protocol.verify`; 22 is the
  local policy).
- `confirm`: the confirmation-window double (scripted owner, 60 s clock, one
  window at a time, closes on disconnect) and the fake clock.
- `sandbox`: the `srt` policy generator (D4), the spec the Swift executor
  must reproduce byte for byte.
- `executor`: the executor port, `FakeExecutor` and the opt-in Linux
  `SrtExecutor`; `worktree` the throwaway-worktree mode.
- `stream_scrub`: the node-side streaming scrub with carry-over (D12) and
  the output pipeline (8 KiB chunks, seq, 1 MiB cap, 4 KiB tail).
- `node`: `ReferenceNode`, the session state machine (D5 checks 23-24, fail
  closed on disconnect).
- `transport`: the `wsproto` client over a localhost socket.
"""
