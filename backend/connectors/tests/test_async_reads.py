"""Async connector reads (Phase 12 D11) against the fake n8n trigger behind
the in-process relay: start (refusals, row, dispatch after commit), the one
call (never re-sent), timeout, lost dispatch / lost worker recovery, pins,
the unattended guard, the audit rows and the registry plumbing."""

import uuid
from datetime import timedelta

import anyio
import pytest
from django.utils import timezone

from audit.models import AuditLog
from audit.unattended import recorded_unattended_run
from connectors import async_reads
from connectors.models import ConnectorAsyncRead, ConnectorBinding
from connectors.services import check_binding_pins
from connectors.tasks import recover_async_reads, run_async_read
from connectors.tests import fakes
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_TELEGRAM,
    CHANNEL_WEB,
)
from core.unattended import UnattendedDenied
from tools.registry import (
    ASYNC_FAILED,
    ASYNC_OK,
    ASYNC_PENDING,
    ASYNC_TIMED_OUT,
    AsyncReadHandle,
    ReadToolError,
    fetch_async_read,
    get_read_tool,
    register_async_read_listener,
    start_async_read,
    unregister_async_read_listener,
)

pytestmark = pytest.mark.django_db

TOOL = "n8n_server_status"
Status = ConnectorAsyncRead.Status


@pytest.fixture
def notified():
    seen = []

    def listener(handle):
        seen.append(handle)

    register_async_read_listener(listener)
    yield seen
    unregister_async_read_listener(listener)


def _start(workspace, owner, *, channel=CHANNEL_WEB, args=None):
    return start_async_read(
        get_read_tool(TOOL),
        workspace=workspace,
        actor=owner,
        args={"scope": "summary"} if args is None else args,
        channel=channel,
    )


@pytest.mark.parametrize("channel", [CHANNEL_WEB, CHANNEL_NODE_VOICE])
def test_a_started_read_runs_once_after_commit(
    n8n_binding, workspace, owner, n8n, committed, notified, channel
):
    with committed():
        handle = _start(workspace, owner, channel=channel)
        row = ConnectorAsyncRead.objects.get(pk=handle.id)
        assert row.status == Status.PENDING
        assert row.arguments == {"scope": "summary"}
        assert n8n.calls == []  # nothing is sent before the commit
    row.refresh_from_db()
    assert row.status == Status.OK
    assert n8n.calls == [(fakes.N8N_STATUS_TOOL, {"scope": "summary"})]
    assert row.arguments == {}  # only the sha256 stays once the read ended
    assert row.result_text == n8n.status_text
    outcome = fetch_async_read(handle)
    assert outcome.status == ASYNC_OK and outcome.terminal
    assert outcome.result.text == n8n.status_text
    assert notified == [handle]
    # A second delivery of the same task never sends again.
    run_async_read(handle.id)
    assert len(n8n.calls) == 1


def test_the_audit_rows_carry_no_content(n8n_binding, workspace, owner, n8n, committed):
    with committed():
        handle = _start(workspace, owner)
    rows = AuditLog.objects.filter(action="connector.read", metadata__mode="async")
    assert rows.count() == 1
    metadata = rows.get().metadata
    assert metadata["outcome"] == "ok"
    assert metadata["tool"] == TOOL
    assert metadata["connector"] == "n8n"
    assert len(metadata["args_sha256"]) == 64
    flat = str(list(AuditLog.objects.values_list("metadata", flat=True)))
    assert n8n.status_text not in flat
    assert "summary" not in flat
    assert ConnectorAsyncRead.objects.get(pk=handle.id).arguments_sha256 == metadata["args_sha256"]


@pytest.mark.parametrize(
    "channel", [CHANNEL_CLI, CHANNEL_HOOK, CHANNEL_OPENAI, CHANNEL_TELEGRAM, "node"]
)
def test_other_channels_are_refused_before_any_row(n8n_binding, workspace, owner, n8n, channel):
    with pytest.raises(ReadToolError) as info:
        _start(workspace, owner, channel=channel)
    assert info.value.code == "channel_forbidden"
    assert not ConnectorAsyncRead.objects.exists()
    assert n8n.calls == []
    audit = AuditLog.objects.get(action="connector.read")
    assert audit.metadata["outcome"] == "channel_forbidden"
    assert audit.metadata["mode"] == "async"


def test_invalid_arguments_are_refused_before_any_row(n8n_binding, workspace, owner, n8n):
    with pytest.raises(ReadToolError) as info:
        _start(workspace, owner, args={"scope": "everything"})
    assert info.value.code == "invalid_arguments"
    assert not ConnectorAsyncRead.objects.exists()


def test_without_a_binding_nothing_starts(n8n, workspace, owner):
    with pytest.raises(ReadToolError) as info:
        _start(workspace, owner)
    assert info.value.code == "not_available"
    assert not ConnectorAsyncRead.objects.exists()


def test_a_firm_workspace_cannot_bind_n8n(n8n, firm, owner):
    from connectors.services import ConnectorInvalidTransition, set_binding_enabled

    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=firm, actor=owner, connector="n8n", enabled=True, channel=CHANNEL_WEB
        )


def test_inside_an_unattended_run_start_is_denied_before_any_row(
    n8n_binding, workspace, owner, n8n
):
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-n8n", workspace=workspace):
            _start(workspace, owner)
    assert not ConnectorAsyncRead.objects.exists()
    assert n8n.calls == []
    assert AuditLog.objects.filter(action="proactive.unattended_denied").count() == 1


