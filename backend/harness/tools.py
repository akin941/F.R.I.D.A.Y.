"""`harness_implement` (Gate A) / `harness_push` (Gate B) -- the two real,
registered `ToolSpec`s Phase 3 branch 2 adds (brief §boundary, round-1
correction, O-1 resolved). Mirrors `tools/export.py`'s exact shape:
`create_execution(approval_request)` transitions the durable
`HarnessImplementationJob` row forward, inside the approval's own
transaction; `dispatch(execution_id)` schedules the actual Celery task via
`transaction.on_commit`, swallowing broker-publish failures itself (never
propagating into the approval decision's own response).

`harness` may import `tools` (new one-way dependency this branch adds,
mirroring `agents -> approvals`/`approvals -> tools`; see pyproject.toml's
import-linter contracts) -- `tools` never imports `harness` back.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import serializers

from core.channels import CHANNEL_WEB
from harness.models import HarnessImplementationJob, HarnessJob
from harness.services import HarnessPermissionDenied, list_implementation_jobs, list_jobs
from memory.models import Memory
from tools.owner_reads import (
    bounded_json_lines,
    record_read,
    require_owner_web_reader,
    validate_args,
)
from tools.registry import (
    DATA_CLASS_FIRM,
    DATA_CLASS_PERSONAL,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    ToolSpec,
    ToolValidationError,
    register_read_tool,
    register_tool,
)

logger = logging.getLogger(__name__)


_JOB_CACHE_ATTR = "_harness_job_cache"


def _job_for_request(approval_request):
    """`_get_job_from_payload` memoized on the request instance: the
    approvals list serializes `describe_execution` AND `describe_request`
    per row, and without this each harness row cost two job queries on an
    unpaginated list (Phase 3 branch 6 review P3)."""
    cached = getattr(approval_request, _JOB_CACHE_ATTR, _JOB_CACHE_ATTR)
    if cached is _JOB_CACHE_ATTR:
        cached = _get_job_from_payload(approval_request.payload)
        setattr(approval_request, _JOB_CACHE_ATTR, cached)
    return cached


def _is_linked_gate(approval_request, job, gate_field):
    """Only the gate the harness itself created and linked to this job gets
    a display summary (Phase 3 branch 6 review P3): a legacy unlinked
    `harness_*` row (mintable by a writer before `user_invocable` existed)
    would otherwise render attacker-chosen payload values as if real."""
    from approvals.models import ApprovalRequest

    return (
        approval_request.origin == ApprovalRequest.Origin.HARNESS
        and getattr(job, gate_field) == approval_request.id
    )


def _get_job_from_payload(payload):
    job_id = payload.get("job_id") if payload else None
    if not job_id:
        return None
    try:
        return HarnessImplementationJob.objects.filter(pk=job_id).first()
    except (ValueError, TypeError, ValidationError):
        # Codex code-review P2, branch 2 round 7: `pk` is a UUIDField --
        # a non-UUID `job_id` in a malformed/adversarial payload reaching
        # the generic approvals API makes this filter raise instead of
        # returning `None`, which would otherwise bubble out of
        # `validate_request` as an unhandled 500 rather than the clean
        # `ToolValidationError` (-> 400) every other unknown-job case
        # already gets. Treated identically to "job not found".
        return None


def validate_harness_implement_request(workspace, payload):
    """Tier 1 only (brief §boundary): a DB read, not filesystem access --
    safe to run in the web container at request time (mirrors
    `tools.export.validate_vault_export_request`'s tier split). Confirms
    the referenced job exists, belongs to this workspace, is actually
    awaiting Gate A, and hasn't already been requested -- the job/request
    linking itself happens in `harness.services.
    create_harness_implementation_job`, in the same transaction as this
    validation, so the row this reads is already visible."""
    job = _get_job_from_payload(payload)
    if job is None or job.workspace_id != workspace.id:
        raise ToolValidationError("Unknown harness implementation job.")
    if job.status != HarnessImplementationJob.Status.PENDING_GATE_A:
        raise ToolValidationError("Job is not awaiting Gate A approval.")
    if job.gate_a_request_id is not None:
        raise ToolValidationError("Gate A has already been requested for this job.")


def create_harness_implement_execution(approval_request):
    """`ToolSpec.create_execution` for Gate A -- transitions the job into
    the running-worktree phase (`pending_gate_a` -> `implementing`) inside
    the approval's own transaction, durable before the broker is ever
    contacted (brief §boundary).

    Only transitions if the job is STILL `pending_gate_a` (Codex
    code-review P1, branch 2 round 1): a curator can cancel a job while
    its Gate A request is still pending (`harness.services.
    cancel_harness_implementation_job` moves it straight to `cancelled`
    without touching the still-pending `ApprovalRequest`) -- if a
    different curator later approves that same request anyway, this must
    not blindly revive a cancelled job back into `implementing`. `dispatch`
    below still fires unconditionally either way (`approvals.services.
    approve_request` always schedules it), but that is a safe no-op: the
    Celery task's own `_claim_implementation_job` only ever claims a job
    whose status is actually `implementing`, which this job's status no
    longer is.

    ALSO requires `approval_request.id == job.gate_a_request_id` (Codex
    code-review P2, branch 2 round 7): `harness_implement` is a real,
    registered gated `ToolSpec` -- nothing stops a caller from requesting
    it directly through the generic approvals API with a fabricated
    payload naming an existing job's id, creating a second, UNLINKED
    `ApprovalRequest` for that same job. A status-only check can't tell
    that request apart from the real one `create_harness_implementation_job`
    created; approving the wrong one would still transition the job
    forward under a decision that was never actually informed by this
    job's own goal/context."""
    job_id = approval_request.payload["job_id"]
    with transaction.atomic():
        job = HarnessImplementationJob.objects.select_for_update(of=("self",)).get(pk=job_id)
        if (
            job.status == HarnessImplementationJob.Status.PENDING_GATE_A
            and job.gate_a_request_id == approval_request.id
        ):
            job.status = HarnessImplementationJob.Status.IMPLEMENTING
            job.save(update_fields=["status", "updated_at"])
    return str(job.id)


def dispatch_harness_implement_execution(job_id):
    """`ToolSpec.dispatch` for Gate A. Mirrors
    `tools.export.dispatch_vault_export_run`'s non-propagating shape."""
    from django.conf import settings

    from harness.tasks import run_harness_implementation_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_harness_implementation_job.delay(job_id)
        return
    try:
        run_harness_implementation_job.delay(job_id)
    except Exception:
        logger.exception("Failed to publish harness implementation job %s to the broker", job_id)


def describe_harness_implement_execution(approval_request):
    """`ToolSpec.describe_execution` for Gate A -- a JSON-safe status
    summary for the UI (brief §boundary), mirroring
    `tools.export.describe_vault_export_execution`'s shape."""
    job = _job_for_request(approval_request)
    if job is None:
        return None
    return {
        "status": job.status,
        "failure_reason": job.failure_reason,
        "branch_name": job.branch_name,
    }


def validate_harness_push_request(workspace, payload):
    """Tier 1 (brief §boundary): confirms the referenced job exists,
    belongs to this workspace, and is actually ready for Gate B to be
    requested. `harness.services.request_harness_push` calls
    `approvals.services.create_request` (which runs this check) BEFORE
    flipping the job's own status to `pending_gate_b` -- mirroring
    `create_harness_implementation_job`'s identical ordering for Gate A --
    so the status this validates against is `pending_review`, the
    pre-transition state, not the one the job is about to move to."""
    job = _get_job_from_payload(payload)
    if job is None or job.workspace_id != workspace.id:
        raise ToolValidationError("Unknown harness implementation job.")
    if job.status != HarnessImplementationJob.Status.PENDING_REVIEW:
        raise ToolValidationError("Job is not ready for Gate B to be requested.")
    if job.gate_b_request_id is not None:
        raise ToolValidationError("Gate B has already been requested for this job.")


def create_harness_push_execution(approval_request):
    """`ToolSpec.create_execution` for Gate B -- transitions the job into
    the pushing phase (`pending_gate_b` -> `pushing`). Same status AND
    approval-identity re-check as `create_harness_implement_execution`
    above, one gate over -- see that function's docstring for the full
    rationale. Especially important here: an unlinked `harness_push`
    request could be created (and pass `validate_harness_push_request`)
    while the job is still `pending_review`, well before the REAL Gate B
    request (with the actual diff summary and review verdict in its
    payload) exists at all -- approving that earlier, unlinked request
    once the job naturally reaches `pending_gate_b` must not push under
    a decision that never saw the real payload."""
    job_id = approval_request.payload["job_id"]
    with transaction.atomic():
        job = HarnessImplementationJob.objects.select_for_update(of=("self",)).get(pk=job_id)
        if (
            job.status == HarnessImplementationJob.Status.PENDING_GATE_B
            and job.gate_b_request_id == approval_request.id
        ):
            job.status = HarnessImplementationJob.Status.PUSHING
            job.save(update_fields=["status", "updated_at"])
    return str(job.id)


def dispatch_harness_push_execution(job_id):
    """`ToolSpec.dispatch` for Gate B."""
    from django.conf import settings

    from harness.tasks import run_harness_push_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_harness_push_job.delay(job_id)
        return
    try:
        run_harness_push_job.delay(job_id)
    except Exception:
        logger.exception("Failed to publish harness push job %s to the broker", job_id)


def describe_harness_push_execution(approval_request):
    job = _job_for_request(approval_request)
    if job is None:
        return None
    return {
        "status": job.status,
        "failure_reason": job.failure_reason,
        "produced_commit_sha": job.produced_commit_sha,
        "push_result": job.push_result,
    }


_DISPLAY_CHANGED_FILES_MAX = 50
_DISPLAY_TEXT_MAX_CHARS = 4000


def _text(value, limit=_DISPLAY_TEXT_MAX_CHARS):
    if not isinstance(value, str):
        return ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def describe_harness_implement_request(approval_request):
    """`ToolSpec.describe_request` for Gate A (Phase 3 branch 6): a
    whitelisted summary built from the request's own snapshotted payload;
    for requests created before the snapshot existed, the job's immutable
    creation-time snapshots fill the gap (`goal`, `repo_git_url`,
    `branch_name`, `agent` never change after creation)."""
    payload = approval_request.payload or {}
    job = _job_for_request(approval_request)
    if job is None or job.workspace_id != approval_request.workspace_id:
        return None
    if not _is_linked_gate(approval_request, job, "gate_a_request_id"):
        return None
    return {
        "kind": "harness_implement",
        "job_id": str(job.id),
        "goal": _text(payload.get("goal", job.goal)),
        "repo_name": _text(payload.get("repo_name", ""), 200),
        "repo_git_url": _text(payload.get("repo_git_url", job.repo_git_url), 500),
        "branch_name": _text(payload.get("branch_name", job.branch_name), 255),
        "agent": _text(payload.get("agent", job.agent), 50),
        "reviewer_agent": _text(payload.get("reviewer_agent") or _reviewer_for(job.agent), 50),
    }


def _reviewer_for(agent):
    from harness.review_execution import select_review_agent
    from harness.services import HarnessInvalidTransition

    try:
        return str(select_review_agent(agent))
    except HarnessInvalidTransition:
        return ""


def describe_harness_push_request(approval_request):
    """`ToolSpec.describe_request` for Gate B (Phase 3 branch 6): only the
    values snapshotted into the payload when Gate B was requested -- the
    decider sees exactly what existed at that moment, never live job
    state. Pre-snapshot requests simply show fewer fields."""
    payload = approval_request.payload or {}
    job = _job_for_request(approval_request)
    if job is None or job.workspace_id != approval_request.workspace_id:
        return None
    if not _is_linked_gate(approval_request, job, "gate_b_request_id"):
        return None
    changed_files = payload.get("changed_files")
    if not isinstance(changed_files, list):
        changed_files = []
    changed_files = [_text(item, 500) for item in changed_files if isinstance(item, str)]
    test_result = payload.get("test_result")
    review_verdict = payload.get("review_verdict")
    return {
        "kind": "harness_push",
        "job_id": str(job.id),
        "branch_name": _text(payload.get("branch_name", job.branch_name), 255),
        "produced_commit_sha": _text(payload.get("produced_commit_sha", ""), 64),
        "changed_files": changed_files[:_DISPLAY_CHANGED_FILES_MAX],
        "changed_files_total": len(changed_files),
        "diff_summary": _text(payload.get("diff_summary", "")),
        "test_result": (
            {
                "passed": bool(test_result.get("passed")),
                "command": _text(test_result.get("command", ""), 500),
                "exit_code": test_result.get("exit_code")
                if isinstance(test_result.get("exit_code"), int)
                else None,
            }
            if isinstance(test_result, dict) and test_result
            else None
        ),
        "review": (
            {
                "agent": _text(payload.get("review_agent", ""), 50),
                "verdict": _text(review_verdict, 50),
                "summary": _text(payload.get("review_summary", "")),
            }
            if isinstance(review_verdict, str) and review_verdict
            else None
        ),
    }


def _register():
    register_tool(
        ToolSpec(
            name="harness_implement",
            gated=True,
            create_execution=create_harness_implement_execution,
            dispatch=dispatch_harness_implement_execution,
            validate_request=validate_harness_implement_request,
            describe_execution=describe_harness_implement_execution,
            describe_request=describe_harness_implement_request,
            # System-only gate: created by `create_harness_implementation_job`
            # with origin=HARNESS, never by the router/planner/a direct POST.
            user_invocable=False,
        )
    )
    register_tool(
        ToolSpec(
            name="harness_push",
            gated=True,
            create_execution=create_harness_push_execution,
            dispatch=dispatch_harness_push_execution,
            validate_request=validate_harness_push_request,
            describe_execution=describe_harness_push_execution,
            describe_request=describe_harness_push_request,
            user_invocable=False,
        )
    )
    register_read_tool(HARNESS_JOBS_SPEC)


# --- `harness_jobs_status` read tool (Phase 8A brief D12, branch 6) ----------
#
# An ungated `ReadToolSpec` (registered here because `tools` may never import
# `harness`): the workspace's latest discovery and implementation jobs, for
# "Claude Code oturumlarım ne yapıyor?" next to `coding_sessions_status`.
# Web channel only, a human installation owner with a curator role in the
# conversation's own workspace (`harness.services.list_jobs` re-checks it),
# `for_actor()` -- another workspace's jobs are never listed. The goal is the
# owner's own typed request; model-produced briefings are left out. A job of
# a `sensitive` repo (sensitivity `private`) makes the whole result
# personal-class, like a GitHub read of a sensitive repo (Phase 5 D5): only
# a personal-data provider may consume it. One `read_tool.read` audit row per
# call (`tools.owner_reads`), ids and counts only.

HARNESS_JOBS_TOOL = "harness_jobs_status"
_JOBS_DEFAULT_LIMIT = 10
_JOBS_MAX_LIMIT = 20
_GOAL_MAX_CHARS = 200


class HarnessJobsArgs(serializers.Serializer):
    """Strict arguments: an optional page size, nothing else."""

    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=_JOBS_MAX_LIMIT, default=_JOBS_DEFAULT_LIMIT
    )


