"""A `node_voice` token reaches exactly the Phase 11 D4 list (release gate
"the node voice credential accepted outside its surfaces").

Walks every routed DRF view with a valid voice token: every route outside
the list answers 401/403 and the token is never accepted there
(`last_used_at` stays empty); the routes on the list are exactly those
whose authenticators accept the `node_voice` kind. Modelled on
test_hook_token_surfaces.py and test_node_credential_surfaces.py."""

import pytest

from accounts.authentication import PersonalAccessTokenAuthentication
from accounts.models import PersonalAccessToken
from accounts.tests.node_voice_helpers import bearer, make_owner, mint_token, pair_node
from accounts.views import NodeVoicePolicyView
from assistant.views import ConversationMessageListView
from config.tests.test_hook_token_surfaces import _routes
from voice.views import (
    LiveSessionCreateView,
    SpeechCreateView,
    TranscriptionCreateView,
    UsageReceiptView,
)

pytestmark = pytest.mark.django_db

# D4: the chat turn, the three voice endpoints, live sessions and the
# policy read -- nothing else.
REACHABLE = {
    ConversationMessageListView,
    TranscriptionCreateView,
    SpeechCreateView,
    UsageReceiptView,
    LiveSessionCreateView,
    NodeVoicePolicyView,
}


def _accepts_node_voice(auth):
    return any(
        issubclass(cls, PersonalAccessTokenAuthentication)
        and PersonalAccessToken.Kind.NODE_VOICE in cls().get_accepted_kinds()
        for cls in auth
    )


def test_exactly_the_d4_views_accept_the_node_voice_kind():
    accepting = {view for _, view, auth in _routes() if _accepts_node_voice(auth)}
    assert accepting == REACHABLE


@pytest.fixture
def voice_token():
    owner = make_owner()
    _, secret = pair_node(owner)
    raw = mint_token(secret)
    return raw, PersonalAccessToken.objects.get(kind=PersonalAccessToken.Kind.NODE_VOICE)


def test_a_voice_token_is_refused_on_every_other_route(voice_token):
    raw, token = voice_token
    client = bearer(raw)
    checked = 0
    for url, view_class, auth in _routes():
        if view_class in REACHABLE:
            continue
        for method in ("get", "post", "patch", "delete"):
            response = getattr(client, method)(url, {}, format="json")
            if auth:
                assert response.status_code in (401, 403), (method, url, response.status_code)
                checked += 1
    assert checked > 100  # the walk really covered the API
    token.refresh_from_db()
    assert token.last_used_at is None


def test_the_voice_token_is_accepted_on_its_routes(voice_token):
    raw, token = voice_token
    client = bearer(raw)
    for url, view_class, _ in _routes():
        if view_class not in REACHABLE:
            continue
        for method in ("get", "post"):
            response = getattr(client, method)(url, {}, format="json")
            # Authenticated: a 404/400/405/201/200, never a 401 or 403.
            assert response.status_code not in (401, 403), (method, url, response.status_code)
    token.refresh_from_db()
    assert token.last_used_at is not None


def test_the_storm_mute_reaches_the_chat_turn_and_every_voice_route(voice_token):
    """Phase 11 D12/D16: the 423 comes from the authenticator, so it reaches
    every route the token reaches -- including the chat turn on `assistant`
    (no `assistant -> voice` edge) -- except the mute-tolerant policy read."""
    from accounts.models import VoiceLiveMute
    from assistant.services import create_conversation

    raw, token = voice_token
    live = create_conversation(workspace=token.workspace, actor=token.user, channel="node_voice")
    VoiceLiveMute.objects.create(workspace=token.workspace, node_device=token.node_device)
    client = bearer(raw)
    chat = f"/api/v1/ai/conversations/{live.id}/messages"
    for method, url in (
        ("get", chat),
        ("post", chat),
        ("post", "/api/v1/voice/transcriptions"),
        ("post", "/api/v1/voice/speech"),
        ("get", "/api/v1/voice/receipt"),
        ("post", "/api/v1/voice/live/sessions"),
    ):
        response = getattr(client, method)(url, {}, format="json")
        assert response.status_code == 423, (method, url)
        assert response.json()["code"] == "voice.live_muted"
    policy = client.get(
        "/api/v1/auth/nodes/voice-policy",
        {"model_sha256": "a" * 64, "threshold": "0.5", "refractory_ms": 2000},
    )
    assert policy.status_code == 200 and "storm_muted" in policy.json()["reasons"]
