"""Google bridge: Gmail + Calendar over native REST (ADR-010, brief D9).

No Google SDK: a handful of REST endpoints over `httpx`. The owner's OAuth
grant is created on the host by `manage.py connect_google` (installed-app
flow, PKCE S256, `127.0.0.1` loopback redirect); what Friday holds is
env-only:

- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` -- the Desktop-app
  OAuth client;
- `GOOGLE_OAUTH_REFRESH_TOKEN` -- the owner's grant.

The refresh token is read from the environment at call time and is never
stored in the database, the cache, a payload, an audit row or a log line.
Access tokens live only in this process's memory (`_TOKENS`), keyed by a
fingerprint of the credentials so a rotated refresh token never reuses an
old access token.

**Granted-scope verification.** Every token refresh returns the scopes the
owner actually granted. The manifest's maximum is `ALLOWED_SCOPES`; a grant
with any scope beyond it disables the whole connector (`scope_extra`), and
a tool whose required scope is not granted is unavailable (`scope_missing`)
-- so `mail_send` cannot exist before the owner re-consents with
`gmail.send`. Checked on enable and on the 15-minute health beat
(`NativeHealth`) and again inside every call, before any API request.
An `invalid_grant` (revoked, expired after 6 months unused, password
change, >100 tokens) surfaces as `reconnect_required`: "run
`manage.py connect_google` again".

`MailReader` / `CalendarReader` are the reader protocols D9 names: the
Gmail/Calendar implementations here are the only ones; another provider
would be another adapter behind the same shapes.
"""

import base64
import hashlib
import html
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from email.message import EmailMessage
from email.policy import SMTP
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.utils import timezone

from connectors.bridges import NativeHealth
from connectors.manifest import schema_hash
from connectors.mcp_client import CallResult, ConnectorError

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary"

ENV_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "GOOGLE_OAUTH_REFRESH_TOKEN"
CREDENTIAL_ENVS = (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN)

SCOPE_GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_CALENDAR_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
# Read scopes come first; the send scopes only after a re-consent (D9).
READ_SCOPES = (SCOPE_GMAIL_READ, SCOPE_CALENDAR_READ)
SEND_SCOPES = (SCOPE_GMAIL_SEND, SCOPE_CALENDAR_EVENTS)
# The code-defined maximum: a grant with anything else disables the connector.
ALLOWED_SCOPES = frozenset(READ_SCOPES + SEND_SCOPES)

API_VERSION = "gmail-v1+calendar-v3"

# The bridge's "server tools": one per REST operation. Each tool needs ANY
# one of its scopes. Their pins hash the request shape the bridge sends, so
# a bridge change that alters it must come with a manifest pin update.
TOOL_SCOPES = {
    "gmail.messages.search": frozenset({SCOPE_GMAIL_READ}),
    "gmail.messages.read": frozenset({SCOPE_GMAIL_READ}),
    "calendar.events.agenda": frozenset({SCOPE_CALENDAR_READ, SCOPE_CALENDAR_EVENTS}),
    "gmail.messages.send": frozenset({SCOPE_GMAIL_SEND}),
    "calendar.events.insert": frozenset({SCOPE_CALENDAR_EVENTS}),
}
_REQUEST_SHAPES = {
    "gmail.messages.search": {
        "method": "GET",
        "path": "messages",
        "params": ["q", "maxResults"],
        "then": {"format": "metadata", "headers": ["From", "Subject", "Date"]},
    },
    "gmail.messages.read": {"method": "GET", "path": "messages/{id}", "params": ["format=full"]},
    "calendar.events.agenda": {
        "method": "GET",
        "path": "events",
        "params": ["timeMin", "timeMax", "singleEvents", "orderBy", "maxResults", "timeZone"],
    },
    "gmail.messages.send": {
        "method": "POST",
        "path": "messages/send",
        "body": ["raw", "threadId"],
        "mime": ["To", "Cc", "Subject", "In-Reply-To", "References", "text/plain;utf-8;base64"],
    },
    "calendar.events.insert": {
        "method": "POST",
        "path": "events",
        "params": ["sendUpdates"],
        "body": ["summary", "description", "location", "start", "end", "attendees"],
    },
}
TOOL_PINS = {name: schema_hash(shape) for name, shape in _REQUEST_SHAPES.items()}

TOOL_OK = "ok"
SCOPE_MISSING = "scope_missing"
SCOPE_EXTRA = "scope_extra"
RECONNECT_REQUIRED = "reconnect_required"

