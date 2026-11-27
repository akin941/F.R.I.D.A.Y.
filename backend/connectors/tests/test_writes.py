"""Gated connector writes (brief D6): snapshot, display == execution
arguments, re-check at approval and execution, at most once, never
retried, work_log on every terminal state."""

import dataclasses
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.models import AuditLog
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import set_binding_enabled
from connectors.tasks import recover_stale_connector_writes, run_connector_write
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from memory.models import Memory
from tools.registry import get_tool, list_tool_names

pytestmark = pytest.mark.django_db

GATE = "mcp_fake_fake_create_note"
URL = "/api/v1/approvals/requests"


def _request(web_client, owner, workspace, arguments, **extra):
    return web_client(owner).post(
        URL,
        {
            "workspace": str(workspace.id),
            "tool_name": GATE,
            "payload": {"arguments": arguments, **extra},
        },
        format="json",
    )


@pytest.fixture
def approve(web_client, owner, committed):
    def _approve(request_id):
        with committed():
            return web_client(owner).post(f"{URL}/{request_id}/approve")

    return _approve


def _without_dispatch(monkeypatch, manifest):
    """Simulate a lost broker publish: the approval commits, nothing runs."""
    from connectors.writes import build_write_tool
    from tools import registry

    spec = build_write_tool(manifest, manifest.get_tool("fake_create_note"))
    monkeypatch.setitem(
        registry._REGISTRY, GATE, dataclasses.replace(spec, dispatch=lambda _id: None)
    )


def test_the_write_is_a_registered_user_invocable_gate(fake_manifest):
    spec = get_tool(GATE)
    assert spec.gated is True
    assert GATE in list_tool_names()


