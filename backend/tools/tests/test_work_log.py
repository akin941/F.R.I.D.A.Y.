"""Phase 5 branch 1 (brief D13, ADR-008 decision 4): `record_tool_outcome`
and its first producer, the vault export. Every row is a system-generated,
auto-approved `work_log`, audited as `memory.system_recorded`; its content
is bookkeeping only (counts, error type, ids, date)."""

import uuid
from datetime import date
from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from memory.models import Memory, compute_fingerprint
from tools import export
from tools.models import VaultExportRun
from tools.registry import ToolSpec, register_tool, unregister_tool
from tools.work_log import record_tool_outcome
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
TODAY = date(2026, 9, 27)


@pytest.fixture
def owner():
    return register_user(email="vault-owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture(autouse=True)
def _fixed_day():
    with mock.patch("tools.work_log.timezone.localdate", return_value=TODAY):
        yield


def approved_request(workspace, owner, tool_name="vault_export"):
    return ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name=tool_name,
        status=ApprovalRequest.Status.APPROVED,
        requested_by=owner,
    )


def work_logs(workspace):
    return list(
        Memory.objects.filter(workspace=workspace, memory_type=Memory.Type.WORK_LOG).order_by(
            "created_at"
        )
    )


def make_memory(workspace, title="A memory title", content="Memory body text."):
    return Memory.objects.create(
        workspace=workspace,
        memory_type=Memory.Type.SEMANTIC,
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        title=title,
        content=content,
        fingerprint=compute_fingerprint(content),
    )


