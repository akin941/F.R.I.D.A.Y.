# ADR-017: Home Hub Topology — One Home Server, Many Nodes

## Status
Accepted 2026-11-26 — owner decision of 2026-11-26 (MASTER_PLAN §O,
decision d11), recorded with the Phase 13 brief
(`docs/prompts/phase-13-home-hub/brief.md`, branch `docs/p13-brief`).
Builds on ADR-001 (workspace axis), ADR-005 (Postgres is the source of
truth), ADR-010 (connectors, Home Assistant bridge), ADR-013 (Friday Node
protocol, unchanged), ADR-014 (node action classes, unchanged) and ADR-015
(voice-originated turns, unchanged). Amends nothing.

Numbering note: the Phase 13 task text named this ADR "ADR-016". ADR-016 is
already taken by Phase 12 ("Proactive Runs, Schedules and Instruction
Skills", `docs/adr/ADR-016-proactive-runs-and-skills.md`), so this ADR takes
the next free number. Every Phase 13 document cites it as ADR-017.

## Context
Until Phase 13 the plan never said where the Friday server runs. The
Docker Compose stack (`deploy/docker/compose.yml` plus the daily-driver
override `compose.daily.yml`) runs on "the Mac or the server host"
(`docs/runbooks/mac-session-handoff.md` A3), every port is published on
`127.0.0.1`, and the node runbook (`docs/runbooks/friday-node.md`) assumes a
TLS reverse proxy that "already fronts Friday". Phase 9 allowed more than
one node per owner but explicitly did not optimise for it (Phase 9 brief,
"Out of scope").

The owner uses Friday only at home, from more than one PC. If the server
lived on one Mac, the other PCs would lose Friday whenever that Mac sleeps,
travels or is rebuilt, and memory would follow the laptop instead of the
person. A hosted database (Supabase or similar) would split the stack:
Postgres in a third party's cloud, Redis, Celery, the gateway and the node
gateway at home, and the owner's personal memory outside the house.

## Decision
1. **One always-on Friday server on a home PC.** A Linux machine on the
   home LAN (a mini PC is enough) runs the whole Docker Compose stack:
   Django web, the Next.js frontend, Postgres with pgvector, Redis, the
   Celery worker with beat, the `node-gateway` and the connector sidecars.
   It is the only Friday server. Postgres on it is the source of truth
   (ADR-005); nothing else holds a primary copy.
2. **Every PC is a paired node of that one server.** Macs now
   (`friday-node`, ADR-013), Linux PCs from Phase 13 (a productionised
   Python node, read capabilities only), Windows later (deferred). Because
   every node talks to the same server, identity, memory, approvals, audit
   and budgets are the same on every PC. A node holds no Friday data of its
   own beyond its credential, its local policy and its bounded spool
   (ADR-013 decisions 2, 5, 7).
3. **Home only; LAN reach by default.** Friday is used only at home. The
   server is reachable on the home LAN only: the reverse proxy binds the
   server's LAN address, the host firewall admits the LAN subnet only, and
   nothing is port-forwarded or published to the internet. Tailscale is an
   optional overlay the owner may add (its CGNAT range is already in
   `DEVICE_PAIRING_NETWORKS`); it is never a public exposure. No public DNS
   name, no public certificate authority, no tunnel service.
4. **Node WSS stays TLS with a pinned certificate (ADR-013 unchanged).**
   The node gateway is served over TLS by the reverse proxy with a
   certificate issued by a private CA the owner creates for the house. The
   node pins the server certificate's key or the private CA (ADR-013
   decision 2); `NODE_TLS_PIN_SHA256` lets preflight catch a changed key.
   "It is only the LAN" is never a reason for plain `ws://`.
5. **Full-disk encryption on the home server.** The server's disk is
   encrypted (LUKS on Linux) so a stolen or discarded machine does not
   leak the owner's memory, transcripts-derived facts, audit trail or the
   `.env` secrets.
6. **Nightly encrypted off-site backup with a tested restore.** Every
   night a `pg_dump` (custom format) is encrypted with `age` to a public
   key whose private half is kept off the server, and the encrypted dumps
   are pushed with `restic` to an off-site repository (Cloudflare R2 or
   any S3-compatible or SFTP target the owner chooses; optional, a second
   local disk is acceptable until one is chosen). A restore drill (restore
   into a scratch Postgres, migrate check, audit-chain verification) is run
   and recorded at least monthly. Preflight warns when the last backup or
   the last drill is too old. Backups are host-level operations configured
   by the owner, not Friday actions: no agent, tool or model can start,
   change or read them.
7. **An explicit later migration path to a VPS, without changing the
   node protocol.** If the owner ever moves the server off the home PC,
   the same Compose stack is brought up on the VPS, the latest backup is
   restored, the same `NODE_SIGNING_KEY` and the same private CA (or the
   same TLS key) are carried over, and each node is re-pointed to the new
   address in its own local settings. The node protocol, the pairing
   records and the audit chain are unchanged; a node whose pin no longer
   matches is re-paired. Reach on a VPS is through the private overlay
   (Tailscale/WireGuard), never public. The checklist lives in
   `docs/runbooks/home-server-setup.md`.
8. **Many nodes are first-class, but an invocation targets exactly one
   node.** Nodes get owner-set names, rooms and roles; the server resolves
   "which PC" for every node call (explicit name, then the speaking node,
   then the last active one, otherwise it asks) and every read or action
   is signed for, sent to and audited against one node. There is no
   broadcast invocation and no fan-out write. ADR-014's classes apply per
   node unchanged: a gate-class action is still a web approval plus a click
   on that node's own window.

## Consequences
- The home PC must stay on. A server outage stops Friday on every PC at
  once; nodes fail closed (ADR-013 decision 6), and nothing is queued.
- The owner operates a small server: OS updates, Docker updates, disk
  encryption passphrase at boot (or a TPM/Clevis unlock they choose),
  backups and drills. The runbook keeps this to a checklist, and preflight
  reports backup freshness and drill age.
- Away from home Friday is unavailable by design, unless the owner adds
  Tailscale. Home Assistant, which already lives on the LAN (ADR-010,
  `docs/mcp/home-assistant.md`), is reachable from the server without any
  new exposure.
- A second PC no longer needs its own Friday: pairing it as a node gives it
  the same memory and the same approvals.
- Moving to a VPS later is a restore plus a re-point, not a redesign.

## Alternatives considered
- **Supabase (hosted Postgres).** Rejected. It would host only Postgres:
  Redis, Celery, the gateway and the node gateway would still run at home,
  so the stack would be split across two places with the latency and the
  failure modes of both, and the owner's personal memory would live with a
  third party. It adds an account, an external dependency and a data
  processor for no capability Friday needs.
- **Object storage as the database** (SQLite files or dumps on S3/R2 as
  the live store). Rejected. Friday needs transactions, row-level locks
  (`select_for_update` on the audit chain head), pgvector and recursive
  CTEs; object storage is a backup target only (decision 6).
- **The server on one of the Macs.** Rejected. The server would sleep,
  travel and be rebuilt with the laptop; the other PCs would depend on it;
  the TCC-granted node and the server would share one machine's blast
  radius. A Mac remains a node.
- **A public VPS from day one.** Not chosen now: Friday is used only at
  home, and a public server adds exposure for no benefit. It stays the
  documented later path (decision 7).
- **One server per PC, synchronised.** Rejected: two primary copies of
  memory and approvals contradict ADR-005 and the single audit chain.
