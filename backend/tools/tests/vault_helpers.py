"""Shared helpers for the vault export tests: a real approved request plus a
claimed `VaultExportRun`, so `export_workspace_memories`/`import_inbox`
pass the approved-run gate (Phase 8B branch 7, brief D12)."""

from approvals.models import ApprovalRequest
from tools import export
from tools.models import VaultExportRun


def claimed_run(workspace, requested_by=None):
    """`(run, claim_token)` for a fresh approved request in `workspace`."""
    request = ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name="vault_export",
        status=ApprovalRequest.Status.APPROVED,
        requested_by=requested_by or workspace.owner,
    )
    run_id = export.create_vault_export_run(request)
    _, token = export.claim_vault_export_run(run_id)
    return VaultExportRun.objects.get(pk=run_id), token


def export_in_run(vault_root_real, workspace, stats=None):
    """`export.export_workspace_memories` inside a freshly claimed run."""
    run, token = claimed_run(workspace)
    with export.claimed_run_scope(run.id, token):
        return export.export_workspace_memories(vault_root_real, workspace, stats)


def run_full(vault_root_real, workspace, stats=None):
    """Inbox import + export inside one claimed run; returns the stats."""
    stats = stats if stats is not None else export.ExportStats()
    run, token = claimed_run(workspace)
    with export.claimed_run_scope(run.id, token):
        export.import_inbox(vault_root_real, workspace, stats)
        export.export_workspace_memories(vault_root_real, workspace, stats)
    return stats
