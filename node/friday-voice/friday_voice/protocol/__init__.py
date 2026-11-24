"""`friday-voice/v1`: the parent (Swift host) <-> child (this package)
protocol over stdio (Phase 11 brief D6).

- `framing`: the length-prefixed frame (header line + optional PCM payload);
- `messages`: the two frozen message sets, their JSON schemas and the
  refusal codes.

Both are pure and dependency-light (stdlib + jsonschema) so the reference
host, the conformance runner and the Swift host's test target all read the
same files: `schemas/v1/` and `vectors/frames.json`.
"""
