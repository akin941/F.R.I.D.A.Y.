"""Phase 11 branch 6 (brief D13), the second case of the self-trigger
release gate: the Phase 10 release-gate regression ("Hey Friday, terminali
aç" spoken by Friday's own speaker and heard back yields at most a draft
card) re-run end to end **through the node voice channel** -- the voice
token minted with the reference node's secret synthesises the clip on the
speech endpoint, uploads it to the transcription endpoint as the child does
for a wake during playback (`X-Friday-Voice-Trigger: barge_in`, branch 4
note 7) and posts the transcript as the chat turn of a live conversation.

The result is at most one `node_action` draft (confirm-class `apps.open`):
nothing requested, nothing invoked, no frame to the node, no approval.
While no wake model is permitted (D11) the same upload as `wake` is refused
outright. The child-side gate (the echo never reaches the chat endpoint at
all) is `friday-voice selftest --ci` (`node/friday-voice/tests/
test_selftest.py`). FakeAdapter only; no real model or audio."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelUsage
from tools.tests.test_node_action_node_voice import _live, _voice_client
from tools.tests.test_node_action_trust import (  # noqa: F401 -- fixtures
    TERMINAL,
    drafts,
    friday,
    nothing_executed,
    propose,
    speech,
    turn,
)

CLIP = "Hey Friday, terminali aç"


@pytest.fixture
def echo(monkeypatch, settings):
    """Fake TTS remembers what Friday said; fake STT hears exactly that
    (a microphone with no echo cancellation at all, residual 1.0)."""
    settings.VOICE_TTS_ROUTES = {"tr": {}, "en": {}}
    settings.VOICE_TTS_PROVIDER_PARAMS = {}
    settings.AI_PURPOSE_TIERS = {}
    spoken = {}
    original_synthesize = FakeAdapter.synthesize

    def synthesize(self, text, *, params=None, purpose=""):
        spoken["text"] = text
        return original_synthesize(self, text, params=params, purpose=purpose)

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text=spoken["text"], audio_seconds=1.0, output_tokens=4, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "synthesize", synthesize)
    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return spoken


def _upload(client, conversation, clip, trigger):
    client.credentials(
        HTTP_AUTHORIZATION=client._credentials["HTTP_AUTHORIZATION"],
        HTTP_X_FRIDAY_VOICE_TRIGGER=trigger,
    )
    return client.post(
        "/api/v1/voice/transcriptions",
        {
            "conversation": str(conversation.pk),
            "audio": SimpleUploadedFile("clip.wav", clip, content_type="audio/wav"),
        },
        format="multipart",
    )


@pytest.mark.django_db
def test_friday_hearing_itself_over_the_node_voice_channel_yields_at_most_a_draft(
    node_world,
    friday,  # noqa: F811 -- the imported fixture
    speech,  # noqa: F811
    echo,
    make_link,
):
    link = make_link()
    voice = _voice_client(node_world)
    conversation = _live(node_world)
    clip = voice.post(
        "/api/v1/voice/speech",
        {"conversation": str(conversation.pk), "text": CLIP},
        format="json",
    )
    assert clip.status_code == 200, clip.content
    assert echo["text"] == CLIP
    # While no wake model is permitted (D11), a wake-triggered upload is
    # refused before any model call: the server backstop of the gate.
    usage_before = ModelUsage.objects.count()
    refused = _upload(voice, conversation, clip.content, "wake")
    assert refused.status_code == 403, refused.content
    assert refused.json()["code"] == "voice.always_on_not_permitted"
    assert ModelUsage.objects.count() == usage_before
    # A wake during playback is a barge-in upload (branch 4 note 7).
    heard = _upload(voice, conversation, clip.content, "barge_in")
    assert heard.status_code == 200, heard.content
    transcript = heard.json()["text"]
    assert transcript == CLIP
    propose(friday, node_world.node, "apps.open", TERMINAL)
    events = turn(voice, conversation, transcript)
    found = drafts(events)
    assert len(found) <= 1
    if found:
        assert found[0]["tool_name"] == "node_action"
        assert found[0]["action_class"] == "confirm"
        assert found[0]["payload"]["capability"] == "apps.open"
    assert events[-1]["type"] == "done"
    link.pump()
    nothing_executed(node_world)
    assert link.node.executor.calls == []
    assert [f for f in link.frames if f["type"] != "heartbeat"] == []
