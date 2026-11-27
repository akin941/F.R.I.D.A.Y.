# ruff: noqa: F811 -- pytest fixtures imported from the e2e helpers are redefined
"""Phase 13b branch 1c (ADR-018) end to end: "Hey Friday, TV'yi duraklat"
spoken through the reference voice host and a real `friday-voice run` child
against a live Friday over real HTTP (the Phase 11 closeout stack,
`ops/tests/test_live_voice_e2e.py`), on an owner-flagged TV.

The fake STT hears the media command; `home.control` resolves it, ADR-018's
classifier admits it, and the chat view's executor makes exactly one Home
Assistant call: no card, no `ApprovalRequest`, no `router.intent` / chat
model call; the call row and its audit row name origin `node` and the
speaking node. The same utterance on a non-flagged TV is still the card.
This is the server-side contract the Mac session (M2) runs with the real
Swift node and the real TV.

Skipped where `node/friday-voice` has no environment (`uv sync --frozen`
there), unless `FRIDAY_VOICE_E2E=1` demands it."""

import pytest

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from connectors.manifest import get_manifest
from connectors.models import HomeDirectCall
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.speech import phrase_key
from ops.tests.test_live_voice_e2e import (  # noqa: F401 -- fixtures and helpers
    _live_conversations,
    _usage,
    _user_messages,
    friday,
    make_link,
    node_home,
    node_world,
    run,
    speech,
    stack,
)
from workspaces.models import Room

pytestmark = pytest.mark.django_db(transaction=True)

TV = "media_player.salon_tv"
UTTERANCE = "Hey Friday, TV'yi duraklat."


@pytest.fixture
def owner(node_world):
    return node_world.owner


def _home_stack(stack, ha, enable, settings, monkeypatch, *, direct):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    world = stack.world
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), world.personal)
    salon = Room.objects.create(workspace=world.personal, name="Salon", key=phrase_key("Salon"))
    tv = allow(world.personal, TV, ["media_pause", "media_play"], label="TV", room=salon)
    tv.direct = direct
    tv.save()
    ha.requests.clear()

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text=UTTERANCE, audio_seconds=1.0, output_tokens=6, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return stack


def _rows(web_client, world):
    (conversation,) = _live_conversations(world)
    page = web_client(world.owner).get(f"/api/v1/ai/conversations/{conversation.pk}/messages")
    assert page.status_code == 200, page.content
    body = page.json()
    rows = body["results"] if isinstance(body, dict) else body
    (user,) = [row for row in rows if row["role"] == "user"]
    (reply,) = [row for row in rows if row["role"] == "assistant"]
    return user, reply


def test_tv_yi_duraklat_by_voice_pauses_the_flagged_tv_directly(
    stack, ha, enable, settings, monkeypatch, web_client
):
    home = _home_stack(stack, ha, enable, settings, monkeypatch, direct=True)
    result = run(home, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    assert result["ended"] and result["failures"] == []
    world = home.world
    user, reply = _rows(web_client, world)
    assert (user["channel"], user["intent"]) == ("node_voice", "home_direct")
    assert reply["content"] == "Salon TV duraklatıldı."
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    call = HomeDirectCall.objects.get()
    assert (call.status, call.origin) == ("succeeded", "node")
    assert str(call.node) == str(world.node.pk)
    audit = AuditLog.objects.get(action="ha.direct_call")
    assert audit.metadata["origin"] == "node" and audit.metadata["entity_id"] == TV
    # Speech in and out only: no router, no chat completion, no card.
    assert _usage("router.intent") == 0 and _usage("chat") == 0
    assert _usage("voice.stt") == 1 and _usage("voice.tts") >= 1
    assert not ApprovalRequest.objects.exists()
    assert len(_user_messages(world)) == 1


def test_the_same_words_on_a_non_flagged_tv_are_the_card(
    stack, ha, enable, settings, monkeypatch, web_client
):
    home = _home_stack(stack, ha, enable, settings, monkeypatch, direct=False)
    result = run(home, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    user, reply = _rows(web_client, home.world)
    assert user["intent"] == "tool_request"
    assert user["intent_payload"]["direct_reason"] == "not_flagged"
    assert reply["content"].startswith("Onay kartı hazır: Salon TV · duraklat.")
    assert ha.requests == [] and not HomeDirectCall.objects.exists()
    assert not ApprovalRequest.objects.exists()
