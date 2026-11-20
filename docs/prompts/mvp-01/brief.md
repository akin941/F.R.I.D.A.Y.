# MVP 1 — Friday Web Shell: Auth + Workspace + Chat + AI Gateway v0

Detailed plan agreed on 2026-07-04 (see conversation/planning doc). Implementation
is split into branches:

| # | Branch | Status |
|---|---|---|
| 1 | `feat/mvp1-auth-workspace-foundation` — custom User, JWT, dev signup, personal workspace auto-create, `for_actor()` scoping, workspace list API | **done (2026-07-04)** |
| 2 | `feat/mvp1-audit-foundation` — AuditLog + record_audit, DB-level append-only trigger, request_id contextvar, user.signup / workspace.created / auth.login_failed events, read-only admin | **done (2026-07-04)** |
| 3 | `feat/mvp1-ai-gateway` — ModelProvider/PromptTemplate/PromptVersion/ModelUsage, InferenceGateway (retry→fallback, streaming, usage on every path), Anthropic/OpenAI-compatible/Fake adapters, seed_ai_defaults, SDK guard test | **done (2026-07-04)** |
| 4 | `feat/mvp1-assistant-chat-api` — Conversation/Message models, context builder v0, `/api/v1/ai/conversations` + SSE message endpoint, gateway `on_usage` callback, chat throttle | **done (2026-07-05)** |
| 5 | `feat/mvp1-frontend-shell` — Next.js scaffold, BFF auth, workspace shell, streaming chat, Today placeholder | **done (2026-07-05)** |
| 6 | `feat/mvp1-e2e-docs` — Playwright smoke suite (FakeAdapter, real backend) + docs | **done (2026-07-05)** |

## Branch 6 notes

- The smoke suite drives a real, unmocked backend (Postgres migrated + `seed_ai_defaults`
  run, `AI_FAKE_PROVIDER=true`) and a real frontend dev server — no route/network
  mocking. This is deliberately narrower than the branch 5 Vitest suite: one
  end-to-end path (`frontend/e2e/smoke.e2e.ts`) covering dev signup -> personal
  workspace auto-selected -> new chat -> send -> streamed FakeAdapter reply ->
  Today placeholder -> logout -> optimistic route guard redirect. Branch-level
  edge cases (retry/refresh/CSRF/staleness/race conditions) stay covered by the
  branch 5 unit/integration tests, which mock the network; duplicating them at
  the e2e layer would be slow and redundant.
- `seed_ai_defaults` is not optional for a working chat flow: `InferenceGateway.
  _select_providers()` filters `ModelProvider.objects.filter(is_active=True)`,
  and `AI_FAKE_PROVIDER` only filters an *existing* fake-type row in/out of
  selection — it doesn't create one. Without the seed command, `complete()`
  raises `NoProviderAvailable` regardless of the flag. This was already true of
  `make up`'s `web` command (`deploy/docker/compose.yml`); the e2e CI job and
  docs make the same two steps (`migrate`, `seed_ai_defaults`) explicit for
  anyone standing the backend up by hand.
- No `data-testid`s were added. A repo-wide audit found the shipped UI already
  has consistent, specific role/label/text/aria-label selectors (form labels,
  button text, `role="alert"`, `aria-label="Active workspace"`, nav
  `aria-label="Primary"`), so the smoke test selects by those instead of
  introducing a parallel selector vocabulary.
- Playwright only manages the **frontend** dev server (`webServer` in
  `frontend/playwright.config.ts`, reused if already running locally). The
  backend is an external precondition it does not start, because standing up
  Postgres + migrations + seed data is a different lifecycle than a Node dev
  server restart-on-crash — `frontend/e2e/global-setup.ts` polls the backend's
  `/healthz` before any test runs and fails fast with an actionable message
  (`make up`, or migrate+seed+runserver by hand) instead of a confusing
  navigation timeout inside the first test.
- Test file naming (`*.e2e.ts` under `frontend/e2e/`) and `vitest.config.ts`'s
  `exclude: [..., "e2e"]` keep the two suites from colliding — Vitest's default
  include glob would otherwise also try to run Playwright specs under `jsdom`.
