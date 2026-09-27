# Integrations

MVP 9 added two channels for reaching Jarvis outside the web app: a CLI
(bearer-token authenticated) and a Telegram bot (relayed, no direct request
of its own). Both are new *front doors* onto the existing chat pipeline —
neither adds a new way to *act*. The one new concept both channels share is
**channel trust** (`core/channels.py`): only a trusted device's web session
is high-trust; everything else is low-trust, and low-trust channels can never
decide an `ApprovalRequest` (`approve`/`reject`) — they can still propose
one, exactly as the web app can. See ADR-004 and
`docs/prompts/mvp-09/brief.md` for the full design rationale.

### The high-trust web channel, defined (Phase 4 branch 2)

Default-deny since Phase 4 branch 2. A request is `CHANNEL_WEB` iff:

1. it authenticated through `DeviceJWTAuthentication` (the sole
   `DEFAULT_AUTHENTICATION_CLASSES` entry) with a valid access JWT carrying
   the `did` (device id) claim — a JWT without it is rejected (401);
2. `did` names a `TrustedDevice` of the token's user that is neither revoked
   nor idle-expired (`DEVICE_IDLE_EXPIRY_DAYS`, default 90) — checked on
   every request, so revoking a device ends its access with the next request
   (an already-open chat stream finishes its reply);
3. the user is active, the installation owner (`User.is_owner`) and not an
   agent;
4. it arrived through the Next.js BFF, whose Origin/Content-Type checks are
   the CSRF boundary.

The authenticator then sets `request.auth` to a `WebSessionPrincipal`;
`request_channel()` returns `web` only for that type, the PAT's channel
(`cli` or `openai`, by token kind) for a `ChannelPrincipal`, and `unknown`
(low-trust) for anything else — so a future authenticator that forgets its channel is low-trust, and
`config/tests/test_channel_matrix.py` fails until it is classified.
`core.permissions.IsWebChannel` guards every web-only surface on top of the
service checks: approval decisions, PAT mint/revoke, Telegram link codes,
pairing codes, the remote key, devices, firm creation, owner deletes (ADR-009)
and task-draft confirmation.

