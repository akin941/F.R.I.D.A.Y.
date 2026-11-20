# `google` connector — governance twin

Gmail and Google Calendar for the owner, over Google's REST APIs (brief D9,
R4; ADR-010). Setup and recovery: `docs/runbooks/google-connector.md`.

## Summary
- **Manifest:** `backend/connectors/manifests/google.py`, version 1
- **Kind:** `native` — bridge `backend/connectors/bridges/google.py` (no MCP
  server, no sidecar, no Google SDK: `httpx` against ~6 REST endpoints)
- **Status:** active
- **Data class:** `personal` — personal workspace only, web-only reads, a
  read marks the conversation `private` + `personal_data`, so every later
  LLM call in it is served only by a `personal_data_allowed` (local)
  provider (D5)
- **Workspace types:** personal
- **Read channels:** web (never CLI, Telegram or the OpenAI facade)

## Source and maintenance
- Upstream: Gmail API v1 (`gmail.googleapis.com/gmail/v1/users/me`) and
  Calendar API v3 (`www.googleapis.com/calendar/v3/calendars/primary`),
  OAuth 2.0 token endpoint `oauth2.googleapis.com/token`.
- Google-only by decision (Q4): `MailReader` / `CalendarReader` protocols in
  the bridge leave room for another adapter; IMAP/SMTP/CalDAV are not used.
- The bridge has no server-side schema to pin; each tool's pin is the
  sha256 of the request shape the bridge sends (`TOOL_PINS`). A bridge
  change that alters a request shape changes the pin: update
  `schema_sha256` and bump the manifest `version` in the same commit.

## Tools
| Friday name | Bridge tool | Kind | Schema pin (sha256) | Scope (any of) | Gate / user-invocable | work_log |
|---|---|---|---|---|---|---|
| `mail_search` | `gmail.messages.search` | read | `1e9d499346a7f61e…` | `gmail.readonly` | — | — |
| `mail_read` | `gmail.messages.read` | read | `0d95b8182eb2bff1…` | `gmail.readonly` | — | — |
| `calendar_agenda` | `calendar.events.agenda` | read | `a5275ab8f20b76df…` | `calendar.readonly`, `calendar.events` | — | — |
| `mail_send` | `gmail.messages.send` | write | `ff4a0ca8f64a6ce1…` | `gmail.send` | `mail_send` / yes | yes (private) |
| `calendar_create_event` | `calendar.events.insert` | write | `a2be9cda597cefab…` | `calendar.events` | `calendar_create_event` / yes | yes (private) |

Bounds: `mail_search` ≤ 20 messages (from, subject, date, 300-char
snippet); `mail_read` one message's text body (text/plain, else text/html
reduced to text; attachments never read) cut at `GOOGLE_MAIL_BODY_MAX_CHARS`
(6000) — without an id it reads the newest message matching a Gmail search
(default `in:inbox`); `calendar_agenda` 1–31 days, ≤ 50 events, cancelled
events dropped. Every read is then bounded by `CONNECTOR_RESULT_MAX_CHARS`,
stripped and fenced like any connector read.

Writes (gate-class, decided on the web only):
- `mail_send`: ≤ 10 recipients across To and Cc, **bare addresses only**
  (the serializer rejects display names, quoted local parts and comments, so
  the dialog can only ever show raw addresses), one-line subject, plain-text
  body (≤ 20 000 chars, newlines normalized, always ending in one), optional
  Gmail `thread_id` and `In-Reply-To` Message-ID. The dialog shows every
  address, the full subject and body and the body's sha256. The MIME
  message is built by `build_mime` from the snapshotted arguments alone —
  To, Cc, Subject, In-Reply-To/References, text/plain UTF-8 base64; no From
  (Gmail sets the account), no Bcc, no Date/Message-ID (Gmail adds them).
- `calendar_create_event`: primary calendar, one-line title, start < end,
  < 14 days, optional location/description, ≤ 20 attendee addresses. The
  dialog states whether Google will e-mail invitations (`sendUpdates=all`
  with attendees, `none` without).

Deliberately NOT offered: drafts, labels, trash/delete, attachments,
forwarding rules, other calendars, event updates/deletes, contacts.

## Credentials
- Env only, read at call time: `GOOGLE_OAUTH_CLIENT_ID`,
  `GOOGLE_OAUTH_CLIENT_SECRET` (Desktop-app OAuth client),
  `GOOGLE_OAUTH_REFRESH_TOKEN` (printed once by `manage.py connect_google`).
- Never in Postgres, the cache, a payload, an audit row or a log line
  (tested with canaries in `connectors/tests/test_google.py`). Access tokens
  live only in process memory, keyed by a fingerprint of the credentials.
- Scope maximum (code): `gmail.readonly`, `gmail.send`, `calendar.readonly`,
  `calendar.events`. Read scopes first; the send scopes only after a
  re-consent (`connect_google --send`), so send capability cannot exist
  before the owner grants it. A grant beyond the maximum is refused by
  `connect_google` and disables the connector at runtime (`scope_extra`).
- `invalid_grant` (revoked, 6 months unused, password change, > 100 tokens
  per client) → every tool `reconnect_required`, audited once, chat says
  "reconnect with `connect_google`".

## Sandbox and network
- Runs in the web (reads) and worker (writes, health beat) processes; no
  sidecar. Egress to `oauth2.googleapis.com`, `gmail.googleapis.com`,
  `www.googleapis.com` over HTTPS; no redirects followed, no proxies from the
  environment, response bodies capped at `CONNECTOR_RESPONSE_MAX_BYTES`.
- `connect_google` runs on the host: a one-shot HTTP server on
  `127.0.0.1:<random port>` for the loopback redirect, PKCE S256, `state`
  checked in constant time.

## Outbound data
- To Google: the owner's search query, message ids, agenda window, and —
  after a decision — exactly the approved mail or event.
- Personal data exposure is to Google only; results reach only
  `personal_data_allowed` providers (D5).

## Prompt-injection surface
- Mail subjects, senders, snippets and bodies are fully attacker-authored;
  calendar titles/locations may be too (invitations). Fenced, bounded,
  stripped; one read per turn; the router never sees connector text; every
  send or event creation needs a web decision on the exact payload (R2).

## Logging
- `connector.read`, `connector.write.*`, `connector.scope_mismatch`,
  `connector.reconnect_required`, `connector.binding.*` (governance table).
- `httpx`/`httpcore` loggers are WARNING-only (settings `LOGGING`): their
  INFO lines would carry request URLs with the owner's query and ids. Bridge
  log lines name the endpoint family and exception class only.

## Review
- Phase 5 branch 8 (2026-09-28): implementation; independent Sonnet 5
  review pending. Re-review on any scope, tool or pin change.