- CI: a new `e2e` job (Postgres service, no Redis — nothing in this smoke path
  touches Celery) runs independently of the existing `backend`/`frontend`
  jobs: `uv sync` -> `migrate` -> `seed_ai_defaults` -> backend `runserver` in
  the background (bash `&` + a `curl`-poll loop against `/healthz`, exits
  non-zero if it never comes up) -> Node/`npm ci` -> `playwright install
  --with-deps chromium` -> `npm run test:e2e`. The Playwright HTML report is
  uploaded as an artifact only `if: failure()`, so successful runs don't pay
  the upload cost.
- Not covered by this branch (explicitly out of scope, consistent with the
  branch 5 gap list): concurrent-send serialization, real (non-fake) provider
  behavior, and multi-workspace switching — those remain unit/integration-test
  concerns, not smoke-test concerns.
- Codex CLI review (`--base main`): no findings — approved on the first pass.
- MVP 1 is complete as of this branch (all 6 planned branches done).

## Branch 5 notes

- **Architecture: Next.js Route Handlers as a BFF**, not a browser-to-Django
  client. The browser only ever calls same-origin `/api/...` routes; those
  routes call Django server-side and translate the JWT pair into `HttpOnly`,
  `SameSite=Lax` cookies (`friday_access` scoped to `/api`, `friday_refresh`
  scoped to `/api/auth/refresh`, `friday_session` — not a credential, just an
  optimistic route-guard flag — scoped to `/`). No JWT is ever readable from
  JS or present in a response body.
- `src/proxy.ts` is Next.js 16's renamed `middleware.ts` (the file convention
  was renamed project-wide; `export function proxy()` replaces
  `export function middleware()`). It is an **optimistic guard only** —
  every Route Handler independently re-checks the access-token cookie, so a
  stale/missing session cookie can only cause an extra redirect, never an
  authorization bypass.
- CSRF: every mutation Route Handler (login/signup/refresh/logout/
  conversation-create/message-send) validates `Origin` against `APP_ORIGIN`
  and rejects non-JSON `Content-Type`, since `SameSite=Lax` cookies alone are
  not treated as a sufficient CSRF defense.
- Streaming: `fetch + ReadableStream`, not `EventSource` — the chat endpoint
  needs `POST` + a JSON body + `Accept: text/event-stream`, none of which
  `EventSource` supports. The BFF's message-POST route forwards the
  backend's SSE body untouched (`new Response(upstream.body, ...)`); it never
  parses or buffers it. `src/lib/sse.ts` does the actual incremental
  chunk/done/error decoding client-side, using a persistent `TextDecoder` so
  a multi-byte UTF-8 character split across two network chunks decodes
  correctly instead of corrupting.
- Client-side fetch wrapper (`src/lib/api-client.ts`): on a 401 it triggers
  exactly one `/api/auth/refresh` call (single-flight — concurrent 401s
  share the same in-flight refresh) and retries the original request exactly
  once; a second 401, or a failed refresh, is a hard logout (redirect to
  `/login`), never a retry loop.
