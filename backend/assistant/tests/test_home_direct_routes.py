# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined as arguments
"""Phase 13b branch 1c (ADR-018, D7/D10): the direct media class end to end
through the real chat view, against the fake Home Assistant.

- Web voice "TV'yi duraklat" on the flagged salon TV: one `POST
  /api/services/media_player/media_pause`, no `draft` event, no
  `ApprovalRequest`, no `ConnectorWriteExecution`, one `HomeDirectCall
  (succeeded)` and one `ha.direct_call` (origin, entity), a fixed reply, no
  model call and no `ModelUsage`.
- **Still gate-class** (the owner's required proofs), one parametrised table
  over every origin (web voice, `node_voice`, typed): a non-media domain, a
  non-flagged entity, a flagged entity with a non-direct service, a scene,
  kill switch on (env; owner), over the rate limit -> a card (voice) or the
  router path (typed), no Home Assistant request, no `HomeDirectCall`. A
  completeness check pins the table against the closed reason set."""

import pytest

from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from assistant.models import Message
from assistant.tests.home_direct_fixtures import (  # noqa: F401 -- fixtures
    ORIGINS,
    PAUSE,
    TV,
    calls,
    committed,
    direct_settings,
    draft_of,
    enable,
    ha,
    home,
    n8n_chat,
    node,
    owner,
    reply_text,
    route,
    turn,
    workspace,
)
from audit.models import AuditLog
from connectors import home_direct
from connectors.models import ConnectorWriteExecution, HomeDirectCall, HomeDirectSettings

pytestmark = pytest.mark.django_db


def test_web_voice_pauses_the_flagged_tv_with_one_call_and_no_card(turn, ha, calls, route):
    route(None)  # the router would say "chat": it is never asked
    events, user = turn("voice", "Hey Friday, TV'yi duraklat")
    assert draft_of(events) is None
    assert reply_text(events) == "Salon TV duraklatıldı."
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    assert calls == [] and not ModelUsage.objects.exists()
    assert not ApprovalRequest.objects.exists()
    assert not ConnectorWriteExecution.objects.exists()
    call = HomeDirectCall.objects.get()
    assert (call.status, call.origin, call.entity_id, call.service) == (
        "succeeded",
        "voice",
        TV,
        "media_pause",
    )
    assert call.node is None and call.source_message_id == user.pk
    assert call.read_back is True and call.state_after["state"] == "paused"
    audit = AuditLog.objects.get(action="ha.direct_call")
    assert audit.metadata["origin"] == "voice"
    assert audit.metadata["entity_id"] == TV and audit.metadata["service"] == "media_pause"
    assert audit.metadata["status"] == "succeeded"
    assert (user.intent, user.route) == ("home_direct", "home.control")
    assert user.intent_payload["call_id"] == str(call.pk)
    assert user.intent_payload["arguments"] == PAUSE
    assert "_provenance" not in user.intent_payload
    reply = Message.objects.get(conversation=user.conversation, role="assistant")
    assert reply.model_usage is None and reply.route == "home.control"


@pytest.mark.parametrize(
    ("text", "service", "data", "reply"),
    [
        ("TV'yi oynat", "media_play", {}, "Salon TV oynatılıyor."),
        # Owner 2026-11-27: Turkish "durdur" pauses; English "stop" stops.
        ("TV'yi durdur", "media_pause", {}, "Salon TV duraklatıldı."),
        ("stop the TV", "media_stop", {}, "Salon TV durduruldu."),
        ("TV'yi oynat duraklat", "media_play_pause", {}, "Salon TV oynatılıyor."),
        ("TV'nin sesini kapat", "volume_mute", {"is_volume_muted": True}, None),
        ("sesi yüzde 40'a ayarla", "volume_set", {"volume_level": 0.4}, None),
        ("TV'nin sesini kıs", "volume_down", {}, None),
    ],
)
def test_each_direct_service_is_one_call_with_a_fixed_reply(turn, ha, text, service, data, reply):
    events, user = turn("voice", text)
    assert draft_of(events) is None, (text, user.intent_payload)
    assert ha.posts == [(f"/api/services/media_player/{service}", {"entity_id": TV, **data})]
    if reply is not None:
        assert reply_text(events) == reply
    assert HomeDirectCall.objects.get().status == "succeeded"


@pytest.mark.parametrize("text", ["TV'yi durdur", "Hey Friday, televizyonu durdur"])
def test_durdur_directly_pauses_a_flagged_tv_that_allowlists_only_media_pause(turn, ha, home, text):
    """Regression (owner 2026-11-27, D7 amendment): a flagged TV whose
    `services` are exactly `["media_pause"]` is paused by "durdur" with one
    direct call: no card, no refusal, never `media_stop`."""
    home.tv.services = ["media_pause"]
    home.tv.save()
    events, user = turn("voice", text)
    assert draft_of(events) is None, user.intent_payload
    assert reply_text(events) == "Salon TV duraklatıldı."
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    call = HomeDirectCall.objects.get()
    assert (call.status, call.service) == ("succeeded", "media_pause")


def test_volume_set_reply_names_the_level(turn, ha):
    events, _ = turn("voice", "sesi yüzde 40'a ayarla")
    assert reply_text(events) == "Salon TV: ses %40'a ayarlandı."


