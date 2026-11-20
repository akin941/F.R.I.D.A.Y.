"""Terminal bookkeeping for a `DeployRun` (ADR-010 decisions 7 and 8,
brief D10/D13): every terminal state -- rollbacks, refusals and
cancellations included -- gets one audit row and one `work_log` memory.

Both carry Friday-authored facts only: ids, SHAs, the closed-vocabulary
error code and the owner-authored target name/environment. Never remote
output, never a commit title (repo text), never anything from a payload
beyond those owner-authored labels.
"""

from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from memory.models import Memory
from tools.work_log import record_tool_outcome

# Terminal states that need the owner's attention (the UI banner, branch 9).
ATTENTION_STATUSES = frozenset(
    {
        "succeeded_degraded",
        "rolled_back",
        "rollback_failed",
        "health_failed",
        "unknown_outcome",
    }
)


def _short(sha):
    return sha[:12] if sha else "-"


def summary_for(run):
    target = run.target
    lines = [
        f"Deploy run {run.id}: {_short(run.sha)} to {target.name} ({target.environment}), "
        f"app {target.remote_app}, status {run.status}.",
        f"Expected live before: {_short(run.expected_current_sha)}; "
        f"rollback SHA: {_short(run.rollback_sha)}.",
    ]
    if run.error_code:
        lines.append(f"Error: {run.error_code}.")
    if run.rollback_started_at is not None:
        lines.append("An automatic rollback was attempted.")
    return "\n".join(lines)


def record_terminal(run, *, actor=None):
    """Call inside the transaction that wrote the terminal state. Sets
    `finished_at` if the caller did not."""
    if run.finished_at is None:
        run.finished_at = timezone.now()
        run.save(update_fields=["finished_at", "updated_at"])
    record_audit(
        action=f"deploy.{run.status}",
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor is not None else AuditLog.ActorType.SYSTEM,
        workspace=run.workspace,
        resource=run,
        metadata={
            "target": str(run.target_id),
            "approval_request": str(run.approval_request_id),
            "sha": run.sha,
            "expected_current_sha": run.expected_current_sha,
            "rollback_sha": run.rollback_sha,
            "error_code": run.error_code,
            "deploy_started": run.deploy_started_at is not None,
            "rollback_attempted": run.rollback_started_at is not None,
        },
    )
    record_tool_outcome(
        run.approval_request,
        outcome=run.status,
        summary=summary_for(run),
        sensitivity=Memory.Sensitivity.NORMAL,
    )