- Chat UI contract: the message list is always reconciled via a GET after
  `done`/`error`/disconnect/abort, since the backend persists the message
  row before the terminal SSE event — the client's optimistic bubbles are
  replaced by the authoritative list rather than patched in place. A second
  send is hard-blocked while a stream is in flight. A stream that ends
  without a terminal event is treated as a disconnect (manual "Refresh
  history" action), never an automatic retry (which could duplicate the
  user's message).
- Workspace context: active workspace resolves to (in order) a still-valid
  saved preference, the first `personal` workspace, or the first accessible
  workspace; the preference is namespaced in `localStorage` by user id.
  Switching workspaces clears the selected conversation and message list,
  aborts an in-flight chat stream (rather than leaving it running against
  the workspace being left), and cancels any pending conversations/messages
  request in the sense that its response is discarded on arrival if it's for
  a workspace/conversation that's no longer current — this is checked via a
  ref tracking the current id, not by literally aborting the fetch, since
  the load functions are also called imperatively (new chat, select).
  `handleSend`/`handleNewChat` additionally use a synchronous ref lock
  (`sendingRef`/`creatingChatRef`), not just `isStreaming`/`creatingChat`
  state, since two rapid clicks in the same tick can both observe the
  pre-update state before a re-render lands — a real race Codex CLI's review
  caught, since state alone isn't a synchronous mutex.
- Test strategy (explicit decision, not deferred to branch 6): Vitest +
  React Testing Library ships in this branch, covering the SSE parser,
  the auth BFF routes (token-leakage, cookie flags, refresh rotation, CSRF),
  the fetch wrapper's refresh/retry contract, workspace-context defaulting,
  and the chat component's optimistic/streaming/error/double-send behavior.
  Branch 6 adds only the real-browser Playwright smoke suite on top.
- `jsdom` test-environment notes: `server-only` throws under jsdom's
  browser-like globals (mocked to a no-op in `src/test/setup.ts`, since the
  guard is a compile-time-only canary irrelevant to testing the module's
  logic); `ResizeObserver`/pointer-capture aren't implemented in jsdom and
  are stubbed, since Radix's `ScrollArea`/`Select`/`Sheet` call them
  unconditionally.
- Dev flow: frontend runs as a separate Node process (`make frontend-dev`,
  port 3001 fixed — required to match `APP_ORIGIN`/`CORS_ALLOWED_ORIGINS`),
  not added to the Docker Compose stack in this branch. CI gained an
  independent `frontend` job (Node 24, `npm ci/lint/typecheck/test/build`).
- Manually verified against the live backend (FakeAdapter) end-to-end, both
  via direct HTTP/cookie-level checks and a real browser: dev signup ->
  personal workspace auto-selected -> new chat -> streamed reply -> Today
  placeholder -> logout -> route guard redirects `/chat` back to `/login`.
- Codex CLI review round 1 (`CHANGES REQUESTED`) caught real bugs beyond
  what manual testing exercised: a retried request that's still 401 after a
  successful refresh was returned as-is instead of triggering logout; a
  workspace switch didn't abort the in-flight stream or guard against a
  stale conversations/messages response from the workspace being left;
  `isStreaming`/`creatingChat` state isn't a synchronous mutex against a
  same-tick double click; `refresh`/`logout` didn't enforce the
  Content-Type check every other mutation route had; `rejectNonJson` used a
  substring rather than exact media-type match; the 429 `Retry-After` header
  wasn't forwarded; and `frontend/.env.example` was silently swallowed by
  the `.env*` gitignore rule. All fixed and covered by new tests before
  requesting re-review.
- Round 2 (`CHANGES REQUESTED`, one remaining blocker): the stale-response
  guard for messages relied on a ref updated inside a `useEffect`, which
  runs after the render commits — a genuine gap where an in-flight request
  for the previous conversation could still read the stale ref. Fixed by
  updating the ref synchronously at every call site that changes the
  selection (`selectConversation()` helper), not via an effect.
- Round 3: `APPROVE WITH NITS` (no blockers, no important findings) —
  mergeable. Remaining nits (test rigor only, not app bugs) were still
  addressed: the same-tick double-click tests now wrap both `fireEvent`
  calls in one `act()` so the second click genuinely observes pre-click
  state, and a direct regression test covers the messages-staleness ref
  specifically (the prior coverage only exercised it at the
  workspace/conversations-list level).

## Branch 4 notes

- `assistant.conversation.created` is recorded via `audit.services.record_audit()`
  from `assistant.services.create_conversation()` (one `transaction.atomic`
  block for the row + its audit entry), matching the `user.signup`/
  `workspace.created` pattern from branch 2. This was a post-review addition:
  the original v2 spec omitted it, Codex classified the omission as
  review-blocking on a second pass, and it was fixed and tested before the
  branch was considered done. Per-`Message` audit events were explicitly
  ruled out of scope for this branch.
- Git housekeeping: this branch had been created from the audit-foundation tip
  before branch 3 (`feat/mvp1-ai-gateway`) was merged, so it initially lacked
  `ai_core`. Branch 3 was fast-forward merged into `main`, then this branch was
  fast-forwarded onto the new `main` tip before any assistant code was written.
- `InferenceGateway.complete(..., on_usage=callback)`: fires exactly once, right
  after `_record()` persists the (single) ModelUsage row, on every accounted
  path (sync success/failure, no-provider, stream success/failure/disconnect).
  Centralizing the call inside `_record()` avoided duplicating it at each of
  the gateway's call sites.
- `assistant.context.build_context()` returns `(messages, context_report,
  prompt_version)` — the resolved `PromptVersion` is threaded straight into the
  gateway call instead of being re-queried, so a `friday_system.active_version`
  repoint mid-request can't desync the sent prompt from the recorded lineage.
- SSE contract: the assistant `Message` row is persisted in `stream_assistant_
  reply`'s `finally` *before* the `done`/`error` event is yielded (not after,
  as an earlier draft had it) — a client cannot observe the terminal event and
  then race the write.