def test_turn_on_is_still_a_card(turn, ha):
    """ "Salondaki TV'yi aç" is not a media service of the closed set."""
    events, user = turn("voice", "salondaki TV'yi aç")
    draft = draft_of(events)
    assert draft["payload"]["arguments"]["service"] == "turn_on"
    assert draft["direct_reason"] == "service_not_direct"
    assert user.intent == "tool_request"
    assert ha.requests == [] and not HomeDirectCall.objects.exists()


# --- still gate-class: the parametrised table ---------------------------------------


def _non_media(home, workspace):
    return "salondaki lambayı kapat"


def _not_flagged(home, workspace):
    home.tv.direct = False
    home.tv.save()
    return "TV'yi duraklat"


def _non_direct_service(home, workspace):
    return "televizyonu HDMI 2'ye geç"


def _scene(home, workspace):
    return "film gecesi modunu aç"


def _kill_switch_env(home, workspace):
    from django.conf import settings

    settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    return "TV'yi duraklat"


def _kill_switch_owner(home, workspace):
    HomeDirectSettings.objects.create(workspace=workspace, kill_switch=True, volume_max="0.60")
    return "TV'yi duraklat"


def _over_the_limit(home, workspace):
    from django.conf import settings

    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 0
    return "TV'yi duraklat"


def _entity_disabled_for_the_service(home, workspace):
    home.tv.services = [s for s in home.tv.services if s != "media_pause"]
    home.tv.save()
    return "TV'yi duraklat"


STILL_GATE = {
    "not_media_player": _non_media,
    "not_flagged": _not_flagged,
    "service_not_direct": _non_direct_service,
    "scene": _scene,
    "kill_switch_env": _kill_switch_env,
    "kill_switch_owner": _kill_switch_owner,
    "rate_limited": _over_the_limit,
}

# Every closed classifier reason is either a row of the table above, proved
# through the turn in another module (named), or not reachable from the
# deterministic matcher (named, with why). A new reason without a row fails.
COVERED_ELSEWHERE = {
    "unattended": "test_home_direct_execution (classify and execute under unattended_run)",
    "origin_not_direct": "connectors/tests/test_home_direct_classifier (CLI/facade/hook)",
    "not_owner": "test_home_direct_typed (a member's typed turn goes to the router)",
    "arguments_invalid": "connectors/tests/test_home_direct_classifier",
    "entity_not_allowed": "the matcher never drafts an entity outside the allowlist",
    "service_not_allowed": "the matcher refuses an unticked service (no draft at all)",
    "not_available": "test_home_direct_execution (the pin check failed before the send)",
    "volume_unknown": "test_home_direct_execution (volume_up without a live level)",
    "volume_at_max": "test_home_direct_execution (volume_up at the maximum)",
    "rate_store_unavailable": "test_home_direct_execution (the cache raising)",
}


def test_the_table_covers_every_closed_reason():
    rows = set(STILL_GATE) - {"scene"}
    assert rows | set(COVERED_ELSEWHERE) == set(home_direct.CLASSIFY_REASONS)
    assert not rows & set(COVERED_ELSEWHERE)


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("case", sorted(STILL_GATE))
def test_still_gate_class(case, origin, turn, home, workspace, ha, calls, route):
    text = STILL_GATE[case](home, workspace)
    # The typed router path, stubbed to "chat": a typed non-direct turn
    # reaches the router exactly as in Phase 13.
    route(None)
    events, user = turn(origin, text)
    assert ha.posts == []
    assert not HomeDirectCall.objects.exists()
    assert user.intent != "home_direct"
    if origin == "chat":
        assert calls and calls[0]["purpose"] == "router.intent"
        assert user.route is None
    else:
        draft = draft_of(events)
        assert draft is not None and draft["tool_name"] == "ha_call_service", reply_text(events)
        assert user.intent == "tool_request"
        if case != "rate_limited":
            assert not ModelUsage.objects.exists()


def test_an_unticked_service_is_refused_not_carded(turn, home, workspace, ha):
    _entity_disabled_for_the_service(home, workspace)
    events, user = turn("voice", "TV'yi duraklat")
    assert draft_of(events) is None
    assert "iznim yok" in reply_text(events)
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_over_the_limit_is_audited_and_a_card(turn, home, ha):
    from django.conf import settings

    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 2
    for _ in range(2):
        events, _ = turn("voice", "TV'yi duraklat")
        assert draft_of(events) is None
    events, user = turn("voice", "TV'yi duraklat")
    assert draft_of(events)["direct_reason"] == "rate_limited"
    assert len(ha.posts) == 2
    assert HomeDirectCall.objects.count() == 2
    limited = AuditLog.objects.get(action="ha.direct_rate_limited")
    assert limited.metadata["limit"] == "entity" and limited.metadata["origin"] == "voice"


def test_the_eleventh_call_in_a_minute_is_a_card(turn, home, ha):
    """The brief's goal 4: 10/min per entity, then the card."""
    for _ in range(10):
        events, _ = turn("chat", "TV'yi duraklat")
        assert draft_of(events) is None
    events, _ = turn("voice", "TV'yi duraklat")
    assert draft_of(events)["direct_reason"] == "rate_limited"
    assert len(ha.posts) == 10
