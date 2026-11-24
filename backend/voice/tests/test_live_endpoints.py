"""Phase 11 branch 1 (brief D4, D5): the voice endpoints over the node
voice channel and `POST /api/v1/voice/live/sessions`.

- role x endpoint over the five Phase 4 principals plus `hook`, `node`
  (the node secret) and `node_voice` credentials;
- a firm-workspace conversation, or a personal one not opened via
  `node_voice`, is a 404 with a voice token (the web session still reaches
  it);
- sessions: `opened_via="node_voice"`, reuse inside the window (an empty
  one too), a new one after it, never another channel's or another
  workspace's conversation, the throttle;
- the "done when": a voice token minted with the node secret transcribes a
  fixture WAV, posts a voice turn and synthesizes its reply on the fake
  stack, and a rotation mid-turn never drops a request.

No real speech model: FakeAdapter through the real gateway."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import PersonalAccessToken
from accounts.tests.node_voice_helpers import (
    CHAT_URL,
    SESSIONS_URL,
    bearer,
    make_pat,
    mint_token,
    pair_node,
    sse_events,
)
from ai_core.adapters.fake import FAKE_TRANSCRIPT
from ai_core.models import ModelUsage
from assistant.models import Conversation, Message
from assistant.services import create_conversation
from voice.tests.conftest import SPEECH, TRANSCRIPTIONS, make_wav
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

RECEIPT = "/api/v1/voice/receipt"


@pytest.fixture
def node(owner):
    return pair_node(owner)


@pytest.fixture
def voice(node):
    _, secret = node
    return bearer(mint_token(secret))


@pytest.fixture
def live(owner, workspace):
    return create_conversation(workspace=workspace, actor=owner, channel="node_voice")


def _upload(client, conversation_id, **extra):
    return client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation_id),
            "audio": SimpleUploadedFile("u.wav", make_wav(seconds=1), content_type="audio/wav"),
        },
        format="multipart",
        **extra,
    )


def _speak(client, conversation_id):
    return client.post(
        SPEECH, {"conversation": str(conversation_id), "text": "Merhaba."}, format="json"
    )


def _receipt(client, conversation_id):
    return client.get(RECEIPT, {"conversation": str(conversation_id)})


ENDPOINTS = {"stt": _upload, "tts": _speak, "receipt": _receipt}


@pytest.fixture
def principals(owner, workspace, node, voice, principal_client):
    _, secret = node

    def make(principal):
        if principal == "owner-pat-hook":
            return bearer(make_pat(owner, workspace, PersonalAccessToken.Kind.HOOK)[0])
        if principal == "node-secret":
            return bearer(secret)
        if principal == "node-voice":
            return voice
        return principal_client(principal)

    return make


MATRIX = [
    ("owner-web", 200),
    ("owner-pat-cli", 403),
    ("owner-pat-openai", 403),
    ("owner-pat-hook", 403),
    ("node-secret", 401),
    ("node-voice", 200),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_voice_endpoints_role_matrix(
    principals, live, speech_providers, endpoint, principal, expected
):
    response = ENDPOINTS[endpoint](principals(principal), live.id)
    assert response.status_code == expected, response.content
    if expected == 403 and principal.startswith("owner-pat"):
        code = response.json()["code"]
        # A `cli` PAT authenticates as `cli` and meets the permission class;
        # the other kinds are refused by the authenticator itself.
        assert code == (
            "channel_forbidden" if principal == "owner-pat-cli" else "token_kind_forbidden"
        )


@pytest.mark.parametrize(("principal", "expected"), [*MATRIX[:5], ("node-voice", 201), *MATRIX[6:]])
def test_sessions_role_matrix(principals, principal, expected):
    if principal == "owner-web":
        expected = 403  # node voice only
    response = principals(principal).post(SESSIONS_URL, {}, format="json")
    assert response.status_code == expected, response.content
    if principal == "owner-pat-cli":
        assert response.json()["code"] == "channel_forbidden"
    assert Conversation.objects.filter(opened_via="node_voice").count() == (
        1 if expected == 201 else 0
    )


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
def test_a_conversation_not_opened_by_a_live_session_is_404(
    owner, workspace, voice, speech_providers, web_client, endpoint
):
    web = Conversation.objects.create(workspace=workspace, created_by=owner)
    facade = create_conversation(workspace=workspace, actor=owner, channel="openai")
    for conversation in (web, facade):
        assert ENDPOINTS[endpoint](voice, conversation.id).status_code == 404
    assert ENDPOINTS[endpoint](web_client(owner), web.id).status_code == 200
    assert (
        not ModelUsage.objects.filter(purpose__startswith="voice.", success=True)
        .exclude(workspace=workspace)
        .exists()
    )


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
def test_a_firm_conversation_is_404_with_a_voice_token(owner, voice, speech_providers, endpoint):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    conversation = create_conversation(workspace=firm, actor=owner, channel="node_voice")
    assert ENDPOINTS[endpoint](voice, conversation.id).status_code == 404


# --- sessions ---------------------------------------------------------------------


def open_session(client):
    response = client.post(SESSIONS_URL, {}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def test_a_session_opens_a_live_conversation(owner, workspace, voice):
    body = open_session(voice)
    assert set(body) == {"conversation_id", "session_id", "opened_at"}
    conversation = Conversation.objects.get(pk=body["conversation_id"])
    assert conversation.opened_via == "node_voice"
    assert conversation.workspace == workspace and conversation.created_by == owner
    assert conversation.title.startswith("Canlı ses — ")


def test_an_empty_live_conversation_is_reused_not_duplicated(voice):
    first = open_session(voice)
    second = open_session(voice)
    assert first["conversation_id"] == second["conversation_id"]
    assert first["session_id"] != second["session_id"]
    assert Conversation.objects.filter(opened_via="node_voice").count() == 1


def test_reuse_follows_the_last_activity(owner, workspace, voice, frozen_now):
    t0 = frozen_now(timezone.now())
    first = open_session(voice)["conversation_id"]
    conversation = Conversation.objects.get(pk=first)
    Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="x",
        created_by=owner,
        channel="node_voice",
    )
    Message.objects.filter(conversation=conversation).update(created_at=t0 + timedelta(minutes=20))
    # 45 min after creation, 25 min after the last message: reused.
    frozen_now(t0 + timedelta(minutes=45))
    assert open_session(voice)["conversation_id"] == first
    # 31 min after the last message: a new one.
    frozen_now(t0 + timedelta(minutes=51))
    assert open_session(voice)["conversation_id"] != first


def test_another_channels_conversation_is_never_reused(owner, workspace, voice, frozen_now):
    t0 = frozen_now(timezone.now())
    live_id = open_session(voice)["conversation_id"]
    frozen_now(t0 + timedelta(minutes=1))
    # More recent, but opened on the web / by the facade.
    Conversation.objects.create(workspace=workspace, created_by=owner)
    create_conversation(workspace=workspace, actor=owner, channel="openai")
    assert open_session(voice)["conversation_id"] == live_id


def test_another_workspaces_live_conversation_is_never_reused(owner, voice):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    foreign = create_conversation(workspace=firm, actor=owner, channel="node_voice")
    assert open_session(voice)["conversation_id"] != str(foreign.pk)


def test_the_session_endpoint_is_throttled(voice, monkeypatch):
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**ScopedRateThrottle.THROTTLE_RATES, "node_voice_session": "2/hour"},
    )
    open_session(voice)
    open_session(voice)
    assert voice.post(SESSIONS_URL, {}, format="json").status_code == 429


def test_the_opening_is_audited_by_assistant(voice):
    from audit.models import AuditLog

    conversation_id = open_session(voice)["conversation_id"]
    row = AuditLog.objects.get(action="assistant.conversation.created")
    assert row.resource_id == conversation_id
    assert row.metadata["channel"] == "node_voice"


# --- done when: a whole live turn on the fake stack --------------------------------


def test_a_live_turn_end_to_end_with_a_rotation_mid_turn(
    owner, workspace, node, speech_providers, chat_setup
):
    _, secret = node
    old = bearer(mint_token(secret))
    session = open_session(old)
    conversation_id = session["conversation_id"]

    stt = _upload(old, conversation_id)
    assert stt.status_code == 200
    assert stt.json()["text"] == FAKE_TRANSCRIPT

    # The host rotates between STT and the chat turn: the old token keeps
    # its 30 s grace, the turn goes out with the new one.
    new = bearer(mint_token(secret))
    turn = new.post(
        CHAT_URL.format(conversation_id),
        {"content": stt.json()["text"], "input_mode": "text"},
        format="json",
    )
    assert turn.status_code == 200
    events = sse_events(turn)
    assert events[-1]["type"] == "done"
    reply = "".join(event["text"] for event in events if event["type"] == "chunk")

    # A request still in flight with the old token succeeds.
    assert _speak(old, conversation_id).status_code == 200
    tts = new.post(
        SPEECH,
        {"conversation": conversation_id, "text": (reply or "Tamam.")[:400]},
        format="json",
    )
    assert tts.status_code == 200 and tts.content[:4] == b"RIFF"

    user_message = Message.objects.get(conversation_id=conversation_id, role="user")
    assert user_message.channel == "node_voice"
    assert user_message.input_mode == "voice"  # forced, whatever the body said
    assert ModelUsage.objects.filter(purpose="voice.stt", workspace=workspace).count() == 1
    assert ModelUsage.objects.filter(purpose="voice.tts", workspace=workspace).count() == 2


def test_a_cli_pat_cannot_open_sessions_or_reach_live_voice(owner, workspace, live):
    raw, _ = make_pat(owner, workspace, PersonalAccessToken.Kind.CLI)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert client.post(SESSIONS_URL, {}, format="json").status_code == 403
    assert _speak(client, live.id).status_code == 403
