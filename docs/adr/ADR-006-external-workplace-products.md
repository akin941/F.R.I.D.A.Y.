# ADR-006: external Is an External Product, Not a Jarvis Module Pack

## Status
Accepted (2026-07-07)

## Context
The original plan had external arriving inside Jarvis as the first company
workspace / module pack (module pack, roadmap MVP 11–15; external is the named
example in ADR-001's context and ADR-002's module-pack rule). Reality moved
faster outside this repo: the external installation is a separate Django
application, mature with real
data, and will run on the its own server via Docker. Long term the
owner plans to sell that platform to other firms, each customer running its
own installation.

## Decision
external is removed from Jarvis's scope entirely. Jarvis's identity is: personal
AI operating system + a platform core that workplaces can integrate with. The
two projects remain independent codebases; designs proven in Jarvis transfer
to external (and future installations) as **patterns, never code**
(`docs/DESIGN_TRANSFER.md`). Jarvis's future contact with a external installation
is an external-client relationship only: an MCP client consuming the
installation's MCP-compatible tools on a user's behalf (the mirror of
(installation side) — never in-process. In the multi-customer scenario
Jarvis becomes the operator's multi-company management/assistant layer over
external installations (fleet phase, MASTER_PLAN §P).

## Consequences
The external module pack (MVP 11–15) is dropped; the MVP 11 slot is reused for
Fleet/Organization Management (vision only), MVP 12–15 are reserved. The external
references in ADR-001/ADR-002 become historical examples — the decisions
themselves stand: workspace axis, company workspaces as a platform capability,
and module packs as the extension mechanism all remain; the first concrete
module pack is now TBD. A Jarvis ↔ external API bridge and a shared pattern
library are open decisions (MASTER_PLAN §O), not commitments.
