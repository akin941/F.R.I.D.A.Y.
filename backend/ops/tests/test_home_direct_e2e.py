# ruff: noqa: F811 -- pytest fixtures imported from the e2e helpers are redefined
"""Phase 13b closeout end to end (brief branch 3, ADR-018): the direct media
class on one Friday, against the fake Home Assistant behind the real bridge
(`httpx.MockTransport` seam, `connectors.tests.fake_home_assistant`).

1. **Every origin is direct on a flagged TV.** A `node_voice` turn through
   the Phase 11 reference voice host and a real `friday-voice run` child
   against a live Friday over real HTTP ("Hey Friday, TV'yi duraklat"), then
   a web voice turn and a typed turn through the chat view: exactly one
   Home Assistant POST each, no `ApprovalRequest`, no
   `ConnectorWriteExecution`, a `HomeDirectCall` + `ha.direct_call` per
   call with the right origin (`node` with the speaking node's id, `voice`,
   `chat`), and no router / chat model call.
2. **Unflagged is Phase 13 unchanged:** the same words are the card; the
   owner's tap and web approval make exactly one call.
3. **The kill switch on `/connectors`** (`PATCH
   /api/v1/connectors/home-assistant/direct`): the next utterance is a card;
   switched back, the next one is direct again.
4. **The rate limit:** the eleventh call on the TV within a minute is a
   card (`rate_limited`, audited), nothing sent.
5. **The clamp:** "sesi yüzde 90'a ayarla" is sent as the owner maximum 0.6.
6. **No protocol change:** `node_protocol_frozen` passes.
7. **The interim Mac server:** a macOS-declared preflight run with
   R2-recorded status files has no `fail` row in `check_home`, and
   `ha_direct_media` passes for the flagged TV.

Part 1's child half skips where `node/friday-voice` has no environment
(`uv sync --frozen` there), unless `FRIDAY_VOICE_E2E=1` demands it; the
other parts run everywhere. Nothing reaches a real model, Home Assistant,
Mac, TV, bucket or network beyond the loopback. The real-hardware run is
the Mac session M2 (pending owner evaluation): the Swift node must satisfy
this unchanged (no protocol change)."""

import json
import socket
import time

import pytest
from django.core.cache import cache

from accounts.tests.node_voice_helpers import CHAT_URL, bearer, mint_token, sse_events
from ai_core.adapters.base import TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from approvals.models import ApprovalRequest
from assistant.models import Conversation, Message
from assistant.services import create_conversation
from audit.models import AuditLog
from connectors.manifest import get_manifest
from connectors.models import ConnectorWriteExecution, HomeDirectCall, HomeEntity
from connectors.tests.conftest import committed, enable  # noqa: F401
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.speech import phrase_key
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from ops.tests.test_live_voice_e2e import (  # noqa: F401 -- fixtures and helpers
    _live_conversations,
    _usage,
    friday,
    make_link,
    node_home,
    node_world,
    run,
    speech,
    stack,
)
from ops.tests.test_preflight_home import write_drill, write_status
from workspaces.models import Room

pytestmark = pytest.mark.django_db(transaction=True)

TV = "media_player.salon_tv"
MEDIA_SERVICES = [
    "media_play",
    "media_pause",
    "media_play_pause",
    "media_stop",
    "volume_up",
    "volume_down",
    "volume_set",
    "volume_mute",
]
PAUSE = ("/api/services/media_player/media_pause", {"entity_id": TV})
DIRECT_URL = "/api/v1/connectors/home-assistant/direct"
APPROVALS_URL = "/api/v1/approvals/requests"
R2 = "s3:https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/friday-backup"


@pytest.fixture
def owner(node_world):
    """The installation owner, for the connector fixtures (`enable`)."""
    return node_world.owner