def test_display_equals_what_the_server_receives_and_it_is_sent_once(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    arguments = {"title": "Toplantı notu", "body": "Çarşamba 10:00\nAjanda: bütçe"}
    response = _request(
        web_client,
        owner,
        workspace,
        arguments,
        # Everything but `arguments` is ignored: the snapshot is server-built.
        binding_id="00000000-0000-0000-0000-000000000000",
        schema_sha256="0" * 64,
    )
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    approval = ApprovalRequest.objects.get(id=request_id)
    assert approval.payload["binding_id"] == str(ConnectorBinding.objects.get().id)
    assert (
        approval.payload["schema_sha256"]
        == fake_manifest.get_tool("fake_create_note").schema_sha256
    )

    summary = web_client(owner).get(f"{URL}/{request_id}").json()["display_summary"]
    assert summary["kind"] == "connector_write"
    assert {row["label"]: row["value"] for row in summary["rows"]} == arguments
    assert server.calls == []  # nothing before the decision

    assert approve(request_id).status_code == 200
    assert len(server.calls) == 1
    name, sent = server.calls[0]
    assert name == "fake_create_note"
    # Byte-for-byte: the dialog's canonical JSON is exactly what was sent.
    assert summary["arguments_json"] == json.dumps(
        sent, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded"
    assert execution.result["text"] == "note created: Toplantı notu"
    assert AuditLog.objects.filter(action="connector.write.succeeded").count() == 1
    work_log = Memory.objects.get(memory_type="work_log")
    assert "succeeded" in work_log.title
    assert "Toplantı" not in work_log.content  # bookkeeping only

    # A second delivery of the task is a no-op.
    run_connector_write(str(execution.id))
    assert len(server.calls) == 1


def test_request_time_validation(fake_manifest, workspace, owner, enable, server, web_client):
    # Not enabled yet -> refused before any row exists.
    assert _request(web_client, owner, workspace, {"title": "a", "body": "b"}).status_code == 400
    enable(fake_manifest, workspace)
    assert _request(web_client, owner, workspace, {"title": "a"}).status_code == 400
    assert _request(web_client, owner, workspace, "not-an-object").status_code == 400
    assert not ApprovalRequest.objects.exists()


def test_binding_disabled_before_approval_is_connector_changed(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    set_binding_enabled(
        workspace=workspace, actor=owner, connector="fake", enabled=False, channel=CHANNEL_WEB
    )
    assert approve(request_id).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "failed"
    assert execution.error_type == "connector_changed"
    assert server.calls == []
    log = AuditLog.objects.get(action="connector.write.failed")
    assert log.metadata["changed"] == "binding"


def test_schema_drift_before_execution_is_never_sent(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    server.schemas["fake_create_note"]["properties"]["cc"] = {"type": "string"}
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "failed"
    assert execution.error_type == "pin_mismatch"
    assert server.calls == []
    binding = ConnectorBinding.objects.get()
    assert binding.tool_status("fake_create_note") == "mismatch"


def test_manifest_version_change_is_connector_changed(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    approval = ApprovalRequest.objects.get(id=request_id)
    approval.payload["manifest_version"] = 0
    approval.save(update_fields=["payload"])
    approve(request_id)
    assert ConnectorWriteExecution.objects.get().error_type == "connector_changed"
    assert server.calls == []


def test_lost_worker_is_unknown_outcome_and_never_resent(
    fake_manifest, workspace, owner, enable, server, web_client, monkeypatch, approve
):
    enable(fake_manifest, workspace)
    _without_dispatch(monkeypatch, fake_manifest)
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "pending"
    # A worker claims it and dies around the send: lease expired, still running.
    ConnectorWriteExecution.objects.filter(id=execution.id).update(
        status="running",
        started_at=timezone.now() - timedelta(minutes=10),
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )
    assert recover_stale_connector_writes() == {
        "redispatched": 0,
        "unknown_outcome": 1,
        "direct_unknown_outcome": 0,
    }
    execution.refresh_from_db()
    assert execution.status == "unknown_outcome"
    assert execution.error_type == "worker_lost"
    run_connector_write(str(execution.id))  # a late redelivery
    assert server.calls == []
    assert Memory.objects.filter(memory_type="work_log", title__contains="unknown_outcome").exists()


def test_lost_dispatch_is_redispatched_exactly_once(
    fake_manifest, workspace, owner, enable, server, web_client, monkeypatch, approve
):
    enable(fake_manifest, workspace)
    with monkeypatch.context() as patch:
        _without_dispatch(patch, fake_manifest)
        request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()[
            "id"
        ]
        approve(request_id)
    ConnectorWriteExecution.objects.update(updated_at=timezone.now() - timedelta(minutes=5))
    assert recover_stale_connector_writes()["redispatched"] == 1
    assert recover_stale_connector_writes()["redispatched"] == 0
    assert len(server.calls) == 1
    assert ConnectorWriteExecution.objects.get().status == "succeeded"


def test_timeout_after_send_is_unknown_outcome(
    fake_manifest, workspace, owner, enable, server, web_client, settings, approve
):
    import anyio

    settings.CONNECTOR_WRITE_TIMEOUT_SECONDS = 0.5
    enable(fake_manifest, workspace)

    async def slow(name):
        await anyio.sleep(3)

    server.on_call = slow
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "unknown_outcome"
    assert execution.error_type == "timeout"
    assert len(server.calls) == 1


def test_a_server_tool_error_is_failed(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    server.note_is_error = True
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "tool_error")


def test_cli_can_request_but_only_web_decides(
    fake_manifest, workspace, owner, enable, server, web_client
):
    enable(fake_manifest, workspace)
    approval = create_request(
        workspace=workspace,
        actor=owner,
        tool_name=GATE,
        payload={"arguments": {"title": "a", "body": "b"}},
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_CLI,
    )
    from approvals.services import ApprovalChannelForbidden, approve_request

    with pytest.raises(ApprovalChannelForbidden):
        approve_request(approval_request=approval, actor=owner, channel=CHANNEL_CLI)
    assert server.calls == []
    assert not ConnectorWriteExecution.objects.exists()


def test_scope_and_serializer_run_at_request_time(fake_manifest, workspace, owner, enable, server):
    enable(fake_manifest, workspace)
    with pytest.raises(InvalidTransition):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name=GATE,
            payload={"arguments": {"title": "x" * 500, "body": "b"}},
            origin=ApprovalRequest.Origin.HUMAN,
            channel=CHANNEL_WEB,
        )


def test_describe_execution_is_status_only(
    fake_manifest, workspace, owner, enable, server, web_client, approve
):
    enable(fake_manifest, workspace)
    request_id = _request(web_client, owner, workspace, {"title": "a", "body": "b"}).json()["id"]
    approve(request_id)
    execution = web_client(owner).get(f"{URL}/{request_id}").json()["execution"]
    assert execution["status"] == "succeeded"
    assert "result" not in execution


def test_fake_manifest_schema_constants_are_what_the_server_offers():
    assert fakes.NOTE_SCHEMA["required"] == ["title", "body"]
