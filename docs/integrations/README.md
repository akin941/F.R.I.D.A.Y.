# Integrations

MVP 9 added two channels for reaching Jarvis outside the web app: a CLI
(bearer-token authenticated) and a Telegram bot (relayed, no direct request
of its own). Both are new *front doors* onto the existing chat pipeline —
neither adds a new way to *act*. The one new concept both channels share is
**channel trust** (`core/channels.py`): a request authenticated by anything
other than the standard password+JWT web login is low-trust, and low-trust
channels can never decide an `ApprovalRequest` (`approve`/`reject`) — they
can still propose one, exactly as the web app can. See ADR-004 and
`docs/prompts/mvp-09/brief.md` for the full design rationale.

## CLI channel

A **Personal Access Token** (`jpat_...`, prefix `TOKEN_PREFIX` in
`accounts/authentication.py`) is a bearer credential bound to exactly one
workspace for its lifetime — a full-permission credential for that
workspace (minus the channel restriction on decisions above), not a
narrower-scoped API key.

- **Mint one**: from the web app's Integrations settings page
  (`/integrations`), or `POST /api/v1/auth/tokens` (JWT-authenticated only —
  minting a token is itself a sensitive, human-decided action). The raw
  token is returned exactly once, in that response.
- **Dev bootstrapping**: `manage.py create_dev_pat <email> <workspace-name>`
  mints a token for an already-existing user/workspace without needing the
  settings UI first (dev-only, requires `DEBUG=True`).
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