MAIL_SEARCH_MAX = 20
SNIPPET_MAX_CHARS = 300
AGENDA_MAX_EVENTS = 50
# Refresh an access token this long before Google says it expires.
_TOKEN_SKEW_SECONDS = 60
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Test seam: tests install an `httpx.MockTransport`; production uses httpx's
# default transport.
_transport = None


def build_client(timeout):
    return httpx.Client(
        transport=_transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "friday"},
    )


# --- credentials and access tokens (process memory only) ---


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: float
    scopes: frozenset

    def __repr__(self):  # never print the token
        return f"AccessToken(scopes={sorted(self.scopes)!r})"


_TOKENS: dict = {}
_LOCK = threading.Lock()


def credentials():
    values = tuple(os.environ.get(name, "").strip() for name in CREDENTIAL_ENVS)
    if not all(values):
        raise ConnectorError("not_configured")
    return values


def _fingerprint(client_id, refresh_token):
    return hashlib.sha256(f"{client_id}\0{refresh_token}".encode()).hexdigest()


def clear_token_cache():
    with _LOCK:
        _TOKENS.clear()


def access_token(client, *, force=False):
    """A valid access token and the scopes granted with it, refreshing
    through the token endpoint when needed."""
    client_id, client_secret, refresh_token = credentials()
    key = _fingerprint(client_id, refresh_token)
    with _LOCK:
        cached = _TOKENS.get(key)
    if cached is not None and not force and cached.expires_at - _TOKEN_SKEW_SECONDS > time.time():
        return cached
    response = _send(
        client,
        "POST",
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    body = _json(response)
    if response.status_code != 200:
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_grant":
            logger.warning("google: refresh token rejected -- reconnect with connect_google")
            raise ConnectorError(RECONNECT_REQUIRED)
        if error in {"invalid_client", "unauthorized_client"}:
            raise ConnectorError("not_configured")
        raise ConnectorError("transport_error")
    value = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(value, str) or not value:
        raise ConnectorError("protocol_error")
    try:
        expires_in = int(body.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    scopes = frozenset(str(body.get("scope", "")).split())
    token = AccessToken(value=value, expires_at=time.time() + expires_in, scopes=scopes)
    with _LOCK:
        _TOKENS.clear()
        _TOKENS[key] = token
    return token


def scope_status(granted, required_any):
    """`ok`, `scope_extra` (a grant wider than the manifest maximum disables
    everything) or `scope_missing`."""
    if granted - ALLOWED_SCOPES:
        return SCOPE_EXTRA
    if not granted & required_any:
        return SCOPE_MISSING
    return TOOL_OK


# --- HTTP ---


def _send(client, method, url, **kwargs):
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        logger.warning("google %s %s: timeout", method, _endpoint(url))
        raise ConnectorError("timeout") from None
    except httpx.HTTPError as exc:
        logger.warning("google %s %s: transport (%s)", method, _endpoint(url), type(exc).__name__)
        raise ConnectorError("transport_error") from None
    if len(response.content) > settings.CONNECTOR_RESPONSE_MAX_BYTES:
        raise ConnectorError("protocol_error")
    return response


def _endpoint(url):
    # Log the host only: paths carry message ids, query strings the owner's
    # search -- both are the owner's data.
    return urlsplit(str(url)).hostname or "?"


def _json(response):
    try:
        return response.json()
    except ValueError:
        return None


class _Api:
    """Authenticated calls for one bridge operation; `state["sent"]` flips
    as a write request is handed to the transport."""

    def __init__(self, client, token, state):
        self.client = client
        self.token = token
        self.state = state

    def request(self, method, url, *, params=None, json=None, write=False):
        if write:
            self.state["sent"] = True
        response = _send(
            self.client,
            method,
            url,
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {self.token.value}"},
        )
        if response.status_code == 401:
            clear_token_cache()
        return response


class GoogleHTTPError(Exception):
    """Google answered with an error status. 4xx: the request was refused
    (nothing happened). 5xx on a write: the outcome is unknown."""

    def __init__(self, status):
        self.status = status
        super().__init__(status)


def _ok_json(response):
    if response.status_code >= 400:
        raise GoogleHTTPError(response.status_code)
    body = _json(response)
    if not isinstance(body, dict):
        raise ConnectorError("protocol_error")
    return body


# --- reader protocols (D9) ---


class MailReader(Protocol):
    def search(self, *, query: str, max_results: int) -> dict: ...

    def read(self, *, message_id: str | None, query: str) -> dict: ...


class CalendarReader(Protocol):
    def agenda(self, *, start_date, days: int) -> dict: ...


def _headers(message):
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in (message.get("payload") or {}).get("headers") or []
    }


def _b64url_decode(data):
    data = str(data or "")
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _html_to_text(markup):
    text = _STYLE_RE.sub(" ", markup)
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(_TAG_RE.sub(" ", text))


def _find_part(part, mime_type):
    if part.get("mimeType") == mime_type and (part.get("body") or {}).get("data"):
        return part
    for child in part.get("parts") or []:
        found = _find_part(child, mime_type)
        if found is not None:
            return found
    return None


def message_text(payload):
    """The text body: the first text/plain part, else the first text/html
    part reduced to text. Attachments are never read."""
    part = _find_part(payload or {}, "text/plain")
    if part is not None:
        return _b64url_decode(part["body"]["data"]).decode("utf-8", "replace")
    part = _find_part(payload or {}, "text/html")
    if part is not None:
        return _html_to_text(_b64url_decode(part["body"]["data"]).decode("utf-8", "replace"))
    return ""


class GmailReader:
    def __init__(self, api):
        self.api = api

    def _ids(self, query, limit):
        body = _ok_json(
            self.api.request(
                "GET", f"{GMAIL_API}/messages", params={"q": query, "maxResults": limit}
            )
        )
        ids = [str(m.get("id", "")) for m in body.get("messages") or []]
        return [i for i in ids if _ID_RE.match(i)][:limit]

    def search(self, *, query, max_results):
        limit = min(max_results, MAIL_SEARCH_MAX)
        messages = []
        for message_id in self._ids(query, limit):
            message = _ok_json(
                self.api.request(
                    "GET",
                    f"{GMAIL_API}/messages/{message_id}",
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "Date"),
                    ],
                )
            )
            headers = _headers(message)
            messages.append(
                {
                    "id": message_id,
                    "thread_id": str(message.get("threadId", "")),
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                    "snippet": html.unescape(str(message.get("snippet", "")))[:SNIPPET_MAX_CHARS],
                }
            )
        return {"messages": messages, "count": len(messages)}

    def read(self, *, message_id, query):
        if not message_id:
            ids = self._ids(query, 1)
            if not ids:
                return {"message": None}
            message_id = ids[0]
        message = _ok_json(
            self.api.request("GET", f"{GMAIL_API}/messages/{message_id}", params={"format": "full"})
        )
        headers = _headers(message)
        body = message_text(message.get("payload"))
        limit = settings.GOOGLE_MAIL_BODY_MAX_CHARS
        return {
            "message": {
                "id": message_id,
                "thread_id": str(message.get("threadId", "")),
                "message_id_header": headers.get("message-id", ""),
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "cc": headers.get("cc", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "body": body[:limit],
                "body_truncated": len(body) > limit,
            }
        }


class GoogleCalendarReader:
    def __init__(self, api):
        self.api = api

    def agenda(self, *, start_date, days):
        zone = ZoneInfo(settings.TIME_ZONE)
        start = start_date or timezone.localdate()
        time_min = datetime.combine(start, dt_time.min, tzinfo=zone)
        time_max = time_min + timedelta(days=days)
        body = _ok_json(
            self.api.request(
                "GET",
                f"{CALENDAR_API}/events",
                params={
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": AGENDA_MAX_EVENTS,
                    "timeZone": settings.TIME_ZONE,
                },
            )
        )
        events = []
        for item in body.get("items") or []:
            if item.get("status") == "cancelled":
                continue
            start_at, end_at = item.get("start") or {}, item.get("end") or {}
            events.append(
                {
                    "summary": str(item.get("summary", "")),
                    "start": str(start_at.get("dateTime") or start_at.get("date") or ""),
                    "end": str(end_at.get("dateTime") or end_at.get("date") or ""),
                    "all_day": "date" in start_at and "dateTime" not in start_at,
                    "location": str(item.get("location", "")),
                    "attendees": len(item.get("attendees") or []),
                }
            )
        return {
            "from": time_min.isoformat(),
            "to": time_max.isoformat(),
            "events": events[:AGENDA_MAX_EVENTS],
        }


# --- writes: built only from the snapshotted payload arguments ---


def build_mime(arguments):
    """The RFC 5322 message for `mail_send`, from the approved arguments and
    nothing else: no From (Gmail sets the authenticated account), no Bcc,
    no Date/Message-ID (Gmail adds them). The body is text/plain UTF-8,
    base64-encoded; its sha256 is the one the decision dialog showed."""
    message = EmailMessage(policy=SMTP)
    message["To"] = ", ".join(arguments["to"])
    if arguments.get("cc"):
        message["Cc"] = ", ".join(arguments["cc"])
    message["Subject"] = arguments["subject"]
    if arguments.get("in_reply_to"):
        message["In-Reply-To"] = arguments["in_reply_to"]
        message["References"] = arguments["in_reply_to"]
    message.set_content(arguments["body"], subtype="plain", charset="utf-8", cte="base64")
    return message.as_bytes()


def event_body(arguments):
    body = {
        "summary": arguments["summary"],
        "start": {"dateTime": arguments["start"]},
        "end": {"dateTime": arguments["end"]},
        "attendees": [{"email": address} for address in arguments.get("attendees") or []],
    }
    if arguments.get("description"):
        body["description"] = arguments["description"]
    if arguments.get("location"):
        body["location"] = arguments["location"]
    return body


def _mail_send(api, arguments):
    raw = base64.urlsafe_b64encode(build_mime(arguments)).decode("ascii")
    body = {"raw": raw}
    if arguments.get("thread_id"):
        body["threadId"] = arguments["thread_id"]
    result = _ok_json(api.request("POST", f"{GMAIL_API}/messages/send", json=body, write=True))
    return {"id": str(result.get("id", "")), "thread_id": str(result.get("threadId", ""))}


def _calendar_insert(api, arguments):
    send_updates = "all" if arguments.get("attendees") else "none"
    result = _ok_json(
        api.request(
            "POST",
            f"{CALENDAR_API}/events",
            params={"sendUpdates": send_updates},
            json=event_body(arguments),
            write=True,
        )
    )
    return {"id": str(result.get("id", "")), "status": str(result.get("status", ""))}


def _parse_date(value):
    return datetime.fromisoformat(value).date() if value else None


_HANDLERS = {
    "gmail.messages.search": lambda api, a: GmailReader(api).search(
        query=a.get("query") or "in:inbox", max_results=int(a.get("max_results") or 10)
    ),
    "gmail.messages.read": lambda api, a: GmailReader(api).read(
        message_id=a.get("message_id") or None, query=a.get("query") or "in:inbox"
    ),
    "calendar.events.agenda": lambda api, a: GoogleCalendarReader(api).agenda(
        start_date=_parse_date(a.get("start_date")), days=int(a.get("days") or 1)
    ),
    "gmail.messages.send": _mail_send,
    "calendar.events.insert": _calendar_insert,
}
_WRITES = frozenset({"gmail.messages.send", "calendar.events.insert"})


class GoogleBridge:
    """The `BRIDGE` of `connectors.manifests.google`."""

    server_name = "google"

    def call_tool(self, manifest, *, server_tool, arguments, schema_sha256, timeout):
        if server_tool not in _HANDLERS:
            raise ConnectorError("tool_missing")
        if TOOL_PINS[server_tool] != schema_sha256:
            raise ConnectorError("pin_mismatch")
        state = {"sent": False}
        try:
            with build_client(timeout) as client:
                token = access_token(client)
                # Scopes re-checked inside every call, before any API request.
                if scope_status(token.scopes, TOOL_SCOPES[server_tool]) != TOOL_OK:
                    raise ConnectorError("scope_mismatch")
                try:
                    structured = _HANDLERS[server_tool](_Api(client, token, state), arguments)
                except (ValueError, TypeError, KeyError, AttributeError):
                    # A malformed Google response (bad base64, wrong shapes):
                    # closed vocabulary, never the exception text.
                    logger.warning("google %s: malformed response", server_tool)
                    raise ConnectorError("protocol_error") from None
                except GoogleHTTPError as exc:
                    if server_tool in _WRITES and exc.status >= 500:
                        # The request reached Google; whether it acted is unknown.
                        raise ConnectorError("protocol_error") from None
                    return CallResult(
                        texts=(f"Google refused the request (HTTP {exc.status}).",),
                        structured=None,
                        is_error=True,
                        dropped_blocks=0,
                        listing=None,
                    )
        except ConnectorError as exc:
            exc.sent = state["sent"]
            raise
        return CallResult(
            texts=(), structured=structured, is_error=False, dropped_blocks=0, listing=None
        )

    def check_health(self, manifest, *, timeout):
        try:
            with build_client(timeout) as client:
                token = access_token(client, force=True)
        except ConnectorError as exc:
            return NativeHealth(self.server_name, API_VERSION, error=exc.code)
        tools = {
            policy.name: scope_status(
                token.scopes, TOOL_SCOPES.get(policy.server_tool, frozenset())
            )
            for policy in manifest.tools
        }
        return NativeHealth(
            self.server_name,
            API_VERSION,
            tools=tools,
            details={
                "granted_scopes": sorted(token.scopes),
                "extra_scopes": sorted(token.scopes - ALLOWED_SCOPES),
            },
        )
