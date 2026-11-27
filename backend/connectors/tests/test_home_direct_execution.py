# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined
"""Phase 13b branch 1c (ADR-018, D9/D10/D10a): `connectors.home_direct.
execute`, the one direct send, against the fake Home Assistant.

- **Execution:** one `POST /api/services/media_player/media_pause`, no
  `ApprovalRequest`, no `ConnectorWriteExecution`, one `HomeDirectCall
  (succeeded)` and one `ha.direct_call`; the state is read back; a timeout
  after the POST -> `unknown_outcome`, reported, never re-sent; HA 4xx ->
  `failed`, reported; a disabled binding or a pin mismatch -> nothing sent,
  the card; the entity gone from Home Assistant -> the card; a lost
  `running` row -> `unknown_outcome` by the recovery task.
- **Volume:** `volume_set 0.9` with max 0.6 -> sent 0.6, 0.9 kept, the reply
  says so; owner max 0.3 -> 0.3; `volume_up` re-checked on the pre-send
  read (at 0.45 direct, at 0.55 / unknown -> nothing sent, the card).
- **Kill switch / rate store / unattended** at send time.
- **Provenance at the executor:** another actor's or another origin's
  message, or a firm workspace -> refused, nothing sent."""

import threading
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.utils import timezone

from approvals.models import ApprovalRequest
from assistant.models import Conversation, Message
from audit.models import AuditLog
from audit.unattended import recorded_unattended_run
from connectors import home_direct
from connectors.bridges import home_assistant as bridge
from connectors.models import (
    ConnectorBinding,
    ConnectorWriteExecution,
    HomeDirectCall,
    HomeDirectSettings,
)
from connectors.tasks import recover_stale_connector_writes
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from connectors.tests.home_direct_helpers import (
    ORIGINS,
    TV,
    bound_ha,  # noqa: F401
    call,
    direct_tv,  # noqa: F401
)
from core.unattended import UnattendedDenied
from tools import home_ports

pytestmark = pytest.mark.django_db

PAUSE = call("media_pause")


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
    return settings


def _message(owner, workspace, text, arguments, origin="voice", node_id=None, actor=None):
    """The turn's saved user message, as the chat view saves it before the
    executor runs (intent `home_direct`, the classification's payload)."""
    channel, input_mode = ORIGINS[origin]
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    return Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content=text,
        status=Message.Status.COMPLETE,
        created_by=actor or owner,
        intent="home_direct",
        intent_payload={
            "tool_name": "ha_call_service",
            "arguments": arguments,
            "origin": origin,
            "node_id": node_id,
        },
        channel=channel,
        input_mode=input_mode,
    )


def _execute(owner, workspace, message, origin="voice", node_id=None):
    channel, _ = ORIGINS[origin]
    return home_direct.execute(
        workspace=workspace,
        actor=owner,
        message=message,
        origin=origin,
        channel=channel,
        node_id=node_id,
    )


def _run(owner, workspace, text, arguments, origin="voice", node_id=None):
    message = _message(owner, workspace, text, arguments, origin=origin, node_id=node_id)
    return _execute(owner, workspace, message, origin=origin, node_id=node_id), message


# --- execution -------------------------------------------------------------------------


def test_a_direct_pause_is_one_post_one_record_and_one_audit_row(direct_tv, owner, workspace, ha):
    ha.states[TV]["state"] = "playing"
    run, message = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert (run.outcome, run.reply) == ("sent", "TV duraklatıldı.")
    assert ha.posts == [("/api/services/media_player/media_pause", {"entity_id": TV})]
    assert not ApprovalRequest.objects.exists()
    assert not ConnectorWriteExecution.objects.exists()
    record = HomeDirectCall.objects.get()
    assert run.call_id == str(record.pk)
    assert record.status == "succeeded" and record.error_type == ""
    assert record.home_entity_id == direct_tv.pk
    assert (record.origin, record.requested_by_id, record.source_message_id) == (
        "voice",
        owner.pk,
        message.pk,
    )
    assert record.state_before == {
        "state": "playing",
        "volume_level": 0.2,
        "is_volume_muted": False,
    }
    assert record.state_after["state"] == "paused" and record.read_back is True
    assert record.started_at and record.finished_at
    audit = AuditLog.objects.get(action="ha.direct_call")
    assert audit.metadata["home_direct_call"] == str(record.pk)
    assert audit.metadata["status"] == "succeeded"
    assert audit.metadata["origin"] == "voice" and audit.metadata["node"] is None
    assert audit.actor_id == owner.pk


def test_a_node_call_records_the_node(direct_tv, owner, workspace, ha):
    node_id = "0b7f4c1e-1d2a-4f5b-9c3d-2e1f0a9b8c7d"
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE, origin="node", node_id=node_id)
    assert run.outcome == "sent"
    record = HomeDirectCall.objects.get()
    assert (record.origin, str(record.node)) == ("node", node_id)