- `core.context.request_id_var` is re-published inside `stream_assistant_reply`
  itself: `RequestIDMiddleware` resets the contextvar as soon as the view
  returns the (still-unconsumed) `StreamingHttpResponse`, before Django ever
  iterates it, so the generator has to restore the id for its own deferred
  execution or `ModelUsage.request_id` ends up empty for every streamed call.
- `POST /api/v1/ai/conversations/<id>/messages` carries a `ScopedRateThrottle`
  (`throttle_scope = "chat"`, `DEFAULT_THROTTLE_RATES["chat"] = "30/min"`);
  `GET` on the same view is exempted via `get_throttles()`. Resolves the
  chat-endpoint half of the branch-1 throttle TODO; auth throttling is still
  open.
- Known, accepted gap (confirmed in review): concurrent sends to the same
  conversation are not serialized, so two in-flight POSTs can race each
  other's context/turn ordering. Deferred past MVP 1 by design — see the
  context builder's own note that a concurrent request may only see
  previously committed history.
- ASGI is not wired up anywhere in this repo (`runserver`/WSGI only), so the
  SSE generator is intentionally synchronous; revisit if/when ASGI serving is
  introduced.

## Branch 3 notes

- **ModelUsage.provider is nullable** (user-approved decision): the "one usage row
  per gateway call" guarantee must also hold when no provider is available at all
  (`error_type="no_provider"`, provider empty in admin).
- One ModelUsage per gateway *call*, not per attempt — retry/fallback details live
  in `context_report["attempts"]`.
- Streaming: retry/fallback only until the first chunk; after that errors are
  terminal. Client disconnect (GeneratorExit) records `client_disconnect` with
  output tokens estimated from streamed characters. Known edge: a stream generator
  that is never started produces no usage row (chat branch always consumes).
- Fake provider is selectable only when `AI_FAKE_PROVIDER=true` (dev compose sets
  it); real providers are selectable only when their `api_key_env_var` is present.
- API keys: DB stores only the env var *name*; adapter errors are wrapped into
  secret-safe strings. Static guard test forbids `anthropic`/`openai` imports
  outside `ai_core/`.
- Prompt discipline: versions are never edited in place — new version + repoint
  `active_version` (enforced by convention + admin docstring, not DB).

## Branch 2 notes

- Immutability is enforced by a Postgres trigger (`audit/migrations/0002`), not
  just admin permissions; verified manually via psql UPDATE/DELETE.
- `core/context.py` holds the request_id contextvar; middleware sets/resets it —
  the same mechanism will feed ModelUsage and structlog in the gateway branch.
- Import contract added: audit never imports business apps (one-way, apps → audit);
  audit's own tests are exempted via ignore_imports.
- Successful-login audit deliberately skipped (MVP 1 plan: login_failed only).
- Git housekeeping: branch 1 was fast-forward merged into main before this branch
  started (audit branch had been created from the MVP 0 tip by mistake).

## Branch 1 notes

- `AUTH_USER_MODEL = accounts.User` — dev DB was reset (`down -v`); MVP 0 dev data
  discarded by agreement.
- sqlite fallback removed: Postgres is now the only database target; local default
  `postgres://friday:friday@localhost:5433/friday` (Compose host port).
- Settings tolerate set-but-empty env vars (copied `.env.example` no longer breaks).
- CI now runs against a pgvector/pg16 service container.
- Scoped-model pattern: `core.db.WorkspaceScopedModel` / `WorkspaceScopedManager`;
  `Workspace` has its own `for_actor` (membership-driven). Out-of-scope objects →
  404 policy applies from the next branches on (no detail endpoints yet in this one).
