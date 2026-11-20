# `external_installation` connector — External installation (blocked placeholder)

## Summary
- **Manifest:** `backend/connectors/manifests/external_installation.py`, version 1
- **Kind:** `mcp` (planned)
- **Status:** **blocked** — no binding can be created and no tool is
  registered (brief D14; tested in `connectors/tests/test_manifests.py` and
  `test_bindings_and_pins.py`)
- **Data class:** `firm`
- **Workspace types:** company only
- **Read channels:** web, cli (once unblocked; reads only)

## Why blocked (O-5)
An external installation is an independent external product (ADR-006).
Jarvis's only future relationship with it is as an MCP client consuming an
installation's MCP-compatible tools on a user's behalf.
The installation does not expose MCP yet, so there is nothing to pin.

## Open questions (decided by the brief that unblocks it)
- **Auth model:** per-user token issued by the installation, OAuth, or a
  service credential per installation; where it lives (sidecar env only,
  like every connector credential).
- **Approval treatment:** reads only at first (`firm`, ungated, audited,
  fenced). Any installation write would be gate-class (ADR-008 decision 3) with a
  snapshot-backed decision dialog — never confirm-class.
- **Scope:** which company workspace maps to which external installation, and
  the argument scope check that keeps firm A from reading firm B.
- **Fleet (§P):** monitoring several installations is out of scope.

## Tools
None. The unblocking commit adds them with schema pins, serializers and
scope checks, and moves `status` to `active`.

## Credentials
None yet. Env var reserved for the URL: `EXTERNAL_INSTALLATION_MCP_URL`.

## Review
Placeholder recorded 2026-09-27 (Phase 5 branch 4); full review due with the
unblocking brief.
