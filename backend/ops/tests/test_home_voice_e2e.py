# ruff: noqa: F811 -- pytest fixtures imported from the e2e helpers are redefined
"""Phase 13 D13 (branch 6) end to end: "salondaki TV'yi aç" spoken through
the reference voice host and a real `friday-voice run` child against a live
Friday over real HTTP (the Phase 11 closeout stack,
`ops/tests/test_live_voice_e2e.py`).

The fake STT hears the home command; the `home.control` route answers it:
one draft card for `ha_call_service` with the exact arguments, spoken back
as Friday's fixed confirmation -- no `router.intent` / chat model call, no
`ApprovalRequest`, no Home Assistant request. The execution needs the
owner's on-screen approval; no voice path reaches it.

Skipped where `node/friday-voice` has no environment (`uv sync --frozen`
there), unless `FRIDAY_VOICE_E2E=1` demands it."""

import pytest

from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from approvals.models import ApprovalRequest
from connectors.manifest import get_manifest
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
UTTERANCE = "Hey Friday, salondaki TV'yi aç."


@pytest.fixture
def owner(node_world):
    return node_world.owner


@pytest.fixture
def home_stack(stack, ha, enable, settings, monkeypatch):
    settings.HA_ALLOWED_DOMAINS = []
    world = stack.world
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), world.personal)
    salon = Room.objects.create(workspace=world.personal, name="Salon", key=phrase_key("Salon"))
    allow(world.personal, TV, ["turn_on"], label="TV", room=salon)
    ha.requests.clear()

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text=UTTERANCE, audio_seconds=1.0, output_tokens=6, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return stack


def test_salondaki_tv_yi_ac_by_voice_is_a_card_on_screen_and_nothing_else(
    home_stack, ha, web_client
):
    result = run(home_stack, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    assert result["ended"] and result["failures"] == []
    world = home_stack.world
    (conversation,) = _live_conversations(world)
    page = web_client(world.owner).get(f"/api/v1/ai/conversations/{conversation.pk}/messages")
    assert page.status_code == 200, page.content
    body = page.json()
    rows = body["results"] if isinstance(body, dict) else body
    (user,) = [row for row in rows if row["role"] == "user"]
    (reply,) = [row for row in rows if row["role"] == "assistant"]
    # What `drafts.ts` rebuilds the card from on `/chat/<id>`.
    assert (user["channel"], user["intent"]) == ("node_voice", "tool_request")
    assert user["intent_payload"]["tool_name"] == "ha_call_service"
    assert user["intent_payload"]["payload"] == {
        "arguments": {"domain": "media_player", "service": "turn_on", "entity_id": TV, "data": {}}
    }
    assert reply["content"].startswith("Onay kartı hazır: Salon TV · aç.")
    # Speech in and out only: no router, no chat completion.
    assert _usage("router.intent") == 0 and _usage("chat") == 0
    assert _usage("voice.stt") == 1 and _usage("voice.tts") >= 1
    assert not ApprovalRequest.objects.exists()
    assert ha.requests == []
    assert len(_user_messages(world)) == 1