def test_no_home_assistant_free_text_is_stored(direct_tv, owner, workspace, ha):
    _run(owner, workspace, "TV'yi duraklat", PAUSE)
    record = HomeDirectCall.objects.get()
    stored = repr((record.state_before, record.state_after, record.data))
    audit = repr(AuditLog.objects.get(action="ha.direct_call").metadata)
    for text in ("SECRET-TITLE", "Samsung", "PICTURE-TOKEN", "HDMI"):
        assert text not in stored and text not in audit


def test_a_timeout_after_the_post_is_unknown_outcome_and_never_resent(
    direct_tv, owner, workspace, ha
):
    ha.post_error = httpx.ReadTimeout("slow")
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert run.outcome == "sent"
    assert run.reply == "TV yanıt vermedi; komut gitmiş olabilir, tekrar denemedim."
    assert run.arguments == {}  # never a card after a send attempt
    assert len(ha.posts) == 1
    record = HomeDirectCall.objects.get()
    assert (record.status, record.error_type) == ("unknown_outcome", "timeout")
    assert AuditLog.objects.get(action="ha.direct_call").metadata["status"] == "unknown_outcome"


def test_a_home_assistant_refusal_is_failed_and_reported(direct_tv, owner, workspace, ha):
    ha.service_status = 400
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert run.outcome == "sent"
    assert run.reply == "Home Assistant TV komutunu reddetti; tekrar denemedim."
    assert len(ha.posts) == 1
    record = HomeDirectCall.objects.get()
    assert (record.status, record.error_type) == ("failed", "tool_error")


def test_a_binding_disabled_before_the_send_sends_nothing(direct_tv, owner, workspace, ha):
    """Disabled between the classification and the send: the re-resolution
    is now the matcher's own "not connected" refusal (D10a: its current
    result), so nothing is sent and no card is built from the payload. (A
    card could not be approved either: the gate refuses a disabled
    binding.)"""
    ConnectorBinding.objects.filter(workspace=workspace).update(enabled=False)
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert (run.outcome, run.reason) == ("not_direct", "provenance_mismatch")
    assert run.arguments == {}
    assert run.reply == "Home Assistant bağlı değil; taslak oluşturmadım."
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_a_tool_whose_pin_check_failed_is_not_available(
    direct_tv, owner, workspace, ha, monkeypatch
):
    """The recorded tool status (the pin beat) is checked by `classify`
    (step 6) at the send: `not_available`, nothing sent."""
    from connectors import home_phrases

    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    resolved = home_phrases.resolve_home_command(
        workspace=workspace, actor=owner, text="TV'yi duraklat"
    )
    binding = ConnectorBinding.objects.get(workspace=workspace)
    health = dict(binding.last_health or {})
    health["tools"] = {**health.get("tools", {}), "ha_call_service": "mismatch"}
    binding.last_health = health
    binding.save()
    # The matcher (which checks the status too) answered before the beat.
    monkeypatch.setattr(home_phrases, "resolve_home_command", lambda **_k: resolved)
    run = _execute(owner, workspace, message)
    assert (run.outcome, run.reason) == ("not_direct", "not_available")
    assert run.arguments == PAUSE and run.tool_name == "ha_call_service"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_a_pin_mismatch_sends_nothing_and_is_the_card(direct_tv, owner, workspace, ha, monkeypatch):
    hashes = bridge.tool_hashes()
    monkeypatch.setattr(
        bridge, "tool_hashes", lambda: {**hashes, bridge.SERVER_TOOL_CALL: "0" * 64}
    )
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert (run.outcome, run.reason) == ("not_direct", "not_available")
    assert run.arguments == PAUSE
    assert ha.posts == []
    record = HomeDirectCall.objects.get()
    assert (record.status, record.error_type) == ("failed", "pin_mismatch")


def test_an_entity_gone_from_home_assistant_is_the_card(direct_tv, owner, workspace, ha):
    del ha.states[TV]
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert (run.outcome, run.reason) == ("not_direct", "target_unavailable")
    assert run.arguments == PAUSE
    assert "cihaz şu an doğrulanamadı" in run.reply
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_a_lost_running_call_is_recovered_to_unknown_outcome(direct_tv, owner, workspace, ha):
    record = HomeDirectCall.objects.create(
        workspace=workspace,
        entity_id=TV,
        service="media_pause",
        origin="voice",
        requested_by=owner,
        status="running",
        started_at=timezone.now() - timedelta(hours=1),
    )
    fresh = HomeDirectCall.objects.create(
        workspace=workspace,
        entity_id=TV,
        service="media_pause",
        origin="voice",
        requested_by=owner,
        status="running",
        started_at=timezone.now(),
    )
    result = recover_stale_connector_writes()
    assert result["direct_unknown_outcome"] == 1
    record.refresh_from_db()
    fresh.refresh_from_db()
    assert (record.status, record.error_type) == ("unknown_outcome", "worker_lost")
    assert fresh.status == "running"
    assert ha.posts == []  # never re-sent
    audit = AuditLog.objects.get(action="ha.direct_call")
    assert audit.actor_type == AuditLog.ActorType.SYSTEM


