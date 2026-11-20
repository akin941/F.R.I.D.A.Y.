# MVP 4 — Tasks: Manual To-Dos + Today Integration

> Status: **done (2026-07-08)** — all 3 branches merged to main (scope approved
> by the user 2026-07-08; plan reviewed by Codex CLI, 2 rounds → APPROVE
> (round 1: CHANGES REQUESTED —
> missing actor-scoped row locking + concurrency tests, undefined Today
> timezone source, a nonexistent `AuditMixin` reference corrected to the
> actual `record_audit()` convention, no explicit no-title/description-in-
> audit-metadata rule, and a loose deferred-extraction note tightened to
> require a future backfill/cloud-eligibility policy; round 2 APPROVE).
> Roadmap slot: personal kernel step 4 (shell/chat → memory → knowledge/RAG →
> **tasks** → tools/approvals). Binding constraints: ADR-001 (workspace axis),
> ADR-002 (modular monolith — `tasks` is a reserved kernel app name),
> CLAUDE.md rules 1–9. Scope decision (2026-07-08): AI-suggested task
> extraction from chat (mirroring memory's extraction pipeline) is
> **explicitly deferred**, not included in this MVP — see Out of scope and
> the MASTER_PLAN Open Decisions entry this brief adds. This MVP is manual
> CRUD + a real Today page only, kept deliberately small (3 branches, not 5-6)
> because there is no proven design to reuse yet the way MVP 3 reused MVP 2's
> extraction pattern.

## Goal and user value

Friday gets a durable, workspace-scoped to-do list: users create tasks with an
optional due date and priority, mark them done/cancelled/reopened, and see
what's due today on a real Today page (currently a skeleton placeholder).
Nothing here is AI-written or AI-injected — every row is user intent, plainly
CRUD, the same trust-before-intelligence posture memory and knowledge shipped
with, just without an extraction pipeline this time.

## Scope

### 1. `tasks` kernel app — model + API

- `Task` (extends `WorkspaceScopedModel`): `title` (required), `description` (optional,
  blank-allowed text), `status` (`open` / `done` / `cancelled`), `due_date`
  (nullable `DateField` — a calendar day, not a datetime; Today's boundary is
  a date comparison, not a timezone-sensitive instant), `priority` (`low` /
  `normal` / `high`, default `normal`), `created_by` (provenance — also the
  sole predicate for the "own tasks" role check below).
- **No `source`/`sensitivity` field.** Every task in this MVP is user-created
  and nothing reads a per-row sensitivity classification (tasks are never
  serialized into a gateway payload — see Out of scope), so both fields would
  be dead weight until a feature actually needs them; add them in the MVP
  that does (extraction, or context injection), not speculatively now.
- **State machine** (enforced in services, illegal transitions → 400,
  repeating a transition the row is already in is an idempotent no-op,
  mirroring memory's convention): `open → done` (complete), `open →
  cancelled` (cancel), `done → open` (reopen), `cancelled → open` (reopen).
  Field edits (title/description/due_date/priority) are a separate action
  from a transition, same split memory uses for edit vs. approve/reject.
  **Every mutating service (transitions and field edits alike) re-fetches
  its row under lock, actor-scoped, inside the caller's transaction** —
  exactly memory's `_locked()` pattern (`Task.objects.filter(pk=task.pk,
  workspace__members__user=actor).select_for_update(of=("self",))`, not
  `for_actor()` directly since its `DISTINCT` is incompatible with `FOR
  UPDATE`), and authorizes/transitions against the row that query returns,
  never against a caller-supplied instance. This closes the same race class
  MVP 2's round-2 review caught: concurrent `complete`+`cancel`, or a
  transition racing a delete/edit, must serialize rather than both
  authorizing against a stale `open` read.
- **Role policy matrix** (applies identically to personal and company
  workspaces, same "no personal-workspace special case" rule as memory's
  matrix). "Own tasks" is the exact predicate `created_by = actor` (there is
  no `source` field to further split on, unlike memory's `source=user_created
  AND created_by=actor` — every row here is user-created by construction):

  | action | owner/admin | member | viewer |
  |---|---|---|---|
  | read/list | ✓ | ✓ | ✓ |
  | create | ✓ | ✓ | – |
  | edit fields | ✓ | own tasks | – |
  | complete/cancel/reopen | ✓ | own tasks | – |
  | delete | ✓ | own tasks | – |

- **API** (`/api/v1/tasks/tasks`, mirrors the knowledge app's endpoint shape):
  `GET` (list; `?workspace=` required, `?status=`, `?due_before=`,
  `?due_after=` optional filters — the Today page is just this list with a
  computed date range, not a bespoke endpoint), `POST` (create), `GET`/`PATCH`
  (field edits only — never `status`)/`DELETE /<uuid:task_id>`, plus three
  dedicated transition actions `POST /<uuid:task_id>/complete`,
  `/cancel`, `/reopen` (kept separate from `PATCH`, same reasoning as
  memory's approve/reject/restore: a transition is a distinct, audited
  action, not a generic field write).
- **Audit**: mutation events only (`task.created/updated/completed/
  cancelled/reopened/deleted`) via `audit.services.record_audit()` at each
  mutation site — there is no Django audit mixin in this codebase (per
  MVP 3's finding: `core.models.TimestampedModel`/`core.db.
  WorkspaceScopedModel` are the only base classes and neither audits),
  so `tasks` follows the same `record_audit()`-per-service-call convention
  memory and knowledge already use, not a mixin. **Audit metadata carries
  ids, the action name, status/transition values, and which fields changed
  on an edit — never `title` or `description` text**, the same
  no-content-in-metadata rule memory and knowledge both enforce (task
  content isn't marked `private`, but it can still be personal or
  company-sensitive free text, and the audit log is bookkeeping, not a
  content mirror); `record_audit()` already rejects secret-shaped metadata
  keys (`audit/tests/test_audit.py`), and this brief adds the same
  no-title/description discipline as an explicit, tested requirement rather
  than an implicit assumption. **No `task.read` batched-read audit event**
  — that pattern in memory exists specifically because ADR-005 requires
  auditing every access to potentially-`private` content; tasks carry no
  sensitivity classification and aren't gated content, so read-auditing
  every list/detail call would be audit-log noise with no corresponding
  requirement to satisfy.
- Server-controlled fields: `status` and `created_by` are never accepted by
  create/edit serializers (status only moves via the transition endpoints);
  `workspace` is resolved through `for_actor()` before any service runs,
  never trusted raw.

### 2. Frontend Tasks UI + Today integration

- `/tasks` page in the shell: list (status filter: open/done/cancelled),
  create form (title, description, due date, priority), inline
  complete/cancel/reopen actions, delete, edit. BFF routes follow the
  established pattern (HttpOnly cookies via `getAccessToken()`, CSRF via
  `rejectCrossOrigin`/`rejectNonJson`, UUID-validated dynamic segments before
  touching cookies/backend, same as knowledge's `[documentId]` route).
- `/today` page: replace the current skeleton-only placeholder
  (`frontend/src/app/(shell)/today/page.tsx`) with a real view — open tasks
  where `due_date <= today` (today's + overdue), sorted by `due_date` then
  priority, with an inline quick-complete action. **"Today" is the
  browser's local calendar date, not the server's**: the client computes
  `YYYY-MM-DD` from the user's local clock and passes it as the
  `due_before` query param, rather than the BFF or Django server computing
  "today" in its own (potentially different) timezone — otherwise a user
  near midnight, offset from the server's timezone, sees the wrong task set.
  This is the first real consumer of the Task list endpoint's date filters;
  its tests include the timezone-boundary case (client-local date differs
  from server-local date).
- Nav: add a "Tasks" entry to `SidebarContent`'s `NAV_ITEMS` (same pattern as
  the Knowledge entry added in MVP 3).
- Vitest/RTL coverage for the Tasks page (list/create/transition/delete/
  filter, workspace-switch draft-discard guard per the established
  `activeWorkspaceIdRef` pattern) and the Today page (date-boundary
  filtering, quick-complete).

### 3. E2E + docs

- Playwright smoke extension (FakeAdapter backend, no LLM involved — tasks
  are pure CRUD, nothing to fake): create a task with today's due date on
  `/tasks`, confirm it appears on `/today`, complete it from Today, confirm it
  drops off Today's open list.
- Docs: MASTER_PLAN new section for Tasks (design summary, mirroring §Q's
  structure), §L roadmap line update ("tasks *(done — §T)*", next item
  un-parenthesized), this brief's status updates per branch, and an Open
  Decisions entry recording that AI-suggested task extraction was
  deliberately deferred (see below) rather than overlooked.

## Out of scope (explicit)

- **AI-suggested task extraction from chat.** Memory's lease-fenced
  extraction pipeline (`MemoryExtractionJob`, Celery worker, conversation
  cursor, `pending_approval` curation) is a proven, reusable design — but
  reusing it here would roughly double this MVP's size for a feature with no
  natural landing spot yet. The roadmap places **tools/approvals** and
  **agents** after tasks specifically so that "the assistant creates a task"
  can be modeled as a real, approval-gated tool call once that
  infrastructure exists, rather than as a second parallel extraction
  pipeline that free-associates from conversation text the way memory does.
  Revisit at that MVP; recorded as a MASTER_PLAN Open Decision so this isn't
  rediscovered from scratch. **That future MVP must add a `source`/
  sensitivity field (or an equivalent cloud-eligibility policy) with a
  conservative backfill for every task created under this MVP** — e.g.
  defaulting existing rows to the same non-cloud-eligible treatment private
  memories get — before any task can be read into a gateway payload or an
  assistant-created-task flow ships; otherwise old manually-created tasks
  would silently become gateway-eligible by default the moment that
  machinery exists, with no explicit decision ever having been made about
  it.
- **Context injection of tasks into chat/gateway payloads.** Tasks are never
  read by `build_context` or serialized into any gateway call in this MVP —
  no "what are my open tasks" chat answer, no `tasks_used` field. That is the
  same tool-calling-shaped feature as the extraction item above, and belongs
  with it.
- Recurring tasks, reminders/notifications, snooze — no scheduling or
  notification system exists yet.
- Subtasks, dependencies, projects/tags/grouping — flat list only.
- Sensitivity/private classification on tasks — nothing reads it in this
  MVP; add it in whichever future MVP first needs to gate a task from a
  gateway payload or an export.
- Generic approval gate (MVP 5) — task completion/cancellation is ordinary
  in-app CRUD like memory curation and knowledge document management, not a
  write/delete/deploy/external-send action the gate is meant to cover.
- Company-workspace-specific task assignment (assignee ≠ creator) — the role
  matrix above only distinguishes "own" vs. not; a dedicated assignee concept
  is a company-workspace feature that can wait for MVP 10's reactivation
  (currently deferred, MASTER_PLAN §L).

## Branch plan

| # | Branch | Contents |
|---|---|---|
| 1 | `feat/mvp4-tasks-foundation` — **done (2026-07-08)**. Codex review: round 1 APPROVE (no correctness-blocking issues; role checks, actor-scoped locking, audited mutations and permission/transition test coverage all checked out against the existing memory/knowledge patterns). | `tasks` kernel app, `Task` model + state machine + role matrix, CRUD + transition API, mutation audit events, permission tests, `INSTALLED_APPS`/import-linter/URL wiring |
| 2 | `feat/mvp4-tasks-ui` — **done (2026-07-08)**. Codex review: round 1 APPROVE (BFF routes and UI components followed the existing auth/CSRF/workspace-guard/stale-response patterns; no introduced issue found). Manually verified end-to-end against the live backend (create → Today filter → complete → reopen → edit → delete). | Next.js `/tasks` page + BFF routes, real `/today` page (date-boundary filtered list + quick-complete), nav entry, Vitest coverage |
| 3 | `feat/mvp4-e2e-docs` — **done (2026-07-08) — MVP 4's final branch**. Codex review: round 1 CHANGES REQUESTED (the initial `GET /api/tasks` issued on `/tasks` mount could resolve *after* the create POST and overwrite state with the stale empty list, flaking the title assertion — fixed by waiting for the initial empty-list render before opening the create form), round 2 APPROVE. Verified with real `npx playwright test` runs both before and after the fix. | Playwright smoke extension (create → appears on Today → complete → drops off Today), MASTER_PLAN new section + §L roadmap line + Open Decisions entry, brief status updates |

Per-branch flow unchanged from MVP 2/3: implement → tests+ruff+CI green →
Codex CLI review (`--base main`) → fix → APPROVE → merge to main (no extra
user confirmation per the 2026-07-05 protocol, except a CRITICAL/HIGH finding
or a review loop dragging past a few rounds — both still checkpoint with the
user).

## Test strategy

- pytest: `for_actor` leakage matrix, state-machine transitions (legal /
  illegal / idempotent-repeat for all four transitions), role × endpoint
  permission matrix incl. the member `created_by=actor` predicate on
  edit/transition/delete, server-controlled serializer fields (`status`,
  `created_by`, `workspace` never client-settable outside `for_actor`), audit
  events on every mutation (create/edit/complete/cancel/reopen/delete) with
  an explicit assertion that audit metadata never contains `title`/
  `description` text, due-date filter correctness (`due_before`/`due_after`
  boundary, client-supplied-date-vs-server-date independence), transition-
  endpoint idempotency, **two-connection concurrency tests for the
  actor-scoped row lock**: concurrent `complete`+`cancel` on the same task
  must serialize (one wins, the other observes the post-lock state and
  either no-ops or 400s, never both succeeding against a stale read), and a
  transition racing a delete must not leave an orphaned audit event for a
  row that no longer exists.
- Vitest/RTL: Tasks page states (list/create/edit/transition/delete/filter),
  Today page (date-boundary filtering incl. overdue and the client-local-
  date-vs-server-local-date case, quick-complete), BFF CSRF/cookie
  contracts, workspace-switch draft-discard.
- Playwright: the create → Today → complete happy path (scope 3). No
  real-LLM involvement anywhere in this MVP (nothing here calls the gateway).

## Risks / notes

- The main risk is scope creep back toward memory's extraction pattern
  mid-implementation ("while we're in here, might as well...") — the Out of
  scope section above exists specifically to name that temptation and defer
  it explicitly rather than relitigate it branch by branch.
- `due_date` as a plain `DateField` (not `DateTimeField`) sidesteps timezone
  ambiguity for "is this due today" — a tradeoff worth stating plainly: this
  MVP has no notion of time-of-day due times, only due days. If a future MVP
  needs due-times, that's a migration + UI change, not a redesign of the
  state machine.
- Because this MVP touches no LLM/gateway code path at all, there is no
  ModelUsage/prompt-version surface to test — the "every LLM call recorded"
  rule (CLAUDE.md #3) simply doesn't apply here, which is itself worth
  confirming explicitly in review rather than silently assuming.
