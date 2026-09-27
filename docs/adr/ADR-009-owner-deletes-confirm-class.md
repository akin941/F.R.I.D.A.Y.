# ADR-009: Owner Deletes Are Reversible (Confirm-Class); Permanent Purge Is Gate-Class

## Status
Accepted (2026-09-27, Phase 4 branch 2 — `docs/prompts/phase-4-identity-ui/brief.md`,
"Open decision resolved", revised after plan-review round 1)

## Context
ADR-008 decision 3 made the write-class taxonomy binding: **gate-class** —
anything irreversible or outward-facing ("delete, deploy, external send, git
push/merge, filesystem export, every harness write") always needs an
`ApprovalRequest`; **confirm-class** — "reversible, self-scoped, in-app rows"
— needs an explicit human confirm but no `ApprovalRequest`. Moving an action
between classes requires a new ADR; the list does not grow by precedent.

ADR-008's 2026-09-27 note and MASTER_PLAN §O flagged the gap: the owner
deleted rows directly over the web channel with no `ApprovalRequest`, while
"delete" is listed as gate-class. The code at that time:

- `memory.services.delete_memory` hard-deletes **only never-approved
  suggestions**; approved memories are archived ("forget") and restorable.
- `knowledge.services.delete_document` and `tasks.services.delete_task`
  hard-deleted.

A first draft reclassified the two real hard deletes as confirm-class by
analogy to the memory case. Plan review rejected that (CRITICAL): it dropped
the "reversible" criterion — growth by precedent.

## Decision
ADR-008's confirm-class definition stays exactly as written. Instead of
reclassifying irreversible deletes, the owner deletes are made **reversible**,
so they meet the existing definition:

1. **Memory.** `delete_memory` already only hard-deletes never-approved
   suggestions (nothing approved is lost; approved memories are archived and
   restorable). Confirm-class as is.
2. **Knowledge documents and tasks get soft-delete.** `deleted_at` on
   `KnowledgeDocument` and `Task`; their default managers (and therefore every
   `for_actor()` consumer) exclude trashed rows; an unfiltered `all_objects`
   manager serves the trash view, restore and purge. A trashed document's
   chunks and embeddings stay but are excluded from retrieval
   (`KnowledgeChunkQuerySet.cloud_eligible`). Deleting is a reversible move to
   the trash ("Çöp kutusu"); `restore` brings the row back. Confirm-class
   under ADR-008 as written.
3. **Permanent purge** of trashed rows happens only
   (a) automatically, `TRASH_RETENTION_DAYS` (default 30) after deletion — the
   retention window is part of the owner's original, confirmed delete
   decision and is stated in the confirmation dialog — via a daily beat task
   per app; or
   (b) explicitly, by "Kalıcı olarak sil" in the trash view, which is
   **gate-class**: an `ApprovalRequest` for the `purge_trash` tool, decided
   separately by the owner over the web channel. `purge_trash` is human-only
   (never proposed by the chat router or the agent planner, refused for any
   machine origin); rows restored before the decision survive it.
4. **Conditions, enforced in code:** a human actor (`not actor.is_agent`) over
   `CHANNEL_WEB` — the delete/restore services take a required `channel`
   argument, never defaulted, and the views are guarded by
   `core.permissions.IsWebChannel`; an explicit UI confirmation naming the
   object (AlertDialog, Phase 4 branch 5); audited (`task.deleted`,
   `task.restored`, `task.purged`, `knowledge.deleted`, `knowledge.restored`,
   `knowledge.purged`, `memory.deleted`); one workspace's in-app rows only.

Everything else that deletes stays gate-class: machine-initiated deletes,
anything touching the vault/filesystem, git, an external system, or a whole
workspace (firm deletion/archival).

ADR-008's "no growth by precedent" holds: this ADR adds no new confirm-class
*kind* of action; it makes these deletes meet the existing definition.

## Consequences
- The owner keeps one-click deletes (with a confirmation) and gains undo for
  30 days; storage for trashed rows is bounded by the retention purge.
- Re-uploading content whose earlier copy is in the trash is allowed (the
  per-workspace content-hash uniqueness ignores trashed rows); restoring that
  copy then refuses the duplicate.
- Code that must see trashed rows has to say so (`all_objects`): the
  ingestion worker does, so a document trashed mid-ingestion finishes and a
  restore needs no re-ingestion.
- One deliberate asymmetry: trashing a task releases its link to the chat
  draft it was confirmed from (`Task.source_message`, Phase 4 branch 4), so
  the draft reads as unconfirmed and can be re-confirmed. A restored task
  keeps its content, status and `source` provenance but not that back-link
  -- the link is a plain unique column, and releasing it at delete time is
  what keeps draft confirmation idempotent across delete/restore/re-confirm.
- The trash UI (list, restore, "Kalıcı olarak sil") lands in Phase 4 branch 5;
  the API (`GET /tasks/trash`, `GET /knowledge/documents/trash`,
  `POST …/restore`, `purge_trash` requests) lands with this ADR.
