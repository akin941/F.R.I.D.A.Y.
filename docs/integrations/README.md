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
(`cli`) for a `ChannelPrincipal`, and `unknown` (low-trust) for anything
else — so a future authenticator that forgets its channel is low-trust, and
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
  (`/api/v1/agents*`), and approvals *creation* and reading
  (`/api/v1/approvals/requests`, `GET …/requests/<id>`, but not the
  `<id>/approve`/`<id>/reject` transition). `workspace` is
  never supplied by the client on these endpoints — it's implied by the
  bound token (`core.channels.scoped_for_actor`); the web inbox's
  `scope=all` (every membership, Phase 4 branch 5) is ignored for a PAT,
  which stays on its bound workspace. A minimal reference client
  ships at `backend/integrations/cli/jarvis_cli.py`
  (`uv run python -m integrations.cli.jarvis_cli`), configured via
  `~/.config/jarvis/cli.toml` or `JARVIS_CLI_TOKEN`/`JARVIS_CLI_URL` env vars.
- **Revoke**: `/integrations` settings page, or
  `POST /api/v1/auth/tokens/<id>/revoke` (soft-revoke, audit trail kept).

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

## Other integrations (not yet built)

Candidates from the original MVP 9 scoping, still unstarted:
Gmail/Calendar/Drive (own OAuth apps, read-only first, minimal scopes),
GitHub, local folder sync.