# --- volume ------------------------------------------------------------------------------


def test_volume_set_above_the_maximum_is_sent_at_the_maximum(direct_tv, owner, workspace, ha):
    asked = call("volume_set", data={"volume_level": 0.9})
    run, _ = _run(owner, workspace, "sesi yüzde 90'a ayarla", asked)
    assert run.reply == "Ses en fazla %60 olabilir; %60'a ayarladım."
    assert ha.posts == [
        ("/api/services/media_player/volume_set", {"entity_id": TV, "volume_level": 0.6})
    ]
    record = HomeDirectCall.objects.get()
    assert record.data == {"volume_level": 0.6}
    assert record.requested_data == {"volume_level": 0.9}
    assert AuditLog.objects.get(action="ha.direct_call").metadata["requested_data"] == {
        "volume_level": 0.9
    }


def test_the_owner_maximum_applies(direct_tv, owner, workspace, ha):
    HomeDirectSettings.objects.create(workspace=workspace, volume_max=Decimal("0.30"))
    asked = call("volume_set", data={"volume_level": 0.9})
    run, _ = _run(owner, workspace, "sesi yüzde 90'a ayarla", asked)
    assert run.reply == "Ses en fazla %30 olabilir; %30'a ayarladım."
    assert ha.posts[0][1] == {"entity_id": TV, "volume_level": 0.3}


def test_volume_set_within_the_maximum_is_sent_as_asked(direct_tv, owner, workspace, ha):
    asked = call("volume_set", data={"volume_level": 0.5})
    run, _ = _run(owner, workspace, "sesi yüzde 50'ye ayarla", asked)
    assert run.reply == "TV: ses %50'ye ayarlandı."
    assert HomeDirectCall.objects.get().requested_data is None


@pytest.mark.parametrize(
    ("level", "reason"),
    [(0.45, None), (0.55, "volume_at_max"), (None, "volume_unknown")],
)
def test_volume_up_is_rechecked_on_the_pre_send_read(
    direct_tv, owner, workspace, ha, level, reason
):
    attributes = ha.states[TV]["attributes"]
    if level is None:
        attributes.pop("volume_level")
    else:
        attributes["volume_level"] = level
    run, _ = _run(owner, workspace, "sesi yükselt", call("volume_up"))
    if reason is None:
        assert run.outcome == "sent"
        assert ha.posts == [("/api/services/media_player/volume_up", {"entity_id": TV})]
    else:
        assert (run.outcome, run.reason) == ("not_direct", reason)
        assert run.arguments == call("volume_up")
        assert ha.posts == [] and not HomeDirectCall.objects.exists()


# --- send-time checks -----------------------------------------------------------------


def test_the_owner_kill_switch_flipped_after_classification_is_the_card(
    direct_tv, owner, workspace, ha
):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    # The route classified (direct); the owner flips the switch before the send.
    HomeDirectSettings.objects.create(
        workspace=workspace, kill_switch=True, volume_max=Decimal("0.60")
    )
    run = _execute(owner, workspace, message)
    assert (run.outcome, run.reason) == ("not_direct", "kill_switch_owner")
    assert run.arguments == PAUSE
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_the_env_kill_switch_at_send_time_is_the_card(direct_tv, owner, workspace, ha, settings):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    run = _execute(owner, workspace, message)
    assert run.reason == "kill_switch_env" and ha.posts == []


