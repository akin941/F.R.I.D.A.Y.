"""The `deploy` gate (ADR-010 decision 7, brief D10): a registered,
system-only (`user_invocable=False`) gated `ToolSpec`, minted only by
`deploys.services.request_deploy` and decided like every gate -- explicit,
separate, audited, owner-only, web-only (`approvals.services`).

`create_execution` runs inside the approval's own transaction and never
raises for a refusal: a changed target configuration (`target_changed`), a
disabled target (`target_disabled`) or a busy target (`target_busy`) leaves
a terminal `refused` `DeployRun` with an audit row and a work_log entry --
a named, audited refusal, never a 500. The busy check is a locked read of
the target's active runs; the partial unique constraint is the backstop,
and its `IntegrityError` maps to the same refusal.
"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from deploys.models import CONFIG_HASH_FIELDS, DeployRun, DeployTarget, compute_config_hash
from deploys.outcomes import ATTENTION_STATUSES, record_terminal
from deploys.services import DEPLOY_TOOL_NAME
from deploys.validation import TargetFieldError, is_sha, validate_remote_app
from tools.registry import ToolSpec, ToolValidationError, register_tool

logger = logging.getLogger(__name__)


def _target_for(workspace_id, payload):
    target_id = payload.get("target_id") if isinstance(payload, dict) else None
    if not isinstance(target_id, str):
        return None
    try:
        return DeployTarget.objects.filter(pk=target_id, workspace_id=workspace_id).first()
    except (ValueError, TypeError, ValidationError):  # malformed UUID -> unknown target
        return None


def _payload_is_well_formed(payload):
    if not isinstance(payload, dict):
        return False
    target = payload.get("target")
    plan = payload.get("health_plan")
    if not isinstance(target, dict) or not isinstance(plan, dict):
        return False
    if set(target) != set(CONFIG_HASH_FIELDS):
        return False
    if compute_config_hash(target) != payload.get("config_hash"):
        return False
    try:
        validate_remote_app(target.get("remote_app"))
    except TargetFieldError:
        return False
    return all(
        is_sha(payload.get(key)) for key in ("sha", "expected_current_sha", "rollback_sha")
    ) and isinstance(payload.get("auto_rollback"), bool)


def validate_deploy_request(workspace, payload):
    """Tier 1 (no network): the payload `request_deploy` built is
    well-formed, self-consistent (its target snapshot hashes to its
    `config_hash`) and names an enabled target of this workspace whose
    configuration still matches."""
    if not _payload_is_well_formed(payload):
        raise ToolValidationError("Malformed deploy request.")
    target = _target_for(workspace.id, payload)
    if target is None:
        raise ToolValidationError("Unknown deploy target.")
    if not target.enabled:
        raise ToolValidationError("Deploy target is not enabled.")
    if target.config_hash != payload["config_hash"]:
        raise ToolValidationError("Deploy target configuration changed.")


def _refuse(approval_request, target, error_code):
    payload = approval_request.payload or {}
    run = DeployRun.objects.create(
        workspace=approval_request.workspace,
        approval_request=approval_request,
        target=target,
        sha=payload.get("sha", ""),
        expected_current_sha=payload.get("expected_current_sha", ""),
        rollback_sha=payload.get("rollback_sha", ""),
        plan=payload,
        status=DeployRun.Status.REFUSED,
        error_code=error_code,
    )
    record_terminal(run)
    return run


def _active_run_exists(target):
    return DeployRun.objects.filter(target=target, status__in=DeployRun.ACTIVE_STATUSES).exists()


def create_deploy_execution(approval_request):
    """`ToolSpec.create_execution`: a `pending` run, or a terminal
    `refused` one. Returns the run id either way."""
    payload = approval_request.payload or {}
    target = _target_for(approval_request.workspace_id, payload)
    if target is not None:
        # Serializes concurrent approvals for the same target: the second
        # one's busy check below sees the first one's committed run.
        target = DeployTarget.objects.select_for_update().get(pk=target.pk)
    if target is None:
        # PROTECT keeps a target with runs; one deleted before its first
        # run leaves nothing to attach a refusal row to. The approval still
        # records the decision; nothing can run.
        logger.warning("deploy request %s names no existing target", approval_request.id)
        return None
    if not target.enabled:
        return str(_refuse(approval_request, target, DeployRun.ErrorCode.TARGET_DISABLED).id)
    if target.config_hash != payload.get("config_hash") or not _payload_is_well_formed(payload):
        return str(_refuse(approval_request, target, DeployRun.ErrorCode.TARGET_CHANGED).id)
    if _active_run_exists(target):
        return str(_refuse(approval_request, target, DeployRun.ErrorCode.TARGET_BUSY).id)
    try:
        with transaction.atomic():
            run = DeployRun.objects.create(
                workspace=approval_request.workspace,
                approval_request=approval_request,
                target=target,
                sha=payload["sha"],
                expected_current_sha=payload["expected_current_sha"],
                rollback_sha=payload["rollback_sha"],
                plan=payload,
            )
    except IntegrityError:
        # The partial unique constraint (one active run per target): the
        # backstop behind the locked check above -- same named refusal.
        return str(_refuse(approval_request, target, DeployRun.ErrorCode.TARGET_BUSY).id)
    return str(run.id)


def dispatch_deploy_execution(run_id):
    """`ToolSpec.dispatch`: publishes the run to the `deploy` queue.
    Non-propagating (a broker failure never reaches the approval response;
    the recovery scan re-dispatches a stale `pending` run). A refused run
    has nothing to dispatch."""
    from deploys.tasks import run_deploy

    if run_id is None:
        return
    if not DeployRun.objects.filter(pk=run_id, status=DeployRun.Status.PENDING).exists():
        return
    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_deploy.delay(run_id)
        return
    try:
        run_deploy.delay(run_id)
    except Exception:
        logger.exception("Failed to publish deploy run %s to the broker", run_id)


def _short(sha):
    return sha[:7] if isinstance(sha, str) else ""


def display_text(payload):
    """The dialog sentence (brief D10), built only from the snapshot."""
    target = payload.get("target") or {}
    plan = payload.get("health_plan") or {}
    port = target.get("ssh_port")
    destination = f"{target.get('ssh_user')}@{target.get('ssh_host')}" + (
        "" if port in (22, None) else f":{port}"
    )
    text = (
        f"Deploy `{_short(payload.get('sha'))}` to {target.get('name')} "
        f"{target.get('environment')} ({destination}, app `{target.get('remote_app')}`). "
        f"Current `{_short(payload.get('expected_current_sha'))}`. "
    )
    window = plan.get("window_seconds")
    if payload.get("auto_rollback"):
        text += (
            f"If health fails within {window} s, Jarvis rolls back to "
            f"`{_short(payload.get('rollback_sha'))}`."
        )
    else:
        text += (
            f"If health fails within {window} s, Jarvis stops and reports it; "
            "nothing is rolled back without a new decision."
        )
    return text


def describe_deploy_request(approval_request):
    """`ToolSpec.describe_request`: the whole decided plan, from the
    payload snapshot only. The commit title is repo text and is labelled
    as such (`commit_title_trust: "repo_text"`)."""
    if approval_request.tool_name != DEPLOY_TOOL_NAME:
        return None
    payload = approval_request.payload or {}
    if not _payload_is_well_formed(payload):
        return None
    target = payload["target"]
    plan = payload["health_plan"]
    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    return {
        "kind": "deploy",
        "text": display_text(payload),
        "target_id": payload.get("target_id"),
        "target_name": target["name"],
        "environment": target["environment"],
        "ssh_destination": f"{target['ssh_user']}@{target['ssh_host']}",
        "ssh_port": target["ssh_port"],
        "remote_app": target["remote_app"],
        "repo_git_url": target["repo_git_url"],
        "tracked_branch": target["tracked_branch"],
        "sha": payload["sha"],
        "current_sha": payload["expected_current_sha"],
        "rollback_sha": payload["rollback_sha"],
        "auto_rollback": payload["auto_rollback"],
        "commit_title": commit.get("title", ""),
        "commit_title_trust": "repo_text",
        "ci_conclusion": commit.get("ci_conclusion", "unknown"),
        "commit_source": commit.get("source", "manual"),
        "require_ci_success": target["require_ci_success"],
        "health": {
            "window_seconds": plan.get("window_seconds"),
            "checks": plan.get("checks"),
            "interval_seconds": plan.get("interval_seconds"),
            "pass_threshold": plan.get("pass_threshold"),
            "fail_threshold": plan.get("fail_threshold"),
            "health_url": plan.get("health_url", ""),
        },
        "verbs": list(payload.get("verbs") or []),
    }


def describe_deploy_execution(approval_request):
    """`ToolSpec.describe_execution`: run status for the UI, closed
    vocabulary only (never remote output)."""
    run = getattr(approval_request, "deploy_run", None)
    if run is None:
        return None
    return {
        "run_id": str(run.id),
        "status": run.status,
        "error_code": run.error_code,
        "active": run.is_active,
        "needs_attention": run.status in ATTENTION_STATUSES,
        "deploy_started": run.deploy_started_at is not None,
        "rollback_attempted": run.rollback_started_at is not None,
        "health_checks": len(run.health_checks or []),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _register():
    register_tool(
        ToolSpec(
            name=DEPLOY_TOOL_NAME,
            gated=True,
            create_execution=create_deploy_execution,
            dispatch=dispatch_deploy_execution,
            validate_request=validate_deploy_request,
            describe_execution=describe_deploy_execution,
            describe_request=describe_deploy_request,
            # System-only (ADR-010 decision 2): minted by
            # `deploys.services.request_deploy`, never by the router, the
            # agent planner or a direct `POST /approvals`.
            user_invocable=False,
        )
    )
