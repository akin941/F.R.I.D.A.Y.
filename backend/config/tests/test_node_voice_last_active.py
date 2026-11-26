"""Phase 13 D6: a `node_voice` transcription request marks the token's node
as the owner's last active PC, through the callable `config/urls.py`
injects into the `voice` routes (`voice` imports nothing of `nodes`). A
web-session transcription, another route, or a request refused before its
conversation is found bumps nothing."""

import io
import wave

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import get_resolver

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from accounts.tests.node_voice_helpers import bearer, mint_token
from ai_core.models import ModelProvider
from assistant.services import create_conversation
from nodes.activity import mark_node_active
from nodes.models import Node
from nodes.services import pair_node
from voice.views import TranscriptionCreateView
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db
TRANSCRIPTIONS = "/api/v1/voice/transcriptions"


def _wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x01\x00" * 16_000)
    return buffer.getvalue()


@pytest.fixture
def world(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    settings.VOICE_STT_LANGUAGE = "tr"
    ModelProvider.objects.create(
        name="fake-stt-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.STT,
        model_id="fake-stt",
        personal_data_allowed=True,
    )
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    nodes = []
    for name in ("MacBook", "Masaüstü"):
        raw, _ = devices.issue_pairing_code(
            owner=owner,
            kind=DevicePairingCode.Kind.STRONG,
            issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            device_kind=TrustedDevice.Kind.NODE,
        )
        nodes.append(pair_node(raw_code=raw, node_name=name, protocol_versions=[1]))
    live = create_conversation(workspace=personal, actor=owner, channel="node_voice")
    return owner, personal, nodes, live


def upload(client, conversation_id):
    return client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation_id),
            "audio": SimpleUploadedFile("u.wav", _wav(), content_type="audio/wav"),
        },
        format="multipart",
    )


def stamp(node):
    return Node.objects.values_list("last_active_at", flat=True).get(pk=node.pk)


def test_the_composition_root_injects_the_nodes_writer():
    found = []
    for pattern in get_resolver().url_patterns:
        for sub in getattr(pattern, "url_patterns", []):
            if getattr(sub.callback, "view_class", None) is TranscriptionCreateView:
                found.append(sub.callback.view_initkwargs.get("on_node_activity"))
    assert found == [mark_node_active]


def test_a_node_voice_transcription_bumps_its_node_only(world):
    _, _, (mac, desk), live = world
    response = upload(bearer(mint_token(mac.node_secret)), live.id)
    assert response.status_code == 200, response.content
    assert stamp(mac.node) is not None
    assert stamp(desk.node) is None


def test_an_unknown_conversation_bumps_nothing(world):
    owner, personal, (mac, _), _ = world
    typed = create_conversation(workspace=personal, actor=owner)  # not node_voice
    response = upload(bearer(mint_token(mac.node_secret)), typed.id)
    assert response.status_code == 404
    assert stamp(mac.node) is None


def test_a_web_session_transcription_bumps_nothing(world, web_client):
    owner, _, (mac, desk), live = world
    web = create_conversation(workspace=live.workspace, actor=owner)
    response = upload(web_client(owner), web.id)
    assert response.status_code == 200, response.content
    assert stamp(mac.node) is None and stamp(desk.node) is None


def test_a_failing_writer_never_fails_the_transcription(world, monkeypatch):
    _, _, (mac, _), live = world

    def boom(device_id):
        raise RuntimeError("db down")

    view = TranscriptionCreateView()
    view.on_node_activity = boom
    request = type(
        "R", (), {"auth": type("T", (), {"node_device_id": mac.node.pk})(), "_request": None}
    )()
    monkeypatch.setattr("voice.views.request_channel", lambda request: "node_voice")
    view._note_node_activity(request)  # swallowed