@pytest.fixture
def direct_home(node_world, friday, ha, enable, settings):
    """Home Assistant bound in the owner's personal workspace; the salon TV
    allowlisted with turn_on and every media service, flagged direct; the
    environment's direct settings at their documented defaults."""
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
    cache.clear()  # the rate windows
    world = node_world
    ha.states[TV] = salon_tv_state()
    enable(get_manifest("home_assistant"), world.personal)
    salon = Room.objects.create(workspace=world.personal, name="Salon", key=phrase_key("Salon"))
    tv = allow(world.personal, TV, ["turn_on", *MEDIA_SERVICES], label="TV", room=salon)
    tv.direct = True
    tv.save()
    ha.requests.clear()
    yield world
    cache.clear()


def _web_turn(web_client, world, text, *, voice=True):
    conversation = Conversation.objects.create(workspace=world.personal, created_by=world.owner)
    body = {"content": text}
    if voice:
        body["input_mode"] = "voice"
    response = web_client(world.owner).post(CHAT_URL.format(conversation.pk), body, format="json")
    assert response.status_code == 200, response.content
    events = sse_events(response)
    return events, Message.objects.get(conversation=conversation, role="user")


def _node_turn(world, text):
    """A `node_voice` turn in process (a token minted with the paired node's
    secret, as the voice host does) -- the child run is part 1's own test."""
    conversation = create_conversation(
        workspace=world.personal, actor=world.owner, channel="node_voice"
    )
    client = bearer(mint_token(world.node_secret))
    response = client.post(CHAT_URL.format(conversation.pk), {"content": text}, format="json")
    assert response.status_code == 200, response.content
    events = sse_events(response)
    return events, Message.objects.get(conversation=conversation, role="user")


def _draft(events):
    return next((e for e in events if e["type"] == "draft"), None)


def _reply(events):
    return "".join(e["text"] for e in events if e["type"] == "chunk")