class TestRecordToolOutcome:
    def test_records_a_system_generated_approved_row_with_audit(self, workspace, owner):
        request = approved_request(workspace, owner)
        row = record_tool_outcome(
            request,
            outcome="succeeded",
            summary="3 written.",
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        assert row is not None
        assert row.workspace == workspace
        assert row.memory_type == Memory.Type.WORK_LOG
        assert row.source == Memory.Source.SYSTEM_GENERATED
        assert row.status == Memory.Status.APPROVED
        assert row.title == "vault_export: succeeded (2026-09-27)"
        assert row.content == (
            "Gated tool vault_export succeeded on 2026-09-27.\n"
            f"Approval request: {request.id}.\n3 written."
        )
        audit = AuditLog.objects.get(action="memory.system_recorded", resource_id=str(row.id))
        assert audit.actor_type == AuditLog.ActorType.SYSTEM

    def test_sensitivity_is_passed_through(self, workspace, owner):
        row = record_tool_outcome(
            approved_request(workspace, owner),
            outcome="succeeded",
            summary="",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        assert row.sensitivity == Memory.Sensitivity.PRIVATE

    def test_sensitivity_is_required(self, workspace, owner):
        with pytest.raises(TypeError):
            record_tool_outcome(approved_request(workspace, owner), outcome="succeeded", summary="")

    @pytest.mark.parametrize("outcome", ["", "Succeeded", "ok; drop", "a" * 41, None, "x y"])
    def test_outcome_must_be_a_status_token(self, workspace, owner, outcome):
        with pytest.raises(ValueError):
            record_tool_outcome(
                approved_request(workspace, owner),
                outcome=outcome,
                summary="",
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        assert work_logs(workspace) == []

    def test_tool_that_opted_out_records_nothing(self, workspace, owner):
        register_tool(
            ToolSpec(
                name="quiet_tool",
                gated=True,
                create_execution=lambda approval_request: None,
                dispatch=lambda execution_id: None,
                records_work_log=False,
            )
        )
        try:
            result = record_tool_outcome(
                approved_request(workspace, owner, tool_name="quiet_tool"),
                outcome="succeeded",
                summary="toggled",
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        finally:
            unregister_tool("quiet_tool")
        assert result is None
        assert work_logs(workspace) == []

    def test_same_outcome_of_two_executions_is_two_rows(self, workspace, owner):
        for _ in range(2):
            record_tool_outcome(
                approved_request(workspace, owner),
                outcome="succeeded",
                summary="0 written.",
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        assert len(work_logs(workspace)) == 2

    def test_duplicate_is_silent(self, workspace, owner):
        request = approved_request(workspace, owner)
        kwargs = {"outcome": "failed", "summary": "x", "sensitivity": Memory.Sensitivity.NORMAL}
        assert record_tool_outcome(request, **kwargs) is not None
        assert record_tool_outcome(request, **kwargs) is None
        assert len(work_logs(workspace)) == 1

    def test_failure_is_swallowed_and_logged(self, workspace, owner):
        with (
            mock.patch("tools.work_log.record_work_log", side_effect=RuntimeError("boom")),
            mock.patch("tools.work_log.logger") as logger,
        ):
            result = record_tool_outcome(
                approved_request(workspace, owner),
                outcome="succeeded",
                summary="",
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        assert result is None
        logger.exception.assert_called_once()

    def test_summary_is_bounded(self, workspace, owner):
        row = record_tool_outcome(
            approved_request(workspace, owner),
            outcome="succeeded",
            summary="x" * 10_000,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        assert len(row.content) < 2200


def _claimed_run(workspace, owner):
    request = approved_request(workspace, owner)
    run_id = export.create_vault_export_run(request)
    return export.claim_vault_export_run(run_id)


class TestVaultExportProducer:
    def test_success_records_counts_only(self, workspace, owner, tmp_path, settings):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        make_memory(workspace, title="SECRET-MEMORY-TITLE", content="SECRET-MEMORY-BODY")
        run_id, token = _claimed_run(workspace, owner)
        export.process_claimed_vault_export_run(run_id, token)
        run = VaultExportRun.objects.get(pk=run_id)
        assert run.status == VaultExportRun.Status.SUCCEEDED
        [row] = work_logs(workspace)
        assert row.title == "vault_export: succeeded (2026-09-27)"
        assert f"Vault export run {run.id}: 1 written, 0 updated, 0 removed." in row.content
        assert f"Approval request: {run.approval_request_id}." in row.content
        assert "SECRET-MEMORY" not in row.title + row.content
        assert str(tmp_path) not in row.content
        assert row.sensitivity == Memory.Sensitivity.NORMAL
        assert AuditLog.objects.filter(
            action="memory.system_recorded", resource_id=str(row.id)
        ).exists()

    def test_retryable_failure_records_nothing(self, workspace, owner, tmp_path, settings):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path / "does-not-exist")
        run_id, token = _claimed_run(workspace, owner)
        export.process_claimed_vault_export_run(run_id, token)
        assert VaultExportRun.objects.get(pk=run_id).status == VaultExportRun.Status.PENDING
        assert work_logs(workspace) == []

    def test_terminal_failure_records_the_error_type_only(
        self, workspace, owner, tmp_path, settings
    ):
        missing = tmp_path / "does-not-exist"
        settings.OBSIDIAN_VAULT_PATH = str(missing)
        request = approved_request(workspace, owner)
        run_id = export.create_vault_export_run(request)
        for _ in range(export.MAX_EXPORT_RETRIES):
            export.process_claimed_vault_export_run(*export.claim_vault_export_run(run_id))
        assert VaultExportRun.objects.get(pk=run_id).status == VaultExportRun.Status.FAILED
        [row] = work_logs(workspace)
        assert row.title == "vault_export: failed (2026-09-27)"
        assert (
            f"Vault export run {run_id} failed after {export.MAX_EXPORT_RETRIES} attempts: "
            f"{VaultExportRun.ErrorType.PATH_VALIDATION_FAILED}." in row.content
        )
        assert str(missing) not in row.content

    def test_a_work_log_failure_never_undoes_the_finalize(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        run_id, token = _claimed_run(workspace, owner)
        with mock.patch("tools.work_log.record_work_log", side_effect=RuntimeError("boom")):
            export.process_claimed_vault_export_run(run_id, token)
        assert VaultExportRun.objects.get(pk=run_id).status == VaultExportRun.Status.SUCCEEDED
        assert AuditLog.objects.filter(action="vault_export").exists()
        assert work_logs(workspace) == []

    def test_fenced_out_finalize_records_nothing(self, workspace, owner):
        run_id, _ = _claimed_run(workspace, owner)
        assert export.finalize_vault_export_success(run_id, uuid.uuid4(), (1, 0, 0)) is False
        assert work_logs(workspace) == []
