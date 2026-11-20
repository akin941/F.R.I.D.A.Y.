# Phase 0 — Foundation: ADR-008 in Code

> Status: **done (2026-07-17)** — approved after 3 rounds of Codex
> plan-review; all four branches shipped, see branch table.
> Parent decisions: ADR-008, `docs/VISION_FRIDAY_2.md`, MASTER_PLAN §AA.

## Goal / value

Land the Friday 2.0 decisions as small, safe code so daily use starts
immediately: firms become definable, the local Ollama stack covers
embeddings too, gateway routing becomes auditable, and — the one real
defect fix — gated agent requests become decidable by the owner in
production. No harness code, no UI redesign, no memory work in this phase.

## Boundary vs. existing systems

- `approvals` is modified (decision rule), **not** rebuilt: state machine,
  role matrix, expiry sweep, audit calls all stay.
- `ai_core` gains a provider row + a routing-reason audit field; the
  gateway's retry/fallback/exactly-one-ModelUsage contract is untouched.
- `workspaces` gains profile data; the scoping axis (ADR-001) is untouched.
- Nothing imports anything new across app boundaries (import-linter stays
  as-is).

## Scope (one branch each)

### 1. `feat/p0-approvals-owner-decides` — single-owner decision rule

Implements ADR-008 k.3. Today `_check_requester_not_decider`
(`approvals/services.py:147`) blocks the owner from deciding any request
carrying `requested_by=owner` — and `agents/execution.py:187` creates
agent tool-request approvals with `actor=agent.created_by`, so in a
single-human production deployment **no agent request is decidable at
all**. Changes:

- New `ApprovalRequest.origin` field: `human` (default) | `agent` |
  `harness` (value reserved now, used in Phase 2). `agents/execution.py`
  passes `origin="agent"`; the HTTP create path stays `human`.
- The two-human rule is replaced per ADR-008 k.3: a decision requires
  (a) curator role (unchanged), (b) a high-trust channel (unchanged,
  MVP 9), (c) an explicit separate decision action (structurally
  unchanged — decisions are their own endpoint). `_check_requester_not_
  decider` is removed; the owner may decide any pending request,
  including their own and their agents'.
- `DEV_ALLOW_SELF_APPROVAL` setting, its boot guard (`approvals/apps.py`)
  and its tests are removed (obsolete once the rule lands).
- **Decision context becomes explicit and non-default at the service
  boundary** (cross-review P1): `approve_request`/`reject_request`
  currently default `channel=CHANNEL_WEB` for direct callers
  (`approvals/services.py:206`) — safe today only because the two-human
  rule blocks most misuse. With self-decision allowed, a future internal
  caller passing the owner user would decide "as web" by accident. The
  `channel` kwarg loses its default (required, keyword-only); every
  caller must pass the real request channel (`approvals/views.py` already
  does), `_check_channel_is_web` stays, and tests pass channels
  explicitly. The invariant under test shifts from "no machine caller
  exists today" to "the service refuses any call that does not carry an
  explicit high-trust decision context."
- **Signup actually off in the daily stack** (cross-review P2): base
  already defaults `False`, but `config/settings/dev.py:7` defaults
  `True`, Compose runs `config.settings.dev` without overriding it, and
  `.env.example:72` says `true`. This branch flips the `dev.py` default
  to `False`, changes `.env.example` to `SIGNUP_ENABLED=false` (comment:
  single-owner, ADR-008), and verifies the Compose stack boots with
  signup rejected. Signup code removal itself still waits for Phase 4's
  device-trust identity.
- **Owner bootstrap command** (cross-review round 3: with signup off there
  is currently *no* way to create the first account — `create_dev_pat`
  requires an existing user, `accounts/management/commands/
  create_dev_pat.py:37`, and `register_user()` is only reachable through
  the now-closed signup view). New `bootstrap_owner` management command:
  calls the existing `register_user()` service (so the personal workspace
  and the `user.signup` audit row are created exactly as before), takes
  email + password (password via env var or interactive prompt, never a
  CLI argument), and **refuses to run if any user already exists** — it
  bootstraps the single owner, it is not a user-creation tool. Documented
  in README/.env.example as the one way in; `create_dev_pat` then works
  against the bootstrapped account.
- Regression suite (release-gating): machine actors still cannot decide
  (agents/celery never call the decision service — asserted, plus the
  explicit-channel requirement above), low-trust channels still get 403
  (existing PAT/Telegram tests re-asserted against the new rule), audit
  rows still written on every decision, e2e/Playwright fixtures audited
  for reliance on the removed flag or on a second user.

### 2. `feat/p0-firm-profiles` — workspaces as firm dossiers (backend)

- `Workspace.profile` JSONField (default `{}`) with a validated shape:
  `repos` (list of {name, url, default_branch}), `services` (list of
  {name, kind, url}), `deploy_targets` (list of {name, host, notes} —
  **metadata only, no secrets**; secrets stay in env per CLAUDE.md),
  `notes` (str). Serializer-level schema validation, not free-form.