def _job_row(job, kind):
    goal = " ".join((job.goal or "").split())
    if len(goal) > _GOAL_MAX_CHARS:
        goal = goal[: _GOAL_MAX_CHARS - 1] + "…"
    return {
        "kind": kind,
        "repo": job.repo.name,
        "status": job.status,
        "failure_reason": job.failure_reason,
        "agent": job.agent,
        "goal": goal,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "elapsed_seconds": job.elapsed_seconds,
    }


def read_harness_jobs(*, workspace, actor, args, channel):
    audit = {"outcome": "ok", "count": 0, "size": 0}
    try:
        require_owner_web_reader(workspace=workspace, actor=actor, channel=channel)
        limit = validate_args(HarnessJobsArgs, args)["limit"]
        try:
            discovery = list(
                list_jobs(workspace=workspace, actor=actor)
                .select_related("repo")
                .order_by("-created_at")[:limit]
            )
            implementation = list(
                list_implementation_jobs(workspace=workspace, actor=actor)
                .select_related("repo")
                .order_by("-created_at")[:limit]
            )
        except HarnessPermissionDenied:
            raise ReadToolError("forbidden") from None
        jobs = sorted(
            [(job, "discovery") for job in discovery]
            + [(job, "implementation") for job in implementation],
            key=lambda pair: pair[0].created_at,
            reverse=True,
        )[:limit]
        rows = [_job_row(job, kind) for job, kind in jobs]
        text, size, truncated = bounded_json_lines(rows)
        if not rows:
            text = "No harness jobs in this workspace."
        private = any(job.sensitivity == Memory.Sensitivity.PRIVATE for job, _ in jobs)
        audit.update(
            {
                "count": len(rows),
                "size": size,
                "truncated": truncated,
                "job_ids": [str(job.id) for job, _ in jobs],
            }
        )
        if private:
            audit["data_class"] = DATA_CLASS_PERSONAL
        return ReadResult(
            text=text,
            size=size,
            truncated=truncated,
            data_class=DATA_CLASS_PERSONAL if private else "",
        )
    except ReadToolError as exc:
        audit["outcome"] = exc.code
        raise
    finally:
        record_read(
            tool=HARNESS_JOBS_TOOL,
            actor=actor,
            workspace=workspace,
            channel=channel,
            metadata=audit,
        )


def harness_jobs_available(workspace):
    """Offered to the router only where the workspace has harness jobs."""
    workspace_id = getattr(workspace, "id", None)
    if workspace_id is None:
        return False
    return (
        HarnessJob.objects.filter(workspace_id=workspace_id).exists()
        or HarnessImplementationJob.objects.filter(workspace_id=workspace_id).exists()
    )


HARNESS_JOBS_SPEC = ReadToolSpec(
    name=HARNESS_JOBS_TOOL,
    description=(
        "the latest harness jobs (discovery and implementation) in this "
        "workspace: repo, status, agent and the owner's goal"
    ),
    argument_serializer=HarnessJobsArgs,
    data_class=DATA_CLASS_FIRM,
    channels=frozenset({CHANNEL_WEB}),
    execute=read_harness_jobs,
    is_available=harness_jobs_available,
    source="harness",
)
