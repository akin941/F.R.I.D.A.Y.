"""Shared helpers for the Phase 11 node voice credential tests (branch 1):
pair a node the way the reference node does (a node pairing code through
`enroll_node_device`), mint its voice token over HTTP with the node secret,
and build bearer clients. No Redis is touched for revocation fan-out: the
callers patch `core.revocation._client` where a revoke runs."""

import json
import secrets

from rest_framework.test import APIClient

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice
from accounts.services import register_user

PASSWORD = "Str0ngDevPass!42"
MINT_URL = "/api/v1/auth/nodes/voice-token"
POLICY_URL = "/api/v1/auth/nodes/voice-policy"
VOICE_LIVE_URL = "/api/v1/auth/nodes/voice-live"
MUTE_URL = "/api/v1/auth/nodes/{}/voice-mute"
TRANSCRIPTIONS_URL = "/api/v1/voice/transcriptions"
SESSIONS_URL = "/api/v1/voice/live/sessions"
CHAT_URL = "/api/v1/ai/conversations/{}/messages"


def make_owner(email="owner@test.dev"):
    user = register_user(email=email, password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


def other_user(email="other@test.dev"):
    """A second, non-owner user (the installation has one owner) with a
    node device row of its own."""
    from accounts.models import TrustedDevice

    user = register_user(email=email, password=PASSWORD, name="Other")
    device = TrustedDevice.objects.create(
        user=user,
        name="Other Mac",
        kind=TrustedDevice.Kind.NODE,
        secret_hash=secrets.token_hex(32),
        enrolled_via=TrustedDevice.EnrolledVia.STRONG_CODE,
    )
    return user, device


def pair_node(owner, name="MacBook"):
    raw_code, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    return devices.enroll_node_device(raw_code=raw_code, node_name=name, client_ip="10.0.0.2")


def bearer(token, *, trigger="ptt"):
    """A bearer client. Every request carries `X-Friday-Voice-Trigger`
    (default `ptt`): the node voice channel requires it on the live-session
    and transcription routes (Phase 11 D11), every other surface ignores it.
    `trigger=None` sends none."""
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    if trigger is not None:
        headers["HTTP_X_FRIDAY_VOICE_TRIGGER"] = trigger
    client.credentials(**headers)
    return client


def mint(node_secret):
    """`POST /auth/nodes/voice-token` with the node secret; returns the
    response."""
    return bearer(node_secret).post(MINT_URL)


def mint_token(node_secret):
    response = mint(node_secret)
    assert response.status_code == 201, response.content
    return response.json()["token"]


def make_pat(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    return raw, token


def sse_events(response):
    events = []
    for chunk in b"".join(response.streaming_content).split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return events


class FakeRedis:
    """Records `publish` calls (the revocation fan-out)."""

    def __init__(self):
        self.published = []

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def close(self):
        pass