- The workspace API surface today is **list-only** (`workspaces/urls.py`
  has one route; the serializer is fully read-only — cross-review round
  3), so this branch explicitly adds: a retrieve endpoint and a `PATCH`
  update endpoint for `profile` (owner/admin update, member/viewer read,
  role × endpoint tests), a writable `profile` serializer field with the
  schema validation above, and audit on mutation
  (`workspace.profile_updated`, no profile content in the audit payload).
  No other workspace field becomes writable.
- Dossier UI ships in Phase 1 (with the firm memory block); this branch is
  backend + tests only.

### 3. `feat/p0-ollama-embedding` — local embeddings

- `seed_ai_defaults`: `ollama-embedding` provider (capability=`embedding`,
  `openai_compatible`), gated on the same `OLLAMA_API_KEY` opt-in,
  `model_id` from `OLLAMA_EMBED_MODEL_ID` (default `nomic-embed-text`,
  dimension 768 via `OLLAMA_EMBED_DIMENSION`), base_url shared with the
  chat provider. Mirrors the existing ollama-local block; idempotent.
- `.env.example` + `deploy/docker/compose.yml` passthroughs.
- No knowledge-side code change expected: retrieval already filters chunks
  by serving provider **and** vector dimension (MASTER_PLAN §Q), so
  documents embedded under the old provider simply don't match the new
  one — the brief documents the re-ingest requirement instead of building
  a migration.
- Tests mirror the existing Ollama seed tests (skip-when-unset, defaults,
  overrides).

### 4. `feat/p0-model-tiers` — routing skeleton (§J)

- `ModelTier` table: `tier` (`cheap`/`mid`/`strong`), FK to
  `ModelProvider`, unique per (tier, provider), plus a purpose→tier map
  (start as a settings dict, promote to DB only when it needs runtime
  editing — decision recorded here to avoid a premature table).
- `ModelUsage.routing_reason` (new nullable CharField): every gateway call
  records why its provider was chosen — `tier:<name>`, `priority`,
  `fallback:<n>`, `only_provider`. Backfill: null for history, no fake
  reasons.
- Gateway behavior: if the purpose maps to a tier and that tier has
  providers, `_select_providers` orders them first, then falls back to the
  existing priority order — with no tier configuration the gateway behaves
  byte-for-byte as today (FakeAdapter test asserts this).

## Out of scope (explicit)

Harness app and runners (Phase 2) · memory embeddings / work_log / overlay
(Phase 1) · any frontend change (Phase 1 dossier UI, Phase 4 redesign) ·
device-trust identity and signup code removal (Phase 4) · OpenAI-compatible
facade (Phase 4) · Home Assistant (Phase 5) · voice (Phase 6).

## Test strategy

Role × endpoint permission tests for every touched surface (CLAUDE.md
rule 7); FakeAdapter for all gateway paths (rule 5); migration check
(`makemigrations --check`); `make lint` + `make test` green per branch;
branch 1 additionally re-runs the full approvals + agents + integrations
suites as its own release gate (it touches the security core).

## Risks

- **Branch 1 touches ADR-004's enforcement core.** Mitigation: the change
  is a *narrowing replacement* (rule swap, not removal of the gate), the
  regression suite above is written before the rule swap, and the branch
  gets cross-vendor review (Codex) before merge — the same discipline the
  vision assigns to the harness.
- **e2e fixtures may silently depend on `DEV_ALLOW_SELF_APPROVAL` or a
  second user.** Audit them in branch 1 before deleting the flag.
- **Tier skeleton scope creep.** The full cost-routing feature (§J) is not
  this branch; anything beyond the reason field + tier-first ordering is
  deferred.

## Branch table

| # | Branch | Status |
|---|---|---|
| 1 | `feat/p0-approvals-owner-decides` | **done (2026-07-17, `1cd62f7`)** |
| 2 | `feat/p0-firm-profiles` | **done (2026-07-17, cherry-picked)** |
| 3 | `feat/p0-ollama-embedding` | **done (2026-07-17, `881c6e2`)** |
| 4 | `feat/p0-model-tiers` | **done (2026-07-17, `7c4c25b`)** |

All four landed on the session working branch (single-branch flow) rather
than separate feat/ branches; each is its own conventional commit. Branches
2-3 were implemented by Sonnet subagents in isolated worktrees and
cherry-picked after verification; branches 1 and 4 (security core, gateway
contract) were implemented directly. Verification at closeout: backend
suites green (approvals/accounts/agents/tools/skills/integrations 445;
ai_core 93; assistant/knowledge/memory 584; workspaces+accounts 85),
import-linter 24/24, migrations check clean, ruff clean, frontend 329
tests + eslint + tsc green. e2e smoke requires SIGNUP_ENABLED=true (see
branch 1) and runs in CI.
