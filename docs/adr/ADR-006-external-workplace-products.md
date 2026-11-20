# ADR-006: Workplace Products Are External Installations, Not Friday Module Packs

## Status
Accepted (2026-07-07)

## Context
The original plan had workplace systems arriving inside Friday as company
workspaces / module packs (a document-intelligence pack on roadmap MVP 11–15;
a company workspace is the named example in ADR-001's context and ADR-002's
module-pack rule). In practice, workplace products are better built and run
outside this repo: as separate applications with their own codebases,
release cycles and deployments, each customer or organization running its
own independent installation.

## Decision
Workplace products are out of Friday's scope as in-repo code. Friday's
identity is: personal AI operating system + a platform core that workplaces
can integrate with. Friday and any workplace product remain independent
codebases; designs proven in Friday transfer to external installations as
**patterns, never code** (`docs/DESIGN_TRANSFER.md`). Friday's contact with
an external workplace installation is an external-client relationship only:
an MCP client consuming the installation's MCP-compatible tools on a user's
behalf (read-only first, writes behind the approval gate) — never
in-process. Where several external installations exist, Friday can become
the operator's multi-installation management/assistant layer over them
(fleet phase, MASTER_PLAN §P — vision only).

## Consequences
The in-repo document-intelligence pack (MVP 11–15) is dropped; the MVP 11
slot is reused for Fleet/Organization Management (vision only), MVP 12–15 are
reserved. The company-workspace examples in ADR-001/ADR-002 become historical
— the decisions themselves stand: workspace axis, company workspaces as a
platform capability, and module packs as the extension mechanism all remain;
the first concrete module pack is now TBD. A Friday ↔ external-installation
API bridge and a shared pattern library are open decisions (MASTER_PLAN §O),
not commitments.