def test_a_slow_workflow_times_out_and_is_never_resent(
    n8n_binding, workspace, owner, n8n, committed, notified, settings
):
    settings.N8N_ASYNC_READ_TIMEOUT_SECONDS = 1

    async def slow(_name):
        await anyio.sleep(3)

    n8n.on_call = slow
    with committed():
        handle = _start(workspace, owner)
    row = ConnectorAsyncRead.objects.get(pk=handle.id)
    assert row.status == Status.TIMED_OUT
    assert row.error_code == "timeout"
    assert fetch_async_read(handle).status == ASYNC_TIMED_OUT
    assert notified == [handle]
    run_async_read(handle.id)
    recover_async_reads()
    assert len(n8n.calls) == 1


def test_a_tool_error_ends_failed(n8n_binding, workspace, owner, n8n, committed):
    n8n.status_is_error = True
    with committed():
        handle = _start(workspace, owner)
    outcome = fetch_async_read(handle)
    assert outcome.status == ASYNC_FAILED and outcome.error_code == "tool_error"
    assert outcome.result is None


def test_a_lost_worker_is_failed_by_the_recovery_scan_never_resent(
    n8n_binding, workspace, owner, n8n, notified, django_capture_on_commit_callbacks
):
    handle = _start(workspace, owner)  # its dispatch never committed: lost
    row = ConnectorAsyncRead.objects.get(pk=handle.id)
    claimed = async_reads.claim_async_read(row.pk)  # a worker claimed it, then died
    assert claimed is not None
    ConnectorAsyncRead.objects.filter(pk=row.pk).update(
        started_at=timezone.now() - timedelta(hours=1)
    )
    with django_capture_on_commit_callbacks(execute=True):
        counts = recover_async_reads()
    assert counts["worker_lost"] == 1
    row.refresh_from_db()
    assert row.status == Status.FAILED and row.error_code == "worker_lost"
    assert n8n.calls == []
    assert notified == [handle]
    run_async_read(handle.id)
    assert n8n.calls == []


def test_a_lost_dispatch_is_republished_once(
    n8n_binding, workspace, owner, n8n, django_capture_on_commit_callbacks
):
    handle = _start(workspace, owner)  # never committed -> never dispatched
    ConnectorAsyncRead.objects.filter(pk=handle.id).update(
        created_at=timezone.now() - timedelta(seconds=90)
    )
    with django_capture_on_commit_callbacks(execute=True):
        counts = recover_async_reads()
    assert counts["redispatched"] == 1
    assert ConnectorAsyncRead.objects.get(pk=handle.id).status == Status.OK
    assert len(n8n.calls) == 1
    assert recover_async_reads()["redispatched"] == 0


def test_a_pending_read_past_its_deadline_is_failed_unsent(n8n_binding, workspace, owner, n8n):
    handle = _start(workspace, owner)
    ConnectorAsyncRead.objects.filter(pk=handle.id).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    assert recover_async_reads()["dispatch_lost"] == 1
    row = ConnectorAsyncRead.objects.get(pk=handle.id)
    assert row.status == Status.FAILED and row.error_code == "dispatch_lost"
    assert n8n.calls == []


def test_old_result_text_is_cleared(n8n_binding, workspace, owner, n8n, committed):
    with committed():
        handle = _start(workspace, owner)
    ConnectorAsyncRead.objects.filter(pk=handle.id).update(
        finished_at=timezone.now() - timedelta(days=2)
    )
    assert recover_async_reads()["cleared"] == 1
    assert ConnectorAsyncRead.objects.get(pk=handle.id).result_text == ""


def test_a_changed_upstream_schema_disables_the_tool_sticky(
    n8n, workspace, owner, enable, committed
):
    from connectors.manifest import get_manifest

    original = dict(n8n.schemas[fakes.N8N_STATUS_TOOL])
    n8n.schemas[fakes.N8N_STATUS_TOOL] = {**original, "required": []}
    binding = enable(get_manifest("n8n"), workspace)
    assert binding.tool_status(TOOL) == "mismatch"
    assert binding.tool_status("n8n_home_backup") == "ok"
    assert AuditLog.objects.filter(action="connector.pin_mismatch", metadata__tool=TOOL).exists()
    with pytest.raises(ReadToolError) as info:
        _start(workspace, owner)
    assert info.value.code == "not_available"
    # Flipping back does not re-enable it under the same manifest version.
    n8n.schemas[fakes.N8N_STATUS_TOOL] = original
    check_binding_pins(binding.id)
    assert ConnectorBinding.objects.get(pk=binding.pk).tool_status(TOOL) == "mismatch"
    assert n8n.calls == []


def test_a_schema_changed_after_start_fails_the_read_unsent(
    n8n_binding, workspace, owner, n8n, committed
):
    with committed():
        handle = _start(workspace, owner)
        n8n.schemas[fakes.N8N_STATUS_TOOL] = {"type": "object"}
    outcome = fetch_async_read(handle)
    assert outcome.status == ASYNC_FAILED and outcome.error_code == "pin_mismatch"
    assert n8n.calls == []
    assert ConnectorBinding.objects.get(pk=n8n_binding.pk).tool_status(TOOL) == "mismatch"


def test_fetch_of_an_unknown_handle_is_failed():
    outcome = fetch_async_read(AsyncReadHandle(id=str(uuid.uuid4()), tool=TOOL))
    assert outcome.status == ASYNC_FAILED and outcome.error_code == "not_found"
    gone = fetch_async_read(AsyncReadHandle(id=str(uuid.uuid4()), tool="no_such_tool"))
    assert gone.error_code == "not_available"


def test_a_pending_read_reads_pending(n8n_binding, workspace, owner, n8n):
    handle = _start(workspace, owner)
    assert fetch_async_read(handle).status == ASYNC_PENDING
    assert not fetch_async_read(handle).terminal