Devices are enrolled at `/enroll` with a one-time pairing code (`manage.py
issue_pairing_code`, or "Yeni cihaz eşleştir" on a trusted session) or the
remote access key; there is no signup and no password login. PINs are
LAN-gated: the BFF forwards the browser IP only from `TRUSTED_CLIENT_IP_HEADER`
(set by the owner's reverse proxy) as `X-Jarvis-Client-IP` with
`X-Jarvis-BFF-Key`, and Django ignores the IP without that key.

## CLI channel

A **Personal Access Token** (`jpat_...`, prefix `TOKEN_PREFIX` in
`accounts/authentication.py`) is a bearer credential bound to exactly one
workspace for its lifetime — a full-permission credential for that
workspace (minus the channel restriction on decisions above), not a
narrower-scoped API key.

- **Mint one**: from the web app's Integrations settings page
  (`/integrations`), or `POST /api/v1/auth/tokens` (web channel only —
  minting a token is itself a sensitive, human-decided action). The raw
  token is returned exactly once, in that response.
- **Expiry** (Phase 4 branch 1): every token expires — 90 days by default,
  or a lifetime chosen at mint (`expires_in_days`, at most 365;
  `PAT_DEFAULT_TTL_DAYS`/`PAT_MAX_TTL_DAYS`). Tokens minted before expiry
  existed were given 30 days. Expired, revoked, unknown and
  deactivated-user tokens all get the same 401; each failure is audited
  (`accounts.pat.auth_failed`, token prefix only, rate-limited).
- **Dev bootstrapping**: `manage.py create_dev_pat <email> <workspace-name>`
  mints a token for an already-existing user/workspace without needing the
  settings UI first (dev-only, requires `DEBUG=True`; audited as
  `accounts.pat.created` with `via=management_command`).
- **Use it**: `Authorization: Bearer jpat_...` against any of the
  PAT-allowlisted endpoints — chat (`/api/v1/ai/conversations*`), agents
  (`/api/v1/agents*`), and approvals *creation* (`/api/v1/approvals/requests`,
  but not the `<id>/approve`/`<id>/reject` transition). `workspace` is
  never supplied by the client on these endpoints — it's implied by the
  bound token (`core.channels.scoped_for_actor`). A minimal reference client
  ships at `backend/integrations/cli/jarvis_cli.py`
  (`uv run python -m integrations.cli.jarvis_cli`), configured via
  `~/.config/jarvis/cli.toml` or `JARVIS_CLI_TOKEN`/`JARVIS_CLI_URL` env vars.
- **Revoke**: `/integrations` settings page, or
  `POST /api/v1/auth/tokens/<id>/revoke` (soft-revoke, audit trail kept).
- **Kind** (Phase 4 branch 6): a token is minted as `cli` (the default) or
  `openai`, fixed for its lifetime. Only `cli` tokens work on the endpoints
  above; an `openai` token there gets 403 `token_kind_forbidden` (and a
  `cli` token gets the same on the OpenAI-compatible facade below). The
  kind is recorded on the mint/revoke audit rows; `create_dev_pat --kind
  openai` mints one from the shell.

## Telegram bot

One Telegram chat links to one `(user, workspace)` pair, backed by one
`Conversation` for the lifetime of the link — there's no in-bot "new
conversation" command.

**Setup** (a real deployment, not the dev stack):
1. Create a bot with [@BotFather](https://t.me/BotFather), set
   `TELEGRAM_BOT_TOKEN` to its API token.
2. Choose your own secret string and set `TELEGRAM_WEBHOOK_SECRET` to it —
   this is *not* the bot token; it's a value only you and Telegram's
   `setWebhook` call know, used to verify inbound webhook authenticity via
   the `X-Telegram-Bot-Api-Secret-Token` header.
3. Run `manage.py register_telegram_webhook <public-https-base-url>` once
   (an operator action, not something automated) to point Telegram's
   `setWebhook` at `<base-url>/api/v1/integrations/telegram/webhook`.

**Linking**: request a code from `/integrations` (`POST
.../telegram/link-code`, 10-minute TTL), then DM the bot `/link <code>`.
Only 1:1 direct messages are supported — a code sent from a group is
silently ignored, never consumed (a group chat has no per-sender identity
in this design). Re-linking with a fresh code replaces the existing link
for that `(user, workspace)` pair rather than erroring.

**Relaying**: once linked, any message sent to the bot goes through the
exact same `classify_intent` → `build_gateway_stream` → persist pipeline
the web chat endpoint uses — same router, same `ModelUsage` accounting.
A `task_draft`/`tool_request` classification degrades to a **read-only**
plain-text description in this channel; there is no confirm path for a
Telegram-surfaced draft in either channel (re-issue the request from the
web chat to actually act on it — see the brief's §4 for why this is a
deliberate simplification, not an oversight).

**Known limitations, deliberate for v1**:
- No `update_id`-based redelivery deduplication — a redelivered update can
  duplicate one relayed message and one gateway call. A cost, not a
  correctness bug (no double side effects beyond the extra LLM call);
  revisit only if actually observed in practice on the current
  single-worker topology.
- The webhook handler's reply-sending step (`telegram_client.send_message`)
  makes a real, bounded (10s timeout) outbound call to Telegram's API —
  this is unavoidable in a live deployment, and it's also why the
  Playwright e2e Telegram section (`frontend/e2e/smoke.e2e.ts`) accepts one
  such call failing fast against its fixture bot token rather than trying
  to eliminate it: the state that section actually asserts on (the link,
  the persisted messages) is fully committed before that call ever runs.

## OpenAI-compatible facade (Phase 4 branch 6)

Lets Open WebUI-class clients use Jarvis as a **low-trust, chat-only**
secondary channel (ADR-008 decision 6). Django serves
`POST /api/v1/openai/chat/completions` and `GET /api/v1/openai/models`
(`backend/integrations/openai_facade.py`); the BFF exposes them as
`/v1/chat/completions` and `/v1/models`
(`frontend/src/app/v1/`, `frontend/src/lib/openai-facade-proxy.ts`), so a
client's base URL is `https://<host>/v1`.

**Setup.** On `/integrations` create a token with kind "OpenAI uyumlu
istemci"; the reveal banner shows a copyable snippet. In Open WebUI: Admin
Panel → Settings → Connections → OpenAI API, base URL `https://<host>/v1`,
API key = the token. Use `jarvis` for chat and set `jarvis-lite` as the
"task model" (titles, tags, follow-up suggestions).

**What it can and cannot do.**

- Auth is an `openai`-kind PAT only (`FacadeTokenAuthentication` →
  `CHANNEL_OPENAI`); web sessions and `cli` tokens are refused. The
  workspace is the token's; no request field can name another.
- `jarvis` runs the web chat's pipeline (router → memory/knowledge context →
  persisted reply) with the router restricted to `chat`, `memory_query`,
  `knowledge_query`: no task drafts, no tool proposals, no approvals, no
  writes. Its conversations appear in web chat with the "OpenAI uyumlu
  istemci" badge; memory extraction runs as usual, so anything learned
  arrives as a `pending_approval` suggestion. The cross-workspace overlay
  stays web-only.
- `jarvis-lite` is ephemeral: the client's system/user/assistant messages
  go to the gateway as they are (purpose `facade.ephemeral`), with no Jarvis
  context; nothing is persisted, nothing is extracted.
- `tools`, `functions`, a non-`none` `tool_choice`/`function_call`, `n>1`
  and a non-text `response_format` are rejected (400
  `unsupported_parameter`); `temperature`, `max_tokens`, `top_p` etc. are
  accepted and ignored — prompt-version parameters and server-side routing
  govern. Unknown model → 404 `model_not_found`. Only `jarvis` and
  `jarvis-lite` exist (no tier aliases until Phase 5's routing map).
- Every call goes through the `InferenceGateway`; `ModelUsage` rows carry
  `context_report.channel = "openai"` and the model alias.

**Conversation mapping.** Clients resend the whole history; Jarvis reads
only the *user* turns, as hashes, to find the server-side conversation
(`integrations/facade_mapping.py`, `FacadeConversation`). Client
`system`/`assistant`/`tool` messages are never persisted — a client cannot
plant assistant turns into memory. A request continues the most recent
binding of the same token and first message whose recorded user turns are
a prefix of the client's (turns answered by another model are tolerated);
a regenerate or edit **forks**: a new conversation seeded with copies of the
server-side messages before the divergence (audited
`assistant.conversation.forked`, extraction cursor after the copies). A new
chat with the same first message starts a new conversation.
`X-Jarvis-Conversation-Id: <uuid>` pins a conversation of the bound
workspace (404 otherwise); every `jarvis` response returns that header.

**Wire format.** Non-streaming: `chat.completion` with `usage`. Streaming
(`stream: true`): `chat.completion.chunk`s — a `delta.role` chunk, content
chunks, a `finish_reason: "stop"` chunk, a usage chunk when
`stream_options.include_usage`, then `data: [DONE]`; a mid-stream gateway
error is one `data: {"error": {...}}` line then `[DONE]` (the reply is kept
as `partial`). `id` is `chatcmpl-<assistant message id>`. Errors use
OpenAI's `{"error": {message, type, param, code}}` shape. Golden files:
`backend/integrations/tests/golden/`.

**Limits.** Per token: `FACADE_RATE` (default `20/min`, Redis, fail-closed
503) and `FACADE_MAX_CONCURRENT_STREAMS` (default 2) completions in flight
(429 with `Retry-After`); a new user turn (for `jarvis-lite` the whole
message list) of at most `FACADE_MAX_REQUEST_CHARS` (32000). In front of
Django the BFF answers requests without a `Bearer jpat_...` credential
itself and, after too many failed authentications in a minute
(`FACADE_PREAUTH_FAILURES_PER_SOURCE` per trusted client IP, else
`FACADE_PREAUTH_FAILURES_GLOBAL` install-wide), returns 429 without
reaching Django. Without a trusted client-IP header that bucket is global,
so an invalid-token flood also pauses valid tokens until the minute ends —
the accepted residual risk. The BFF routes never read or forward cookies
and emit no CORS headers. Failed facade authentications are audited like
CLI ones (`accounts.pat.auth_failed`, `surface: openai`, rate-limited).

**Verification (2026-09-27, branch 6).**

- Contract: `integrations/tests/test_openai_facade.py`,
  `test_facade_mapping.py`, `test_facade_contract.py` (FakeAdapter, no
  `openai` SDK in the backend code or tests).
- Client smoke with the official `openai` Python client (2.44) through the
  BFF (`next start` → `/v1` → `runserver`, FakeAdapter): `models.list()` →
  `jarvis`, `jarvis-lite`; non-streaming and streaming (`include_usage`)
  completions; a second request with the history continued the same
  conversation; `jarvis-lite` persisted nothing; unknown model 404, `tools`
  400, `cli` key 403, bad key 401 — all in OpenAI's error shape.
- Load check (plan-review R4b): gunicorn gthread 3 × 16 (the daily-stack
  default), Redis-backed limits, FakeAdapter slowed to 0.8 s per chunk
  (~7.4 s replies): 8 parallel SSE streams (4 web chat + 4 facade on two
  tokens) plus 4 polling tabs (activity summary + conversation list every
  second). All 8 streams 200; worst time to first byte 0.92 s (web chat,
  whose first byte is the first model chunk after the simulated 0.8 s) /
  0.11 s (facade role chunk); 64 polling requests, median 15 ms, max 46 ms
  — nothing waited more than 2 s; all stream slots were back at 0 after.
- A manual Open WebUI smoke (streaming, history continuity, `jarvis-lite`
  as the task model) is still to be recorded here by the owner.

## Other integrations (not yet built)

Candidates from the original MVP 9 scoping, still unstarted:
Gmail/Calendar/Drive (own OAuth apps, read-only first, minimal scopes),
GitHub, local folder sync.