def test_the_kill_switch_is_rechecked_after_the_pre_send_read(
    direct_tv, owner, workspace, ha, monkeypatch
):
    """Flipped while the entity was being read: the locked re-check right
    before the send refuses."""
    original = bridge.precondition_state

    def read_then_flip(binding, arguments):
        result = original(binding, arguments)
        HomeDirectSettings.objects.create(
            workspace=workspace, kill_switch=True, volume_max=Decimal("0.60")
        )
        return result

    monkeypatch.setattr(bridge, "precondition_state", read_then_flip)
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert run.reason == "kill_switch_owner"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_a_failing_rate_store_is_the_card(direct_tv, owner, workspace, ha, monkeypatch):
    def broken(*_args):
        raise home_direct.RateStoreUnavailable("ConnectionError")

    monkeypatch.setattr(home_direct, "count_direct_call", broken)
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert run.reason == "rate_store_unavailable"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_the_executor_count_is_the_authority_and_audits(
    direct_tv, owner, workspace, ha, monkeypatch, settings
):
    """The classifier's peek said "within the limit" (a concurrent burst);
    the executor's own increment refuses, takes its count back, audits once
    and sends nothing."""
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 1
    # One call of the burst is already counted ...
    home_direct.cache.set(
        home_direct._entity_key(workspace.pk, TV, home_direct._now_seconds()), 1, timeout=70
    )
    # ... but the classifier's peek (pre-check and locked re-check) missed it.
    monkeypatch.setattr(home_direct, "over_limit", lambda *_a: None)
    run, _ = _run(owner, workspace, "TV'yi duraklat", PAUSE)
    assert (run.outcome, run.reason) == ("not_direct", "rate_limited")
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    assert AuditLog.objects.filter(action="ha.direct_rate_limited").count() == 1
    # The refused call spent no quota.
    assert home_direct.peek_direct_calls(workspace.pk, TV) == (0, 1)


def test_a_concurrent_burst_never_overshoots_the_entity_limit(workspace, settings):
    """Follow-up 1 of the branch 1b merge: increment-then-check with
    take-back is the only authority -- however the threads interleave, no
    more calls pass than the limit."""
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 3
    results = []
    barrier = threading.Barrier(12)

    def one():
        barrier.wait()
        results.append(home_direct.count_direct_call(workspace.pk, TV))

    threads = [threading.Thread(target=one) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    passed = [r for r in results if r is None]
    assert 1 <= len(passed) <= 3
    assert home_direct.peek_direct_calls(workspace.pk, TV)[1] == len(passed)


def test_an_unattended_run_never_reaches_the_send(direct_tv, owner, workspace, ha):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    with recorded_unattended_run("run-direct"), pytest.raises(UnattendedDenied):
        _execute(owner, workspace, message)
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


# --- provenance at the executor ---------------------------------------------------------


def test_another_origin_than_the_payloads_is_refused(direct_tv, owner, workspace, ha):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE, origin="voice")
    run = home_direct.execute(
        workspace=workspace,
        actor=owner,
        message=message,
        origin="chat",
        channel="web",
        node_id=None,
    )
    assert (run.outcome, run.reason) == ("not_direct", "provenance_mismatch")
    assert run.arguments == {}
    assert ha.posts == [] and not HomeDirectCall.objects.exists()
    assert AuditLog.objects.get(action="ha.direct_provenance_refused").metadata["detail"] == (
        "origin"
    )


def test_another_actors_message_is_refused(direct_tv, owner, workspace, ha):
    from accounts.tests.node_voice_helpers import other_user

    member, _ = other_user()
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE, actor=member)
    run = _execute(owner, workspace, message)
    assert run.reason == "provenance_mismatch" and run.arguments == {}
    assert ha.posts == []


def test_a_message_that_is_not_a_direct_turn_is_refused(direct_tv, owner, workspace, ha):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    Message.objects.filter(pk=message.pk).update(intent="tool_request")
    message.refresh_from_db()
    run = _execute(owner, workspace, message)
    assert run.reason == "provenance_mismatch"
    assert ha.posts == []


def test_a_firm_workspace_is_never_direct(direct_tv, owner, firm, ha):
    message = _message(owner, firm, "TV'yi duraklat", PAUSE)
    run = _execute(owner, firm, message)
    assert run.reason == "provenance_mismatch"
    assert ha.posts == [] and not HomeDirectCall.objects.exists()


def test_the_port_reaches_the_executor(direct_tv, owner, workspace, ha):
    message = _message(owner, workspace, "TV'yi duraklat", PAUSE)
    run = home_ports.run_home_direct(
        workspace=workspace,
        actor=owner,
        message=message,
        origin="voice",
        channel="web",
    )
    assert run.outcome == "sent" and len(ha.posts) == 1


@pytest.mark.parametrize(
    ("number", "suffix"),
    [(0, "'a"), (10, "'a"), (20, "'ye"), (30, "'a"), (40, "'a"), (50, "'ye"), (60, "'a")]
    + [(70, "'e"), (80, "'e"), (90, "'a"), (100, "'e"), (45, "'e"), (36, "'ya"), (42, "'ye")],
)
def test_the_dative_suffix(number, suffix):
    assert home_direct.dative_suffix(number) == suffix


def test_executor_reasons_are_closed_and_have_text():
    assert set(home_direct.EXECUTOR_REASONS) == {"provenance_mismatch", "target_unavailable"}
    assert home_direct.REASONS == set(home_direct.REASON_TEXT)
