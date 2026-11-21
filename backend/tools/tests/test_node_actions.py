"""The `node_action` gate end to end (Phase 10 brief, branch 3): request ->
approve over web -> grant -> reference node window -> fake executor ->
terminal execution, plus the owner-away rules, single use and the
server-side compromised-server cases (D2, D6, D7)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.models import NodeKillSwitch
from approvals.models import ApprovalRequest
from approvals.services import (
    ApprovalExpired,
    approve_request,
    create_request,
)
from approvals.tasks import sweep_expired_approval_requests
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import bus, grants, services
from nodes.audit_chain import verify_chain
from nodes.models import NodeActionExecution, NodeInvocation
from nodes.protocol.signing import sign_envelope
from nodes.reference.node import default_catalogue
from tools.tests.conftest import REPO_ARGS, advertise, script

HUMAN = ApprovalRequest.Origin.HUMAN


def request(world, *, workspace=None, capability="repo.command.run", args=None, **extra):
    return create_request(
        workspace=workspace or world.firm,
        actor=extra.pop("actor", world.owner),
        tool_name="node_action",
        payload={
            "node_id": str(extra.pop("node_id", world.node.pk)),
            "capability": capability,
            "args": dict(REPO_ARGS) if args is None else args,
            "source_message_id": "",
            **extra,
        },
        origin=HUMAN,
        channel=CHANNEL_WEB,
    )


def approve(world, approval, django_capture_on_commit_callbacks, *, execute=True):
    with django_capture_on_commit_callbacks(execute=execute):
        approve_request(approval_request=approval, actor=world.owner, channel=CHANNEL_WEB)
    return NodeActionExecution.objects.get(approval=approval)


def inbox(world):
    return bus.drain_inbox(world.node.pk)


def chain_rows(execution):
    return NodeInvocation.objects.filter(trace_id=execution.trace_id)


def invoke_audits(execution):
    return AuditLog.objects.filter(action="nodes.invoke", resource_id=str(execution.pk))


# --- end to end ---------------------------------------------------------


@pytest.mark.django_db
def test_end_to_end_request_approve_grant_window_run(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(scripts=[script(("stdout", "acme tests ok\n"))])
    approval = request(node_world)
    assert approval.status == "pending"
    assert approval.payload["trust"]["reason_code"] == "trust_check_pending"
    execution = approve(node_world, approval, django_capture_on_commit_callbacks)
    execution.refresh_from_db()
    assert execution.status == "sent" and execution.grant_issued_at is not None
    link.pump()
    execution.refresh_from_db()
    assert execution.status == "ok"
    assert execution.exit_code == 0
    assert execution.confirm_state == "approved"
    assert execution.output_bytes == len("acme tests ok\n")
    assert "acme tests ok" in execution.tail
    kinds = [(f["type"], f.get("state")) for f in link.frames if f["type"] != "heartbeat"]
    assert kinds[:2] == [("confirmation", "shown"), ("confirmation", "approved")]
    assert link.node.executor.calls[0]["argv"][0] == "python3"
    rows = list(chain_rows(execution))
    assert len(rows) == 1
    row = rows[0]
    assert (row.status, row.invocation_class, row.hash_version) == ("ok", "gate", 2)
    assert row.approval_id == approval.pk and row.grant_id == execution.grant_id
    assert row.exit_code == 0 and row.confirm_state == "approved"
    assert invoke_audits(execution).count() == 1
    assert verify_chain().ok
    # The window showed the approval id prefix (D5 check 23).
    assert str(approval.pk)[:8] in link.node.window.shown[0]


@pytest.mark.django_db
def test_a_failing_command_is_failed_with_its_exit_code(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(scripts=[script(("stderr", "1 failed\n"), exit_code=1)])
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    assert (execution.status, execution.exit_code) == ("failed", 1)
    assert chain_rows(execution).get().status == "failed"


# --- owner away (D6) ----------------------------------------------------


@pytest.mark.django_db
def test_a_node_action_approval_expires_after_ten_minutes(node_world):
    approval = request(node_world)
    ApprovalRequest.objects.filter(pk=approval.pk).update(
        created_at=timezone.now() - timedelta(seconds=601)
    )
    with pytest.raises(ApprovalExpired):
        approve_request(approval_request=approval, actor=node_world.owner, channel=CHANNEL_WEB)
    approval.refresh_from_db()
    assert approval.status == "expired"
    assert not NodeActionExecution.objects.exists()


@pytest.mark.django_db
def test_the_sweep_expires_a_node_action_at_ten_minutes_only(node_world):
    fresh = request(node_world)
    stale = request(node_world)
    ApprovalRequest.objects.filter(pk=fresh.pk).update(
        created_at=timezone.now() - timedelta(seconds=590)
    )
    ApprovalRequest.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - timedelta(seconds=601)
    )
    assert sweep_expired_approval_requests() == 1
    fresh.refresh_from_db()
    stale.refresh_from_db()
    assert (fresh.status, stale.status) == ("pending", "expired")


@pytest.mark.django_db
def test_dispatch_61_seconds_after_approval_is_stale_and_signs_nothing(
    node_world, django_capture_on_commit_callbacks
):
    approval = request(node_world)
    execution = approve(node_world, approval, django_capture_on_commit_callbacks, execute=False)
    ApprovalRequest.objects.filter(pk=approval.pk).update(
        decided_at=timezone.now() - timedelta(seconds=61)
    )
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert execution.status == "stale_dispatch"
    assert execution.grant_id is None and execution.grant_issued_at is None
    assert inbox(node_world) == []
    row = chain_rows(execution).get()
    assert (row.status, row.nonce, row.grant_id) == ("stale_dispatch", "", None)
    # No retry: a second dispatch changes nothing.
    services.dispatch_gated(execution.pk)
    assert chain_rows(execution).count() == 1 and inbox(node_world) == []


@pytest.mark.django_db
def test_the_beat_sweep_fails_an_undispatched_execution_stale(
    node_world, django_capture_on_commit_callbacks
):
    approval = request(node_world)
    execution = approve(node_world, approval, django_capture_on_commit_callbacks, execute=False)
    assert services.sweep_node_actions() == 0
    ApprovalRequest.objects.filter(pk=approval.pk).update(
        decided_at=timezone.now() - timedelta(seconds=61)
    )
    assert services.sweep_node_actions() == 1
    execution.refresh_from_db()
    assert execution.status == "stale_dispatch"


@pytest.mark.django_db
def test_node_offline_consumes_the_approval(node_world, django_capture_on_commit_callbacks):
    bus.clear_presence(node_world.node.pk)
    approval = request(node_world)
    execution = approve(node_world, approval, django_capture_on_commit_callbacks)
    execution.refresh_from_db()
    approval.refresh_from_db()
    assert execution.status == "node_offline"
    assert approval.status == "approved"  # consumed: a new attempt is a new proposal
    assert inbox(node_world) == []
    bus.mark_present(node_world.node.pk)
    services.dispatch_gated(execution.pk)
    assert inbox(node_world) == [] and chain_rows(execution).count() == 1


@pytest.mark.django_db
def test_window_timeout_is_confirm_timeout(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(decisions=())
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    assert execution.status == "confirming"
    link.node.clock.advance(61)
    link.pump()
    execution.refresh_from_db()
    assert execution.status == "confirm_timeout"
    assert execution.refusal_code == "confirm_timeout"
    assert link.node.executor.calls == []
    services.dispatch_gated(execution.pk)
    assert inbox(node_world) == []


@pytest.mark.django_db
def test_owner_denies_on_the_mac(node_world, make_link, django_capture_on_commit_callbacks):
    link = make_link(decisions=(("deny", 0),))
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    assert (execution.status, execution.confirm_state) == ("denied", "denied")
    assert link.node.executor.calls == []


# --- single use (D2) ----------------------------------------------------


@pytest.mark.django_db
def test_dispatching_the_same_execution_twice_issues_one_grant(
    node_world, django_capture_on_commit_callbacks
):
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    grant_id = NodeActionExecution.objects.get(pk=execution.pk).grant_id
    services.dispatch_gated(execution.pk)
    services.dispatch_gated(execution.pk)
    envelopes = inbox(node_world)
    assert len(envelopes) == 1
    assert envelopes[0]["grant"]["grant_id"] == str(grant_id)


@pytest.mark.django_db
def test_a_replayed_envelope_is_refused_replay(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    (envelope,) = inbox(node_world)
    link.deliver(envelope)
    replay = link.deliver(envelope)
    assert [(f["status"], f["refusal_code"]) for f in replay] == [("refused", "replay")]
    assert len(link.node.executor.calls) == 1


@pytest.mark.django_db
def test_a_grant_for_approval_a_cannot_run_approval_b_args(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    (envelope_a,) = inbox(node_world)
    other_args = {"repo_path": "~/work/acme", "command_kind": "lint"}
    approval_b = request(node_world, args=other_args)
    execution_b = approve(node_world, approval_b, django_capture_on_commit_callbacks)
    (envelope_b,) = inbox(node_world)
    # The server binds every grant to its own approval's args.
    assert envelope_b["grant"]["approval_id"] == str(approval_b.pk)
    assert envelope_b["grant"]["args_sha256"] == execution_b.args_sha256
    assert envelope_a["grant"]["args_sha256"] != envelope_b["args_sha256"]
    # A compromised server splicing A's grant onto B's args, re-signed.
    forged = {k: v for k, v in envelope_b.items() if k not in ("sig", "key_id")}
    forged["grant"] = envelope_a["grant"]
    forged = sign_envelope(forged, node_world.keyring.active)
    answer = link.deliver(forged)
    assert [(f["status"], f["refusal_code"]) for f in answer] == [("refused", "grant_mismatch")]
    assert link.node.executor.calls == []


@pytest.mark.django_db
def test_issue_runs_the_contract_check_before_building(monkeypatch, node_world):
    """Branch 1 follow-up 2: `issue` calls `check_gate_request` before
    `build_gated_invoke` signs anything."""
    order = []
    real_check, real_build = grants.check_gate_request, grants.build_gated_invoke
    monkeypatch.setattr(
        grants, "check_gate_request", lambda *a, **k: order.append("check") or real_check(*a, **k)
    )
    monkeypatch.setattr(
        grants, "build_gated_invoke", lambda **k: order.append("build") or real_build(**k)
    )
    approval = request(node_world)
    approve_request(approval_request=approval, actor=node_world.owner, channel=CHANNEL_WEB)
    execution = NodeActionExecution.objects.get(approval=approval)
    services.dispatch_gated(execution.pk)
    assert order == ["check", "build"]


# --- compromised server, server side (D2) -------------------------------


def _approved(world, capture):
    approval = request(world)
    return approval, approve(world, approval, capture, execute=False)


@pytest.mark.django_db
def test_a_tampered_stored_payload_is_args_changed(node_world, django_capture_on_commit_callbacks):
    approval, execution = _approved(node_world, django_capture_on_commit_callbacks)
    payload = dict(approval.payload)
    payload["args"] = {"repo_path": "~/work/acme", "command_kind": "build"}
    ApprovalRequest.objects.filter(pk=approval.pk).update(payload=payload)
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == ("server_refused", "args_changed")
    assert inbox(node_world) == [] and execution.grant_id is None


@pytest.mark.django_db
def test_a_capability_removed_from_the_snapshot_is_not_invocable(
    node_world, django_capture_on_commit_callbacks
):
    approval, execution = _approved(node_world, django_capture_on_commit_callbacks)
    advertise(node_world.node, [e for e in default_catalogue() if e["name"] != "repo.command.run"])
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == ("server_refused", "not_invocable")
    assert inbox(node_world) == []


@pytest.mark.django_db
def test_a_path_rule_moved_after_approval_is_path_not_in_workspace(
    node_world, django_capture_on_commit_callbacks
):
    approval, execution = _approved(node_world, django_capture_on_commit_callbacks)
    rule = node_world.rules["~/work"]
    rule.target_workspace = node_world.personal
    rule.save()
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == (
        "server_refused",
        "path_not_in_workspace",
    )
    assert inbox(node_world) == []


@pytest.mark.django_db
def test_kill_switch_engaged_between_approval_and_dispatch_sends_nothing(
    node_world, django_capture_on_commit_callbacks
):
    approval, execution = _approved(node_world, django_capture_on_commit_callbacks)
    devices.engage_node_kill_switch(owner=node_world.owner, via=NodeKillSwitch.Via.WEB)
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == ("server_refused", "kill_switch")
    assert inbox(node_world) == []
    row = chain_rows(execution).get()
    assert row.nonce == "" and row.grant_id is None


@pytest.mark.django_db
def test_no_signing_key_is_signing_unavailable(
    node_world, settings, django_capture_on_commit_callbacks
):
    approval, execution = _approved(node_world, django_capture_on_commit_callbacks)
    settings.NODE_SIGNING_KEY = ""
    services.dispatch_gated(execution.pk)
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == ("server_refused", "signing_unavailable")


# --- the gateway: routing and the watchdog (D7) -------------------------


@pytest.mark.django_db
def test_frames_for_another_socket_or_a_finished_invocation_are_discarded(
    node_world, make_link, django_capture_on_commit_callbacks
):
    from nodes.gateway import NodeSession

    link = make_link()
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    assert execution.status == "ok"
    result = next(f for f in link.frames if f["type"] == "result")
    confirmation = next(f for f in link.frames if f["type"] == "confirmation")
    # The same socket, after the invocation finished.
    assert link.session.receive(result) == "discarded"
    assert link.session.receive(confirmation) == "discarded"
    # Another socket that never forwarded it.
    other = NodeSession(node_world.node, 2)
    for frame in (result, confirmation):
        assert other.receive(frame) == "discarded"
    assert other.discarded == 2 and link.session.discarded == 2
    assert chain_rows(execution).count() == 1


@pytest.mark.django_db
def test_the_watchdog_times_out_a_silent_node(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(decisions=())
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    deadline = services.watchdog_deadline(execution)
    assert deadline == execution.expires_at + 900 + 30
    assert link.session.tick(now=deadline) == []
    assert link.session.tick(now=deadline + 1) == [str(execution.invocation_id)]
    execution.refresh_from_db()
    assert execution.status == "timeout"
    assert chain_rows(execution).get().status == "timeout"


@pytest.mark.django_db
def test_socket_loss_ends_in_flight_invocations_node_disconnected(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(decisions=())
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    link.node.disconnect()
    link.session.disconnect()
    execution.refresh_from_db()
    assert execution.status == "node_disconnected"
    assert not bus.is_present(node_world.node.pk)
    assert link.node.executor.calls == []


@pytest.mark.django_db
def test_a_misrouted_envelope_is_dropped_and_refused(
    node_world, django_capture_on_commit_callbacks
):
    from nodes.gateway import NodeSession

    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    (envelope,) = inbox(node_world)
    bus.push_invoke(node_world.other_node.pk, envelope)
    session = NodeSession(node_world.other_node, 2)
    assert session.pull_invokes() == []
    execution.refresh_from_db()
    assert (execution.status, execution.refusal_code) == ("server_refused", "node_mismatch")


@pytest.mark.django_db
def test_the_beat_sweep_times_out_an_execution_whose_gateway_is_gone(
    node_world, django_capture_on_commit_callbacks
):
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    execution.refresh_from_db()
    later = timezone.now() + timedelta(seconds=90 + 900 + 30 + 61)
    assert services.sweep_node_actions(now=later) == 1
    execution.refresh_from_db()
    assert execution.status == "timeout"


@pytest.mark.django_db
def test_every_execution_writes_exactly_one_chain_and_audit_row(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    done = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    bus.clear_presence(node_world.node.pk)
    offline = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    for execution in (done, offline):
        execution.refresh_from_db()
        assert chain_rows(execution).count() == 1
        assert invoke_audits(execution).count() == 1
    assert verify_chain().ok


@pytest.mark.django_db
def test_tail_retention_clears_old_tails(node_world, make_link, django_capture_on_commit_callbacks):
    link = make_link()
    execution = approve(node_world, request(node_world), django_capture_on_commit_callbacks)
    link.pump()
    execution.refresh_from_db()
    assert execution.tail
    assert services.clear_expired_tails() == 0
    later = timezone.now() + timedelta(days=15)
    assert services.clear_expired_tails(now=later) == 1
    execution.refresh_from_db()
    assert execution.tail == ""
    assert verify_chain().ok  # the chain row is untouched by the update


@pytest.mark.django_db
def test_hello_marks_the_node_present_and_classifies_v2(node_world, make_link):
    bus.clear_presence(node_world.node.pk)
    link = make_link()
    assert bus.is_present(node_world.node.pk)
    snapshot = services.latest_snapshot(node_world.node)
    entry = next(e for e in snapshot.entries if e["name"] == "repo.command.run")
    assert entry["invocable"] and entry["protocol_version"] == 2
    assert link.session.receive({"type": "result", "v": 2}) == "frame_error"
    assert link.session.frame_errors == 1