def _wait(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _no_card_and_no_gate_write():
    return not ApprovalRequest.objects.exists() and not ConnectorWriteExecution.objects.exists()


# --- 1. every origin is direct on a flagged TV -------------------------------------


@pytest.fixture
def voice_child(stack, direct_home, monkeypatch):
    """The live stack (uvicorn + the real `friday-voice` child) hearing the
    pause command on the flagged TV."""

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        return TranscribeAdapterResult(
            text="Hey Friday, TV'yi duraklat.",
            audio_seconds=1.0,
            output_tokens=6,
            raw_usage={"fake": True},
        )

    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return stack


def test_node_voice_web_voice_and_typed_each_make_one_direct_call(voice_child, ha, web_client):
    world = voice_child.world
    # node_voice through the reference voice host and a real child process.
    result = run(voice_child, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    assert result["ended"] and result["failures"] == []
    (conversation,) = _live_conversations(world)
    user = Message.objects.get(conversation=conversation, role="user")
    assert (user.channel, user.intent) == ("node_voice", "home_direct")
    reply = Message.objects.get(conversation=conversation, role="assistant")
    assert reply.content == "Salon TV duraklatıldı."
    assert ha.posts == [PAUSE]

    # Web voice: "devam ettir" resumes.
    events, voice_user = _web_turn(web_client, world, "TV'yi devam ettir")
    assert _draft(events) is None and _reply(events) == "Salon TV oynatılıyor."
    assert voice_user.intent == "home_direct"
    # Typed: "durdur" pauses (owner 2026-11-27, amending Phase 13b D7: some
    # TVs close the app on media_stop), before any router call.
    events, typed_user = _web_turn(web_client, world, "TV'yi durdur", voice=False)
    assert _draft(events) is None and _reply(events) == "Salon TV duraklatıldı."
    assert (typed_user.intent, typed_user.route) == ("home_direct", "home.control")

    assert ha.posts == [
        PAUSE,
        ("/api/services/media_player/media_play", {"entity_id": TV}),
        PAUSE,
    ]
    calls = list(HomeDirectCall.objects.order_by("created_at"))
    assert [(c.origin, c.service, c.status) for c in calls] == [
        ("node", "media_pause", "succeeded"),
        ("voice", "media_play", "succeeded"),
        ("chat", "media_pause", "succeeded"),
    ]
    assert str(calls[0].node) == str(world.node.pk)
    assert calls[1].node is None and calls[2].node is None
    assert [c.source_message_id for c in calls] == [user.pk, voice_user.pk, typed_user.pk]
    audits = list(AuditLog.objects.filter(action="ha.direct_call").order_by("created_at"))
    assert [(a.metadata["origin"], a.metadata["entity_id"]) for a in audits] == [
        ("node", TV),
        ("voice", TV),
        ("chat", TV),
    ]
    assert audits[0].metadata["node"] == str(world.node.pk)
    assert audits[1].metadata["node"] is None and audits[2].metadata["node"] is None
    # No card, no gate write; speech in and out only, no router or chat call.
    assert _no_card_and_no_gate_write()
    assert _usage("router.intent") == 0 and _usage("chat") == 0
    assert _usage("voice.stt") == 1


# --- 2. unflagged: the Phase 13 card, approved on the web ---------------------------


def test_an_unflagged_tv_is_the_card_and_the_approval_makes_one_call(direct_home, ha, web_client):
    world = direct_home
    tv = HomeEntity.objects.get(workspace=world.personal, entity_id=TV)
    tv.direct = False
    tv.save()
    events, user = _node_turn(world, "Hey Friday, TV'yi duraklat")
    draft = _draft(events)
    assert draft is not None and draft["direct_reason"] == "not_flagged"
    assert user.intent == "tool_request"
    payload = draft["payload"]
    assert payload == {
        "arguments": {
            "domain": "media_player",
            "service": "media_pause",
            "entity_id": TV,
            "data": {},
        }
    }
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    assert not ApprovalRequest.objects.exists()

    client = web_client(world.owner)
    tapped = client.post(
        APPROVALS_URL,
        {"workspace": str(world.personal.pk), "tool_name": "ha_call_service", "payload": payload},
        format="json",
    )
    assert tapped.status_code == 201, tapped.content
    assert ha.posts == []
    decided = client.post(f"{APPROVALS_URL}/{tapped.json()['id']}/approve", format="json")
    assert decided.status_code == 200, decided.content
    assert _wait(
        lambda: ConnectorWriteExecution.objects.exclude(status__in=("pending", "running")).exists()
    )
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded", (execution.status, execution.error_type)
    assert ha.posts == [PAUSE]
    assert not HomeDirectCall.objects.exists()


# --- 3. the kill switch on /connectors ------------------------------------------------


def test_the_kill_switch_on_connectors_makes_the_next_utterance_a_card(direct_home, ha, web_client):
    world = direct_home
    client = web_client(world.owner)
    switched = client.patch(
        DIRECT_URL, {"workspace": str(world.personal.pk), "kill_switch": True}, format="json"
    )
    assert switched.status_code == 200, switched.content
    assert switched.json()["state"] == "off_owner"
    assert (preflight.check_home_direct()[0].status, "off_owner") == (
        WARN,
        switched.json()["state"],
    )

    events, user = _web_turn(web_client, world, "TV'yi duraklat")
    assert _draft(events)["direct_reason"] == "kill_switch_owner"
    assert user.intent == "tool_request"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()

    back = client.patch(
        DIRECT_URL, {"workspace": str(world.personal.pk), "kill_switch": False}, format="json"
    )
    assert back.status_code == 200 and back.json()["state"] == "on"
    events, user = _web_turn(web_client, world, "TV'yi duraklat")
    assert _draft(events) is None and user.intent == "home_direct"
    assert ha.posts == [PAUSE]
    changes = AuditLog.objects.filter(action="ha.direct_settings_changed").count()
    assert changes == 2
    assert not ApprovalRequest.objects.exists()


# --- 4. the rate limit ----------------------------------------------------------------


def test_the_eleventh_call_in_a_minute_is_a_card(direct_home, ha, web_client):
    world = direct_home
    for _ in range(10):
        events, user = _web_turn(web_client, world, "TV'yi duraklat")
        assert _draft(events) is None and user.intent == "home_direct"
    assert len(ha.posts) == 10
    events, user = _web_turn(web_client, world, "TV'yi duraklat")
    draft = _draft(events)
    assert draft is not None and draft["direct_reason"] == "rate_limited"
    assert user.intent == "tool_request"
    assert len(ha.posts) == 10  # nothing more sent
    assert HomeDirectCall.objects.count() == 10
    limited = AuditLog.objects.get(action="ha.direct_rate_limited")
    assert limited.metadata["limit"] == "entity" and limited.metadata["entity_id"] == TV
    assert not ApprovalRequest.objects.exists()


# --- 5. the clamp ---------------------------------------------------------------------


def test_sesi_yuzde_90_is_sent_as_the_maximum(direct_home, ha):
    events, user = _node_turn(direct_home, "Hey Friday, sesi yüzde 90'a ayarla")
    assert _draft(events) is None and user.intent == "home_direct"
    assert _reply(events) == "Ses en fazla %60 olabilir; %60'a ayarladım."
    assert ha.posts == [
        ("/api/services/media_player/volume_set", {"entity_id": TV, "volume_level": 0.6})
    ]
    call = HomeDirectCall.objects.get()
    assert (call.origin, call.data, call.requested_data) == (
        "node",
        {"volume_level": 0.6},
        {"volume_level": 0.9},
    )
    assert AuditLog.objects.get(action="ha.direct_call").metadata["requested_data"] == {
        "volume_level": 0.9
    }


# --- 6. no protocol change -------------------------------------------------------------


def test_the_node_protocol_is_frozen():
    row = preflight._check_node_protocol_frozen()
    assert row.status == PASS, row


# --- 7. the interim Mac server: macOS-declared preflight with R2 status files ----------


def test_a_macos_preflight_with_r2_status_files_has_no_fail_and_direct_media_passes(
    direct_home, settings, tmp_path, monkeypatch, ha
):
    table = {"web": ["172.18.0.5"], "localhost": ["127.0.0.1", "::1"]}

    def resolve(host):
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return list(table[host])

    monkeypatch.setattr(preflight, "resolve_host", resolve)  # no DNS in tests
    settings.HOME_SERVER_PLATFORM = "macos"
    settings.ALLOWED_HOSTS = ["owner-mbp.local", "192.168.1.30", "localhost", "127.0.0.1", "web"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.30/ws/node/v1"
    settings.NODE_TLS_PIN_SHA256 = ""
    settings.HOME_BACKUP_STATUS_PATH = str(tmp_path / "status.json")
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(tmp_path / "drill.json")
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-20"
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = R2
    write_status(tmp_path, hours_ago=3, repository=R2)
    write_drill(tmp_path, days_ago=4)

    results = preflight.run_preflight(
        checks=(("home", preflight.check_home), ("home_direct", preflight.check_home_direct))
    )
    rows = {row.name: row for row in results}
    assert [r for r in results if r.status == FAIL] == [], results
    assert rows["home_backup_fresh"].status == PASS
    assert rows["home_backup_fresh"].detail.endswith(", off-site R2")
    assert rows["home_server_platform"].status == WARN
    assert rows["home_disk_encryption"].status == WARN
    assert "FileVault" in rows["home_disk_encryption"].detail
    assert rows["ha_direct_media"].status == PASS, rows["ha_direct_media"]
    assert rows["ha_direct_media"].detail.startswith("state on, 1 flagged entity")
    # Read-only: no Home Assistant request, no model call, no secret echoed.
    assert ha.requests == []
    assert _usage("router.intent") == 0
    assert "0123456789abcdef" not in json.dumps([r.detail for r in results])
