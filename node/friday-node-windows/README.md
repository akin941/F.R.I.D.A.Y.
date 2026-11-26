# friday-node-windows — deferred (stub, Phase 13)

There is **no Windows node** in Phase 13 and no code in this directory
(`docs/prompts/phase-13-home-hub/brief.md` D10, branch table row W).

## Why it is deferred

- Roadmap decision d1 (`docs/research/2026-11-roadmap-proposal.md` §B1):
  the node shell is native per platform, and **Tauri v2 only if a Windows
  node is ever needed**. Nobody in the home needs one yet: the owner's PCs
  are Macs (the Swift `friday-node`) and Linux (`node/friday-node-linux`).
- A Windows node needs its own decisions that no current brief makes: where
  the credential lives (DPAPI / Credential Manager), the stand-in for Touch
  ID for local policy changes (Windows Hello), the hook relay transport (a
  named pipe with a peer-SID check instead of a Unix socket with
  `SO_PEERCRED`), and the service model.

## The contract it must pass

Whatever it is built with, a Windows node is a Friday Node like every
other (ADR-013, unchanged by Phase 13):

1. the protocol `friday-node.v1` (and `v2` only with its own ADR-014
   sandbox evidence for actions); the schemas in
   `backend/nodes/protocol/schemas/` and the contracts in
   `backend/nodes/capabilities/`;
2. **the same conformance suite**: every case of `manage.py
   node_conformance_server --external --tls-self-signed --protocol 1`
   (and `--protocol 2` for an action-capable node) through the external
   hook (`backend/nodes/conformance/gateway.py`), including the
   compromised-server family (unknown capability, out-of-schema args,
   replay, bad signature, policy-mutation frames);
3. pairing with `platform: "windows"` (`POST /api/v1/nodes/pair`, branch 1),
   TLS pinned to the home server's private CA or SPKI, the server signing
   key pinned at pairing;
4. local policy changeable only locally, by the owner, never by a protocol
   frame; fail closed when the server is gone.

The Linux node (`node/friday-node-linux`) is the closest template: the
reference node vendored with a lock, platform code only around it.

## Before any code

It needs **its own brief** (and an independent plan review) that decides
the shell (Tauri v2 per d1, or the Python package as on Linux), the items
above and the CI job that runs the conformance suite on a Windows runner.
