"""`manage.py connect_google` -- the owner's Google grant, created on the host
(brief D9, R4; runbook `docs/runbooks/google-connector.md`).

Installed-app ("Desktop app") OAuth flow with PKCE S256 on a `127.0.0.1`
loopback redirect bound to a random free port -- no callback route through
the BFF, no out-of-band copy/paste (blocked by Google since 2023). The host
shell is the root of trust, as for `issue_pairing_code`.

1. Reads `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` from the
   environment (never from an argument: shell history).
2. Prints the consent URL (and opens the browser unless `--no-browser`),
   then waits for exactly one loopback redirect whose `state` matches.
3. Exchanges the code (with the PKCE verifier) at the token endpoint and
   verifies the granted scopes against the manifest maximum: a wider grant
   is refused; a narrower one (a declined scope) is reported.
4. Prints the refresh token ONCE, as the `.env` line to paste. It is never
   written to the database, the cache, a file or a log.

Read scopes by default; `--send` asks for the read AND send scopes
(installed apps get no incremental authorization, so adding `gmail.send`
re-consents the full set and yields a new refresh token that replaces the
old one in `.env`).
"""

import base64
import hashlib
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from django.core.management.base import BaseCommand, CommandError

from connectors.bridges import google

# Test seam: how the consent URL is opened.
open_browser = webbrowser.open


def pkce_pair():
    """(verifier, S256 challenge) per RFC 7636: 64 random bytes ->
    86-char base64url verifier (within 43..128)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(*, client_id, redirect_uri, scopes, challenge, state):
    return (
        google.AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                # A refresh token every time, and the full consent screen
                # (so a re-run after adding scopes re-consents the set).
                "access_type": "offline",
                "prompt": "consent",
            }
        )
    )


class _Redirect:
    def __init__(self):
        self.params = None
        self.done = threading.Event()


def _handler(redirect):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            parts = urlsplit(self.path)
            if parts.path != "/":
                self.send_response(404)
                self.end_headers()
                return
            if redirect.params is None:
                redirect.params = {k: v[0] for k, v in parse_qs(parts.query).items()}
                redirect.done.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Jarvis: bu sekmeyi kapatabilirsin.\n")

        def log_message(self, *args):  # the query string carries the code
            return

    return Handler


def wait_for_redirect(server, redirect, timeout):
    deadline = monotonic() + timeout
    server.timeout = 0.5
    while not redirect.done.is_set():
        if monotonic() > deadline:
            raise CommandError("No redirect from Google before the timeout; nothing changed.")
        server.handle_request()
    return redirect.params


def exchange_code(*, client_id, client_secret, code, verifier, redirect_uri, timeout=30):
    with google.build_client(timeout) as client:
        try:
            response = client.post(
                google.TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise CommandError(f"Token exchange failed ({type(exc).__name__}).") from None
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not isinstance(body, dict):
        error = body.get("error", "") if isinstance(body, dict) else ""
        raise CommandError(f"Token exchange refused by Google ({error or response.status_code}).")
    return body


class Command(BaseCommand):
    help = (
        "Connect the owner's Google account (Gmail + Calendar): PKCE loopback OAuth on "
        "127.0.0.1; prints the refresh token once for .env."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            action="store_true",
            help="Also grant gmail.send and calendar.events (re-consents the full set).",
        )
        parser.add_argument("--no-browser", action="store_true", help="Only print the URL.")
        parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait.")

    def handle(self, *args, **options):
        client_id = os.environ.get(google.ENV_CLIENT_ID, "").strip()
        client_secret = os.environ.get(google.ENV_CLIENT_SECRET, "").strip()
        if not client_id or not client_secret:
            raise CommandError(
                f"Set {google.ENV_CLIENT_ID} and {google.ENV_CLIENT_SECRET} in the environment "
                "(Desktop-app OAuth client; see docs/runbooks/google-connector.md)."
            )
        scopes = google.READ_SCOPES + (google.SEND_SCOPES if options["send"] else ())
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        redirect = _Redirect()
        server = HTTPServer(("127.0.0.1", 0), _handler(redirect))
        try:
            redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
            url = authorization_url(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=scopes,
                challenge=challenge,
                state=state,
            )
            self.stdout.write("Open this URL, sign in as the owner and allow access:\n")
            self.stdout.write(url + "\n")
            if not options["no_browser"]:
                open_browser(url)
            params = wait_for_redirect(server, redirect, options["timeout"])
        finally:
            server.server_close()
        if not secrets.compare_digest(params.get("state", ""), state):
            raise CommandError("The redirect's state does not match; nothing changed.")
        if params.get("error"):
            raise CommandError(f"Google returned an error ({params['error']}); nothing changed.")
        code = params.get("code", "")
        if not code:
            raise CommandError("The redirect carried no authorization code; nothing changed.")
        body = exchange_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            verifier=verifier,
            redirect_uri=redirect_uri,
        )
        granted = frozenset(str(body.get("scope", "")).split())
        extra = granted - google.ALLOWED_SCOPES
        if extra:
            raise CommandError(
                "Google granted scopes beyond the connector manifest "
                f"({', '.join(sorted(extra))}); the token is discarded. Revoke the grant at "
                "https://myaccount.google.com/permissions and run connect_google again."
            )
        refresh_token = body.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CommandError(
                "Google returned no refresh token. Revoke the grant at "
                "https://myaccount.google.com/permissions and run connect_google again."
            )
        declined = [scope for scope in scopes if scope not in granted]
        if declined:
            self.stdout.write(
                self.style.WARNING(
                    "Declined on the consent screen (the tools needing them stay disabled): "
                    + ", ".join(declined)
                )
            )
        google.clear_token_cache()
        self.stdout.write(
            self.style.SUCCESS(
                "Granted: " + ", ".join(sorted(granted)) + "\n"
                "Put this line in .env (replacing any previous one) and restart web and "
                "worker. It is shown only this once:\n"
            )
        )
        self.stdout.write(f"{google.ENV_REFRESH_TOKEN}={refresh_token}\n")
