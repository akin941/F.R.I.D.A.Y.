# Runbook — Google mail/calendar connector (owner)

Phase 5 branch 8 (brief D9, R4). Jarvis reads the owner's Gmail and primary
Google Calendar and — after a web decision on the exact payload — sends a
mail or creates an event. Governance: `docs/mcp/google.md`.

Everything below is done once by the owner on the host. No step runs through
the web app except switching the connector on.

## 1. Create the OAuth client (Google Cloud Console)

1. Create (or pick) a Google Cloud project for Jarvis only.
2. **APIs & Services → Library:** enable **Gmail API** and **Google Calendar
   API**.
3. **Google Auth Platform → Branding / Audience (OAuth consent screen):**
   - User type **External** (a personal Google account has no Internal).
   - App name e.g. "Jarvis (personal)", your own address as support and
     developer contact.
   - **Data access (scopes):** add `…/auth/gmail.readonly`,
     `…/auth/calendar.readonly`, and — only if you will let Jarvis send —
     `…/auth/gmail.send`, `…/auth/calendar.events`. Nothing else: a grant
     with any other scope is refused by `connect_google` and disables the
     connector.
4. **Publishing status: "In production"** (Audience → Publish app). Do
   **not** stay in "Testing": Testing refresh tokens expire after 7 days.
   Do **not** submit for verification: an unverified app used only by its
   own developer falls under Google's personal-use exemption (the restricted
   `gmail.readonly` scope included); no security assessment, cap of 100
   users — you are the only one.
5. **Clients → Create client → Application type "Desktop app".** Copy the
   client id and the client secret now — the secret is shown only once.
   A client unused for 6 months is deleted by Google; the connector's
   15-minute health beat counts as use.

## 2. Connect (host shell)

The flow is the installed-app flow: PKCE S256 with a loopback redirect on
`127.0.0.1` and a random port (out-of-band copy/paste has been blocked by
Google since 2023). Run it on the machine whose browser you will use:

```bash
cd backend
export GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
export GOOGLE_OAUTH_CLIENT_SECRET=...        # env, never an argument
uv run python manage.py connect_google        # read scopes only
# later, to allow sending:
uv run python manage.py connect_google --send # read + send scopes
```

(`--no-browser` only prints the URL; `--timeout` defaults to 300 s.)

- The browser shows **"Google hasn't verified this app"**: this is expected
  for a personal unverified app. Click **Advanced → Go to Jarvis
  (unsafe)** — you are the developer. Then allow the listed scopes. You may
  untick a scope; the tools that need it stay disabled and the command
  lists what was declined.
- The command prints the granted scopes and the refresh token **once**:
  `GOOGLE_OAUTH_REFRESH_TOKEN=1//…`. Put it — with the client id and secret —
  in `deploy/docker/.env` (compose passes the three to web and worker), then
  `make up`. It is never stored in the database, cache, audit or logs;
  losing it only means running `connect_google` again.
- **Adding the send scopes later re-consents the full set** (installed apps
  get no incremental authorization) and yields a **new** refresh token:
  replace the old line in `.env`. Until then `mail_send` and
  `calendar_create_event` are refused at request time.

## 3. Switch it on (web)

From a trusted web session, as the installation owner, in the personal
workspace (it cannot bind to a firm workspace): `PUT
/api/v1/connectors/bindings/google` with `{"workspace": "<personal
workspace id>", "enabled": true}` (the connectors screen arrives with Phase
5 branch 9). The health check
runs right away and then every 15 minutes: it refreshes the access token and
compares the granted scopes with the manifest. Tool statuses on the binding:
`ok`, `scope_missing` (not granted), `scope_extra` (grant wider than the
manifest — every tool off, audited), `reconnect_required`, `unchecked`.

Chat reads (`mail_search`, `mail_read`, `calendar_agenda`) are web-only and
need a local model allowed to see personal data
(`manage.py set_provider_personal_data`, e.g. `ollama-local`); with none,
nothing is read. A reading conversation becomes private.

## 4. Reconnect

When a chat read answers "Google bağlantısının yetkisi düşmüş…" or the
binding shows `reconnect_required`, Google rejected the refresh token
(`invalid_grant`): revoked at myaccount.google.com/permissions, unused for 6
months, a password change (Gmail scopes), or more than 100 tokens issued for
this client. Run step 2 again, replace `GOOGLE_OAUTH_REFRESH_TOKEN`, `make
up`. The next health check (≤ 15 min, or toggle the binding off and on)
clears it. `not_configured` means an env var is missing or the client id /
secret is wrong (`invalid_client`).

If `connect_google` says Google returned **no refresh token**, remove the
old grant at myaccount.google.com/permissions and run it again.

## 5. Revoke

myaccount.google.com/permissions → Jarvis → remove access; then delete the
three `GOOGLE_OAUTH_*` lines and switch the binding off.
