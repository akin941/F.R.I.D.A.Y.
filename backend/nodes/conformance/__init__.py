"""The Friday Node conformance suite (ADR-013 release gate; Phase 9 brief
D5/D13, Phase 10 brief D5, D11, D12).

- `cases`: the case definitions in Python. `build_node_conformance` renders
  them to `cases/v1/*.json` and `cases/v2/*.json` (one file per case) plus
  `vectors/sandbox.json` and `vectors/scrub_stream.json`; `--check` fails
  when a committed file drifts from its definition.
- `runner`: plays a case against a node, in process or over a localhost
  `wsproto` socket (the hostile fake gateway), and checks the node's frames;
  the gateway side counts stray `output`/`confirmation` frames as frame
  errors.
- `escape`: the `srt` escape suite (D11): cases as data, the fake-sandbox
  static check, and the runner's verdict logic.
- `keys/conformance_keys.json`: the test-only key pair the fake gateway
  signs with; every production server refuses it.

The case and vector files are what the Swift test target loads (Mac
session M1/M2). Pure Python like the reference node: no Django.
"""
