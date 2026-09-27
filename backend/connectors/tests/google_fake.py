"""A fake Google (token endpoint, Gmail, Calendar) behind `httpx.MockTransport`
-- the Google bridge runs its real code path against it; no network. Every
request is recorded so tests can assert "exactly once" and "never"."""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs

import httpx

from connectors.bridges import google

CLIENT_ID = "jarvis-test.apps.googleusercontent.com"
# Canaries: credential values that must never show up anywhere Jarvis writes.
CLIENT_SECRET_CANARY = "GOCSPX-CLIENT-SECRET-CANARY"
REFRESH_TOKEN_CANARY = "1//REFRESH-TOKEN-CANARY-0123456789"
ACCESS_TOKEN_CANARY = "ya29.ACCESS-TOKEN-CANARY"
NEW_REFRESH_TOKEN = "1//NEW-REFRESH-TOKEN-FROM-CONSENT"

ALL_SCOPES = google.READ_SCOPES + google.SEND_SCOPES


def b64url(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


@dataclass
class FakeGoogle:
    scopes: tuple = google.READ_SCOPES
    token_error: str = ""
    requests: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    inserted: list = field(default_factory=list)
    send_status: int = 200
    body_text: str = "Merhaba, yarınki toplantı 10:00'da. Ajanda: bütçe."
    body_html: str = ""
    # authorization_code exchange (connect_google)
    expected_code: str = "AUTH-CODE-1"
    code_challenge: str = ""
    redirect_uri: str = ""
    consent_scopes: tuple = ALL_SCOPES
    events: list = field(
        default_factory=lambda: [
            {
                "summary": "Ekip toplantısı",
                "start": {"dateTime": "2026-09-28T10:00:00+03:00"},
                "end": {"dateTime": "2026-09-28T11:00:00+03:00"},
                "location": "Ofis",
                "attendees": [{"email": "ahmet@example.com"}],
            },
            {"summary": "İptal", "status": "cancelled", "start": {}, "end": {}},
        ]
    )

    def transport(self):
        return httpx.MockTransport(self.handler)

    def _message(self, message_id, fmt):
        headers = [
            {"name": "From", "value": "Ahmet Yılmaz <ahmet@example.com>"},
            {"name": "To", "value": "owner@example.com"},
            {"name": "Subject", "value": "Yarınki toplantı"},
            {"name": "Date", "value": "Sun, 27 Sep 2026 21:00:00 +0300"},
            {"name": "Message-ID", "value": "<abc123@mail.example.com>"},
        ]
        payload = {"mimeType": "multipart/alternative", "headers": headers, "parts": []}
        if self.body_text:
            payload["parts"].append(
                {"mimeType": "text/plain", "body": {"data": b64url(self.body_text)}}
            )
        if self.body_html:
            payload["parts"].append(
                {"mimeType": "text/html", "body": {"data": b64url(self.body_html)}}
            )
        return {
            "id": message_id,
            "threadId": "t" + message_id,
            "snippet": "Merhaba, yarınki toplantı &amp; ajanda",
            "payload": payload,
        }

    def handler(self, request):
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": request.content.decode("utf-8", "replace"),
            }
        )
        url = request.url
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            return self._token(request)
        if request.headers.get("authorization") != f"Bearer {ACCESS_TOKEN_CANARY}":
            return httpx.Response(401, json={"error": {"code": 401}})
        path = url.path
        if path == "/gmail/v1/users/me/messages" and request.method == "GET":
            limit = int(url.params.get("maxResults", "10"))
            ids = ["m1", "m2", "m3"][:limit]
            return httpx.Response(200, json={"messages": [{"id": i} for i in ids]})
        if path.startswith("/gmail/v1/users/me/messages/") and request.method == "GET":
            message_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self._message(message_id, url.params.get("format")))
        if path == "/gmail/v1/users/me/messages/send" and request.method == "POST":
            self.sent.append(json.loads(request.content))
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"error": {}})
            return httpx.Response(200, json={"id": "sent-1", "threadId": "t-1"})
        if path == "/calendar/v3/calendars/primary/events" and request.method == "GET":
            return httpx.Response(200, json={"items": self.events})
        if path == "/calendar/v3/calendars/primary/events" and request.method == "POST":
            self.inserted.append({"params": dict(url.params), "body": json.loads(request.content)})
            return httpx.Response(200, json={"id": "ev-1", "status": "confirmed"})
        return httpx.Response(404, json={})

    def _token(self, request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if self.token_error:
            return httpx.Response(400, json={"error": self.token_error})
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET_CANARY:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") != REFRESH_TOKEN_CANARY:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN_CANARY,
                    "expires_in": 3599,
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )
        if form.get("grant_type") == "authorization_code":
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode("ascii")).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if (
                form.get("code") != self.expected_code
                or challenge != self.code_challenge
                or form.get("redirect_uri") != self.redirect_uri
            ):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN_CANARY,
                    "refresh_token": NEW_REFRESH_TOKEN,
                    "expires_in": 3599,
                    "scope": " ".join(self.consent_scopes),
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def api_requests(self):
        return [r for r in self.requests if "oauth2.googleapis.com" not in r["url"]]
