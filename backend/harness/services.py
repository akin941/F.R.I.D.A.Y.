"""HarnessRepo/HarnessJob services -- the only sanctioned write path for
either model.

Role policy (brief §1: "curator-gated CRUD" -- the whole registry, not just
writes. Registering a real git URL and flipping `enabled=True` grants a repo
harness access; even reading which repos are registered is curator-only,
since a viewer/member has no reason to see or reason about that allowlist).
HarnessJob (branch 2) is curator-gated the same way -- launching a container
against a registered repo is not a lighter-weight action than registering
the repo in the first place:

    action                     owner/admin   member/viewer
    read/list                  yes           no
    create                     yes           no
    edit fields                yes           no
    delete / cancel            yes           no
"""

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import ProtectedError

from approvals.models import ApprovalRequest
from approvals.services import create_request, revoke_request
from audit.services import record_audit
from core.channels import CHANNEL_SYSTEM
from core.repo_identity import is_self_repo
from harness import policy
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo, HarnessReview
from memory.models import Memory
from workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}

EDITABLE_FIELDS = (
    "name",
    "git_url",
    "default_branch",
    "runner",
    "enabled",
    "sensitive",
    "test_command",
)

# Agent selection (Phase 5 D11): `settings.HARNESS_AGENT_POLICY`, read
# through `harness.policy`, replaces the Phase 3 `_SELECTABLE_DISCOVERY_/
# _IMPLEMENTATION_/_REVIEW_AGENTS` sets. The capability split those sets
# enforced survives as the per-kind `selectable` lists (an agent cleared
# for discovery is not thereby cleared to write), and the reviewer is the
# policy's explicit implementer -> reviewer pairing, never the implementer
# itself (system check at boot + `policy.reviewer_for` at run time).


class HarnessServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class HarnessPermissionDenied(HarnessServiceError):
    """Actor's role does not allow the action -> 403."""

    code = "harness.permission_denied"


class HarnessInvalidTransition(HarnessServiceError):
    """Illegal state-machine move, or an unselectable agent/disabled repo
    -> 400."""

    code = "harness.invalid_transition"


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _require_curator(actor, workspace, action):
    if get_role(actor, workspace) not in _CURATOR_ROLES:
        raise HarnessPermissionDenied(f"Role does not allow {action}.")


def _locked(repo, actor):
    """Re-fetch the row under lock, scoped to the actor, inside the caller's
    transaction -- mirrors `tasks.services._locked`. The lock query itself
    enforces workspace scope (membership join) so a forged or stale instance
    can never reach the mutation path."""
    locked = (
        HarnessRepo.objects.filter(pk=repo.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise HarnessPermissionDenied("Repo no longer exists.")
    return locked


def _record(repo, action, actor, **metadata):
    # Audit metadata carries ids/flags only -- never `git_url` (a repo
    # location isn't a secret, but it isn't audit bookkeeping content
    # either; the resource pk is enough to look the row up).
    record_audit(
        action=action,
        actor=actor,
        workspace=repo.workspace,
        resource=repo,
        metadata=metadata,
    )


def list_repos(*, workspace, actor):
    _require_curator(actor, workspace, "listing harness repos")
    return HarnessRepo.objects.for_actor(actor, workspace=workspace)


def get_repo(*, workspace, actor, repo_id):
    _require_curator(actor, workspace, "viewing this harness repo")
    return HarnessRepo.objects.for_actor(actor, workspace=workspace).filter(id=repo_id).first()


@transaction.atomic
def create_repo(
    *,
    workspace,
    actor,
    name,
    git_url,
    default_branch="main",
    runner=HarnessRepo.Runner.MAC_LOCAL,
    enabled=False,
    sensitive=False,
    test_command="",
):
    _require_curator(actor, workspace, "registering a harness repo")
    repo = HarnessRepo.objects.create(
        workspace=workspace,
        name=name,
        git_url=git_url,
        default_branch=default_branch,
        runner=runner,
        enabled=enabled,
        sensitive=sensitive,
        test_command=test_command,
    )
    _record(repo, "harness.repo_registered", actor, enabled=enabled, sensitive=sensitive)
    return repo


@transaction.atomic
def edit_repo(*, repo, actor, **fields):
    # Lock first, authorize against the locked row's own workspace -- a
    # forged/stale `repo` instance whose cached `.workspace` attribute
    # disagrees with the row's real current workspace must not let a
    # curator-in-workspace-B-but-only-member-in-A actor slip through by
    # having the pre-lock check run against the wrong workspace (Codex
    # code-review P2, branch 1 round 1).
    repo = _locked(repo, actor)
    _require_curator(actor, repo.workspace, "editing this harness repo")
    changed = []
    for field_name in EDITABLE_FIELDS:
        if field_name in fields and fields[field_name] != getattr(repo, field_name):
            setattr(repo, field_name, fields[field_name])
            changed.append(field_name)
    if not changed:
        return repo
    repo.save(update_fields=[*changed, "updated_at"])
    _record(repo, "harness.repo_updated", actor, fields_changed=sorted(changed))
    return repo


@transaction.atomic
def delete_repo(*, repo, actor):
    # Same lock-then-authorize ordering as edit_repo above.
    repo = _locked(repo, actor)
    _require_curator(actor, repo.workspace, "deleting this harness repo")
    # Audit-record before the delete attempt, matching every other
    # `_record` call's ordering in this module -- safe even if the delete
    # below fails, since this whole function is `@transaction.atomic`: a
    # `HarnessInvalidTransition` raised from the `except` clause rolls
    # back the entire transaction, including this insert.
    _record(repo, "harness.repo_deleted", actor)
    try:
        repo.delete()
    except ProtectedError as exc:
        # HarnessJob.repo is on_delete=PROTECT (models.py) precisely so a
        # repo's job/audit history can never silently disappear -- but
        # that DB-level protection surfacing as an unhandled 500 once a
        # repo has any job at all (Codex code-review P2, branch 2 round 5)
        # is a service-layer gap, not a reason to weaken the FK. Curators
        # get a clean 400 with the actual reason instead of a crash;
        # disabling the repo (already the "make it inert" lever, brief
        # §1) is the correct next action, not deletion.
        raise HarnessInvalidTransition(
            "Cannot delete a repo with existing harness job history. Disable it instead."
        ) from exc


def _locked_job(job, actor):
    """Same shape as `_locked` above, one model over -- re-fetch under
    lock, scoped to the actor, so a forged/stale `job` instance can never
    reach the mutation path."""
    locked = (
        HarnessJob.objects.filter(pk=job.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise HarnessPermissionDenied("Job no longer exists.")
    return locked


def _record_job(job, action, actor, **metadata):
    # Audit metadata carries ids/status/agent only -- never `goal` (free
    # text the curator wrote, not bookkeeping content) and never the
    # captured transcript (branch 3's own concern).
    record_audit(
        action=action,
        actor=actor,
        workspace=job.workspace,
        resource=job,
        metadata=metadata,
    )


def list_jobs(*, workspace, actor):
    """Same curator-gated read as `list_repos` -- launching/reviewing a run
    against a registered repo is not a lighter-weight action than
    registering the repo in the first place (module docstring)."""
    _require_curator(actor, workspace, "listing harness jobs")
    return HarnessJob.objects.for_actor(actor, workspace=workspace)


def get_job(*, workspace, actor, job_id):
    _require_curator(actor, workspace, "viewing this harness job")
    return HarnessJob.objects.for_actor(actor, workspace=workspace).filter(id=job_id).first()


@transaction.atomic
def create_harness_job(*, workspace, actor, repo, goal, agent=None):
    """The only sanctioned creation path for `HarnessJob` rows. `repo` is
    re-locked here (not just trusted from the caller) so an `enabled`
    flip can never race a job's creation against it, mirroring
    `edit_repo`/`delete_repo`'s own lock-before-authorize discipline: the
    curator check below runs against `workspace` (the create endpoint's
    own already-`for_actor()`-resolved param, the same trust level
    `create_repo`'s `workspace` argument already has -- there is no
    existing row here for a forged instance to misrepresent), but the
    *repo*'s current state is re-verified live, under lock, since a
    curator could disable it in the gap between the caller reading it and
    this call running.

    `agent=None` takes the policy default (`agent_routing_reason`
    "policy:default"); a named agent must be policy-selectable for
    discovery ("owner_selected")."""
    _require_curator(actor, workspace, "creating a harness job")
    if repo.workspace_id != workspace.id:
        raise HarnessPermissionDenied("Repo does not belong to this workspace.")
    locked_repo = _locked(repo, actor)
    if not locked_repo.enabled:
        raise HarnessInvalidTransition("Repo is not enabled for harness runs.")
    try:
        agent, routing_reason = policy.resolve_agent(policy.DISCOVERY, agent)
    except policy.AgentNotSelectable:
        raise HarnessInvalidTransition(
            f"Agent '{agent}' is not selectable in this phase."
        ) from None
    sensitivity = Memory.Sensitivity.PRIVATE if locked_repo.sensitive else Memory.Sensitivity.NORMAL
    job = HarnessJob.objects.create(
        workspace=workspace,
        repo=locked_repo,
        goal=goal,
        agent=agent,
        agent_routing_reason=routing_reason,
        sensitivity=sensitivity,
        created_by=actor,
    )
    _record_job(
        job,
        "harness.job_created",
        actor,
        agent=agent,
        agent_routing_reason=routing_reason,
        repo_id=str(locked_repo.id),
    )
    # Codex code-review P1, branch 2 round 1: an earlier draft left the job
    # in `pending` with nothing ever enqueuing `harness.tasks.run_harness_job`
    # -- the recovery scan only reclaims a stale `running` job, so a job
    # created through this, the only sanctioned creation path, would never
    # be claimed or executed at all. Dispatch after commit, mirroring
    # `memory.jobs.create_extraction_job`'s identical shape.
    transaction.on_commit(lambda: dispatch_harness_job(job.id))
    return job


def dispatch_harness_job(job_id):
    """Publish `job_id` to the broker. A broker PUBLICATION failure (e.g.
    Redis down) must never propagate into the caller -- the create-job
    request must not fail because the broker is unreachable. The job stays
    `pending`; the recovery scan re-dispatches a job stuck `pending` past
    `HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS` (mirrors
    `memory.jobs.dispatch_extraction_job`'s identical shape and its own
    "must not swallow the task's own exceptions under
    CELERY_TASK_ALWAYS_EAGER" reasoning -- eager mode never touches a real
    broker, so there is nothing broker-shaped to catch in that mode)."""
    from django.conf import settings

    from harness.tasks import run_harness_job as run_harness_job_task

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_harness_job_task.delay(str(job_id))
        return
    try:
        run_harness_job_task.delay(str(job_id))
    except Exception:
        logger.exception("Failed to publish harness job %s to the broker", job_id)


@transaction.atomic
def cancel_harness_job(*, job, actor):
    """Kill switch (brief §2, brake #3): a fast, fenced flag flip only,
    covering every non-terminal status (Codex round-4 HIGH: `cancel_agent`'s
    own precedent cancels any non-terminal state, not just `running` --
    an earlier draft of this branch only handled `running`, which would
    have let an already-`pending` (queued but not yet claimed) job still
    launch after the owner cancelled it).

    `pending` -> `cancelled` directly (nothing has launched yet, so
    there's nothing to signal; `harness.execution._claim_job`'s own
    `status == pending` re-check under lock is what stops an
    already-cancelled job from ever starting). `running` -> only
    `cancel_requested = True` is set here; the actual container teardown
    is the owning Celery task's job (`harness.execution._wait_for_container`),
    never this request's -- this function never touches Docker."""
    locked = _locked_job(job, actor)
    _require_curator(actor, locked.workspace, "cancelling this harness job")
    if locked.status == HarnessJob.Status.PENDING:
        locked.status = HarnessJob.Status.CANCELLED
        locked.failure_reason = HarnessJob.FailureReason.KILLED
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
        _record_job(locked, "harness.job_cancelled", actor)
    elif locked.status == HarnessJob.Status.RUNNING:
        locked.cancel_requested = True
        locked.save(update_fields=["cancel_requested", "updated_at"])
        _record_job(locked, "harness.job_cancel_requested", actor)
    # Any other status is already terminal -- idempotent no-op, matching
    # cancel_task/cancel_agent's repeat-is-a-no-op convention.
    return locked


# --- HarnessImplementationJob (Phase 3 branch 2, brief §boundary) ---

# Codex code-review P1, branch 2 round 1: an earlier version treated every
# non-terminal status as "flag-only" cancellation, but `pending_review` and
# `pending_gate_b` have no running container/wait loop watching
# `cancel_requested` at all -- review completion could still call
# `request_harness_push`, and an already-approved Gate B could still push,
# neither of which ever re-checks the flag. Only the two statuses with an
# actual polling loop (`_wait_for_implementation_container` for
# `implementing`, and `run_harness_push_job`'s own brake for `pushing`)
# get flag-only treatment; every other non-terminal status is cancelled
# immediately instead.
_IMPLEMENTATION_ACTIVE_LOOP_STATUSES = (
    HarnessImplementationJob.Status.IMPLEMENTING,
    HarnessImplementationJob.Status.PUSHING,
)
_IMPLEMENTATION_IDLE_WAIT_STATUSES = (
    HarnessImplementationJob.Status.PENDING_REVIEW,
    HarnessImplementationJob.Status.PENDING_GATE_B,
)


def _locked_implementation_job(job, actor):
    """Same shape as `_locked_job` above, one model over."""
    locked = (
        HarnessImplementationJob.objects.filter(pk=job.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise HarnessPermissionDenied("Implementation job no longer exists.")
    return locked


def _record_implementation_job(job, action, actor, **metadata):
    # Audit metadata carries ids/status/agent only -- never `goal`/
    # `diff_summary` (free text/gateway output, not bookkeeping content).
    record_audit(
        action=action,
        actor=actor,
        workspace=job.workspace,
        resource=job,
        metadata=metadata,
    )


def _snapshot_reviewer(agent):
    from harness.review_execution import select_review_agent

    try:
        return str(select_review_agent(agent))
    except HarnessInvalidTransition:
        return ""


def list_implementation_jobs(*, workspace, actor):
    _require_curator(actor, workspace, "listing harness implementation jobs")
    return HarnessImplementationJob.objects.for_actor(actor, workspace=workspace)


def get_implementation_job(*, workspace, actor, job_id):
    _require_curator(actor, workspace, "viewing this harness implementation job")
    return (
        HarnessImplementationJob.objects.for_actor(actor, workspace=workspace)
        .filter(id=job_id)
        .first()
    )


@transaction.atomic
def create_harness_implementation_job(
    *,
    workspace,
    actor,
    repo,
    goal,
    agent=None,
    discovery_job=None,
):
    """The only sanctioned creation path for `HarnessImplementationJob`
    rows. Mirrors `create_harness_job`'s own lock-then-authorize
    discipline, plus three preconditions that are new to this phase and
    are all refused at creation time, before Gate A is ever requested --
    never a doomed row created and then immediately failed (mirrors the
    existing "repo not enabled"/"agent not selectable" checks' own
    fail-at-creation shape):

    1. Self-modification hard-block (ADR-004, brief §boundary) -- a repo
       whose remote canonically matches this Jarvis installation's own is
       refused outright for implementation, even though it's fine for
       Phase 2 read-only discovery.
    2. `test_command` must be configured -- brief §boundary: "a repo with
       none configured fails every implementation job closed", and the
       earliest point that's knowable is right here, not after a container
       has already run.
    3. `agent` (None = the policy default) must be policy-selectable for
       implementation (`HARNESS_AGENT_POLICY`, Phase 5 D11 -- distinct
       from the discovery list).

    Requests Gate A immediately, in the same transaction as job creation
    (durable-before-request) -- `approvals.services.create_request` runs
    its own `validate_request` tier-1 check against the just-created,
    same-transaction-visible row.
    """
    _require_curator(actor, workspace, "creating a harness implementation job")
    if repo.workspace_id != workspace.id:
        raise HarnessPermissionDenied("Repo does not belong to this workspace.")
    locked_repo = _locked(repo, actor)
    if not locked_repo.enabled:
        raise HarnessInvalidTransition("Repo is not enabled for harness runs.")
    # Phase 3 branch 5 (2026-09-27 audit): an ADR-004 hard rule must not
    # relax because a setting was never filled in. With no self-repo
    # identifiers configured the guard cannot tell whether this repo IS
    # Jarvis, so implementation (write) jobs are refused outright until
    # the owner sets HARNESS_SELF_REPO_IDENTIFIERS. Read-only discovery is
    # unaffected.
    if not settings.HARNESS_SELF_REPO_IDENTIFIERS:
        raise HarnessInvalidTransition(
            "HARNESS_SELF_REPO_IDENTIFIERS is not configured; implementation runs are "
            "refused until this installation's own remote(s) are set (ADR-004 "
            "self-modification guard)."
        )
    if is_self_repo(
        locked_repo.git_url, self_repo_identifiers=settings.HARNESS_SELF_REPO_IDENTIFIERS
    ):
        raise HarnessInvalidTransition(
            "This repo resolves to Jarvis's own installation; implementation/review "
            "runs are refused (ADR-004 self-modification guard)."
        )
    # Codex code-review P2, branch 2 round 4: a bare truthiness check
    # treats a whitespace-only string (e.g. a curator pasting a value
    # with trailing spaces, or `"   "`) as "configured" -- a shell
    # interprets that as a no-op that exits 0, so the wrapper would
    # report tests as having "passed" without running any verification
    # at all. `.strip()` catches this the same way `HarnessRepoCreate
    # Serializer.validate_name`'s identical empty-after-strip check does.
    if not locked_repo.test_command.strip():
        raise HarnessInvalidTransition(
            "Repo has no test_command configured; implementation jobs cannot verify "
            "their own result without one."
        )
    try:
        agent, routing_reason = policy.resolve_agent(policy.IMPLEMENTATION, agent)
    except policy.AgentNotSelectable:
        raise HarnessInvalidTransition(
            f"Agent '{agent}' is not selectable for implementation."
        ) from None
    if discovery_job is not None and discovery_job.workspace_id != workspace.id:
        raise HarnessPermissionDenied("discovery_job does not belong to this workspace.")
    if discovery_job is not None and discovery_job.repo_id != locked_repo.id:
        raise HarnessInvalidTransition("discovery_job was run against a different repo.")
    sensitivity = Memory.Sensitivity.PRIVATE if locked_repo.sensitive else Memory.Sensitivity.NORMAL
    job = HarnessImplementationJob.objects.create(
        workspace=workspace,
        repo=locked_repo,
        discovery_job=discovery_job,
        goal=goal,
        agent=agent,
        agent_routing_reason=routing_reason,
        sensitivity=sensitivity,
        created_by=actor,
    )
    approval_request = create_request(
        workspace=workspace,
        actor=actor,
        tool_name="harness_implement",
        # Snapshot for the decider (Phase 3 branch 6, plan-review round 9
        # item 5): `ToolSpec.describe_request` renders only these values,
        # so later edits to the repo row can't change what Gate A showed.
        payload={
            "job_id": str(job.id),
            "goal": job.goal,
            "repo_name": locked_repo.name,
            "repo_git_url": job.repo_git_url,
            "branch_name": job.branch_name,
            "agent": str(job.agent),
            "reviewer_agent": _snapshot_reviewer(job.agent),
        },
        origin=ApprovalRequest.Origin.HARNESS,
        channel=CHANNEL_SYSTEM,
        # The owning service of this system-only gate (ADR-010).
        system_created=True,
    )
    job.gate_a_request = approval_request
    job.save(update_fields=["gate_a_request", "updated_at"])
    _record_implementation_job(
        job,
        "harness.implementation_job_created",
        actor,
        agent=agent,
        agent_routing_reason=routing_reason,
        repo_id=str(locked_repo.id),
    )
    return job


def cancel_harness_implementation_job(*, job, actor):
    """Kill switch mirroring `cancel_harness_job`'s shape, extended to
    every non-terminal status this job type has (five, not two -- brief
    §boundary's own state machine has more phases than discovery's).

    `pending_gate_a`, `pending_review`, `pending_gate_b` -> `cancelled`
    directly -- none of these has an active polling loop watching
    `cancel_requested` (Codex code-review P1, branch 2 round 1: an
    earlier version only flag-flipped these too, which did nothing at
    all for the two idle-wait phases -- review completion or an
    already-approved Gate B would still proceed as if nothing had
    happened). Their own linked `ApprovalRequest` (`gate_a_request`/
    `gate_b_request`), if one is still pending, is now revoked too (Phase
    3 branch 4, `approvals.services.revoke_request`) -- even though a
    curator who approves it anyway can no longer revive the job either
    way (`harness.tools`'s `create_execution` callbacks already
    re-check the job's current status under lock, Codex code-review P1,
    branch 2 round 1, companion fix), leaving a stale-but-still-pending
    request sitting in the approvals inbox forever was its own UX gap:
    nothing ever closed it out, so a curator would keep seeing (and
    could still, harmlessly, decide) a request for a job that's already
    gone. A worktree already exists by `pending_review`/`pending_gate_b`
    and is cleaned up immediately here, same as every other terminal
    path (brief round-2/3 MEDIUM finding).

    This function is deliberately NOT `@transaction.atomic` at the
    function level (Codex code-review P2, branch 4 round 1) -- the job's
    own lock/transition runs in its OWN inner `with transaction.atomic():`
    block below, and `revoke_request` (which locks the `ApprovalRequest`
    row) is called strictly AFTER that block, at this function's own top
    level, so it only ever runs once the job's lock has actually been
    released. A function-level decorator would make the inner block a
    mere SAVEPOINT, not a real transaction boundary -- the job row would
    stay locked for the decorator's ENTIRE outer transaction, i.e.
    through the revoke calls too, reintroducing exactly the ordering
    conflict this structure exists to avoid. This was confirmed the hard
    way: an earlier version of this fix added the inner block *without*
    removing a pre-existing `@transaction.atomic` decorator, and a real
    two-connection concurrency test still deadlocked identically, with
    `connection.in_atomic_block` still `True` immediately after the inner
    block's own `with` exited -- proof the job lock was never actually
    released until the decorator's outer transaction did.

    Even with the true transaction boundary fixed, one indirect ordering
    conflict remains: `approve_request` locks the `ApprovalRequest` first,
    then the job second (inside `create_harness_implement_execution`/
    `create_harness_push_execution`), all within its OWN single
    transaction -- the exact reverse order this function's two separate
    transactions take. `revoke_request` setting the request to `expired`
    also fires `harness.signals`'s `post_save` receiver, which schedules
    (via `transaction.on_commit`) ANOTHER attempt to lock the job row --
    and that third, signal-triggered lock attempt can still land while
    `approve_request`'s own transaction is mid-flight holding both locks
    at once, producing a genuine Postgres deadlock (also confirmed with
    the same concurrency test). Since `approve_request`'s own bundling of
    both locks into one transaction is pre-existing, security-relevant
    approvals code well outside this branch's scope to restructure, and
    the revoke itself is a UX cleanup, not a security boundary
    (`create_harness_implement_execution`/`create_harness_push_execution`'s
    own status re-check is what actually prevents a stale approval from
    reviving a cancelled job), each revoke call below is best-effort: a
    deadlock or any other failure here is logged and swallowed, never
    allowed to make an otherwise-successful cancellation itself fail.

    `implementing`/`pushing` -> `cancel_requested = True` only; these are
    the two phases with a real container/wait loop
    (`_wait_for_implementation_container`/`run_harness_push_job`'s own
    brake) that actually observes the flag and performs its own teardown
    and worktree cleanup, never this request."""
    gate_a_request = None
    gate_b_request = None
    ran_job = None
    with transaction.atomic():
        locked = _locked_implementation_job(job, actor)
        _require_curator(actor, locked.workspace, "cancelling this harness implementation job")
        if locked.status == HarnessImplementationJob.Status.PENDING_GATE_A:
            locked.status = HarnessImplementationJob.Status.CANCELLED
            locked.failure_reason = HarnessImplementationJob.FailureReason.KILLED
            locked.save(update_fields=["status", "failure_reason", "updated_at"])
            _record_implementation_job(locked, "harness.implementation_job_cancelled", actor)
            gate_a_request = locked.gate_a_request
        elif locked.status in _IMPLEMENTATION_IDLE_WAIT_STATUSES:
            was_pending_gate_b = locked.status == HarnessImplementationJob.Status.PENDING_GATE_B
            locked.status = HarnessImplementationJob.Status.CANCELLED
            locked.failure_reason = HarnessImplementationJob.FailureReason.KILLED
            locked.save(update_fields=["status", "failure_reason", "updated_at"])
            _record_implementation_job(locked, "harness.implementation_job_cancelled", actor)
            _cleanup_worktree_best_effort(locked)
            # The job actually ran (Gate A was approved): it leaves a
            # work_log like every other post-implementation terminal path.
            ran_job = locked
            if was_pending_gate_b:
                gate_b_request = locked.gate_b_request
        elif locked.status in _IMPLEMENTATION_ACTIVE_LOOP_STATUSES:
            locked.cancel_requested = True
            locked.save(update_fields=["cancel_requested", "updated_at"])
            _record_implementation_job(locked, "harness.implementation_job_cancel_requested", actor)
        # Any other status is already terminal -- idempotent no-op.
    if ran_job is not None:
        from harness.implementation_execution import record_implementation_work_log

        record_implementation_work_log(ran_job)
    if gate_a_request is not None:
        _revoke_gate_request_best_effort(gate_a_request, actor)
    if gate_b_request is not None:
        _revoke_gate_request_best_effort(gate_b_request, actor)
    return locked


def _revoke_gate_request_best_effort(approval_request, actor=None):
    """See `cancel_harness_implementation_job`'s own docstring for why
    this must never raise: the cancellation it's called from has
    already committed by this point, and a stale approval request left
    behind on a rare deadlock is a UX inconvenience, not a security
    issue -- `harness.tools`'s own status re-checks are the actual
    guard against it being decided into reviving a job that's already
    gone.

    `actor` is optional (Codex code-review P2, branch 4 round 6): the
    worktree-retention sweep (`harness.implementation_execution.
    _sweep_expired_worktree_job`) calls this with no actor at all -- a
    passive system timeout, not a human choosing to withdraw anything --
    and `revoke_request`/`record_revoked` record that correctly as a
    `SYSTEM`-attributed audit entry only when `actor` is genuinely
    `None`, never a human actor synthesized from the job's own creator."""
    try:
        revoke_request(approval_request=approval_request, actor=actor)
    except Exception:
        logger.exception(
            "Failed to revoke approval request %s after job cancellation", approval_request.pk
        )


def fail_implementation_job_for_review(*, job, review, failure_reason=None):
    """A `changes_requested` verdict (the default `failure_reason`) fails
    the job closed without ever reaching Gate B (brief §boundary's own
    fail-closed precedent, extended from the test-outcome contract to the
    review verdict) -- there is no "let the human decide anyway" path for
    a changes-requested review in this phase; a curator who disagrees can
    always re-run implementation from scratch with a refined goal.

    `failure_reason` is overridable (Phase 3 branch 3) for the OTHER way
    review can end a job without ever producing a verdict at all -- a
    review container that fails to launch, or a gateway summarization
    call that never returns a recognized verdict, uses
    `REVIEW_UNAVAILABLE` instead (`harness.review_execution.
    _finalize_review_unavailable`) -- a distinct, deterministic reason
    from "review ran and said no," mirroring the test-outcome contract's
    own `test_result_unavailable` vs `tests_failed` split.

    Cleans up the worktree immediately (every terminal path does, brief
    round-2/3 MEDIUM finding)."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job.pk, status=HarnessImplementationJob.Status.PENDING_REVIEW)
            .first()
        )
        if locked is None:
            return None
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = (
            failure_reason or HarnessImplementationJob.FailureReason.REVIEW_CHANGES_REQUESTED
        )
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
    _record_implementation_job(
        locked,
        "harness.implementation_job_failed",
        locked.created_by,
        review_id=str(review.id) if review is not None else "",
    )
    _cleanup_worktree_best_effort(locked)
    from harness.implementation_execution import record_implementation_work_log

    record_implementation_work_log(locked)
    return locked


@transaction.atomic
def request_harness_push(*, job, review):
    """Requests Gate B (push approval) once implementation + tests +
    review have all completed (brief §boundary) -- `review` must already
    be a `succeeded`, `approved` review of exactly this job; a
    `changes_requested` verdict is the caller's job to route to
    `fail_implementation_job_for_review` instead, never here. Branch 3
    calls this once its own review container records a real verdict; this
    branch's own tests call it directly with a manually-constructed
    completed `HarnessReview` fixture, since the real review dispatch
    itself is stubbed/mocked in this branch (brief §boundary)."""
    locked = (
        HarnessImplementationJob.objects.select_for_update(of=("self",))
        .filter(pk=job.pk, status=HarnessImplementationJob.Status.PENDING_REVIEW)
        .first()
    )
    if locked is None:
        raise HarnessInvalidTransition("Job is not awaiting review completion.")
    if review.implementation_job_id != locked.id:
        raise HarnessInvalidTransition("Review does not belong to this job.")
    if review.status != HarnessReview.Status.SUCCEEDED:
        raise HarnessInvalidTransition("Review has not succeeded.")
    if review.verdict != HarnessReview.Verdict.APPROVED:
        raise HarnessInvalidTransition(
            "Review verdict is not 'approved'; route to fail_implementation_job_for_review instead."
        )
    approval_request = create_request(
        workspace=locked.workspace,
        actor=locked.created_by,
        tool_name="harness_push",
        payload={
            "job_id": str(locked.id),
            "review_id": str(review.id),
            "diff_summary": locked.diff_summary,
            "changed_files": locked.changed_files,
            # Snapshotted at request time (Phase 3 branch 6, plan-review
            # round 9 item 5) so Gate B's display summary can never drift
            # from what the decider actually saw.
            "branch_name": locked.branch_name,
            "produced_commit_sha": locked.produced_commit_sha,
            "test_result": locked.test_result,
            "review_agent": str(review.agent),
            "review_verdict": str(review.verdict),
            "review_summary": review.summary,
        },
        origin=ApprovalRequest.Origin.HARNESS,
        channel=CHANNEL_SYSTEM,
        # The owning service of this system-only gate (ADR-010).
        system_created=True,
    )
    locked.gate_b_request = approval_request
    locked.status = HarnessImplementationJob.Status.PENDING_GATE_B
    locked.save(update_fields=["gate_b_request", "status", "updated_at"])
    _record_implementation_job(
        locked,
        "harness.implementation_job_gate_b_requested",
        locked.created_by,
        review_id=str(review.id),
    )
    return locked


def _cleanup_worktree_best_effort(job):
    """Codex code-review P2, branch 2 round 3: an earlier version cleared
    `worktree_path` unconditionally, even when cleanup itself failed --
    leaving the directory genuinely leaked on disk with no durable
    pointer left anywhere to find it by (mirrors the exact
    `removal_confirmed` discipline `harness.execution._safe_remove_container`
    already established for containers: only clear the handle once
    removal is actually confirmed). Leaving `worktree_path` populated on
    a failed cleanup is also what lets a future recovery sweep
    (`HARNESS_WORKTREE_RETENTION_HOURS`, branch 4) find and retry it.

    Confirmation comes from checking the filesystem afterward, not from
    `runner.cleanup_directory` raising (Codex code-review P1, branch 2
    round 5): that function is `shutil.rmtree(path, ignore_errors=True)`
    -- it never raises, by design -- so the earlier `try/except` here was
    dead code that always treated cleanup as successful regardless of
    whether the directory was actually removed."""
    if not job.worktree_path:
        return
    import os

    from harness import runner

    runner.cleanup_directory(job.worktree_path)
    if os.path.exists(job.worktree_path):
        logger.error(
            "Failed to fully clean up worktree for implementation job %s (still present on disk)",
            job.pk,
        )
        return
    HarnessImplementationJob.objects.filter(pk=job.pk).update(worktree_path="")


def fail_implementation_job_for_gate_a_decision(job_id, *, decision_status):
    """`harness.signals`'s receiver calls this when Gate A is rejected or
    expires -- a rejected/expired `ApprovalRequest` has no `dispatch` to
    run (brief §boundary), so without this the job would stay
    `pending_gate_a` forever. No worktree exists yet at this gate, so
    there is nothing to clean up beyond the status flip.

    A cheap, non-locking pre-check (Codex code-review P2, branch 4
    round 1) skips the `select_for_update` attempt entirely when the job
    is already terminal -- a real, empirically-reproduced deadlock risk
    otherwise: `cancel_harness_implementation_job` (Phase 3 branch 4)
    now revokes a job's own `gate_a_request` as part of cancelling it,
    which fires THIS signal via `revoke_request`'s `expired` write --
    but the job was already transitioned to `cancelled` by that SAME
    cancellation, moments earlier, making this function's own job-lock
    attempt here completely redundant in that case. `approve_request`
    bundles its own approval-row lock and job-row lock into ONE
    transaction; a concurrent approval landing at the same moment this
    redundant lock attempt runs is exactly the shape of a genuine
    Postgres deadlock, confirmed with a real two-connection concurrency
    test. Once a job reaches a terminal status it never goes back to
    `pending_gate_a`, so skipping the lock on a plain (non-blocking)
    read of an already-terminal status is always safe -- this is purely
    an optimization to avoid a lock that would find nothing to do
    anyway, never a change to what this function actually decides."""
    if (
        HarnessImplementationJob.objects.filter(pk=job_id)
        .exclude(status=HarnessImplementationJob.Status.PENDING_GATE_A)
        .exists()
    ):
        return
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status=HarnessImplementationJob.Status.PENDING_GATE_A)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = (
            HarnessImplementationJob.FailureReason.GATE_EXPIRED
            if decision_status == ApprovalRequest.Status.EXPIRED
            else HarnessImplementationJob.FailureReason.GATE_A_REJECTED
        )
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
    _record_implementation_job(locked, "harness.implementation_job_failed", locked.created_by)


def fail_implementation_job_for_gate_b_decision(job_id, *, decision_status):
    """Same as `fail_implementation_job_for_gate_a_decision` above, one
    gate over -- including the same non-locking pre-check and the same
    deadlock-avoidance rationale (Codex code-review P2, branch 4 round
    1). A worktree DOES exist by the time Gate B is even requested
    (implementation + tests + review already completed), so this path
    also cleans it up (brief round-2/3 MEDIUM finding: every terminal
    path cleans up its own worktree immediately)."""
    if (
        HarnessImplementationJob.objects.filter(pk=job_id)
        .exclude(status=HarnessImplementationJob.Status.PENDING_GATE_B)
        .exists()
    ):
        return
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status=HarnessImplementationJob.Status.PENDING_GATE_B)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = (
            HarnessImplementationJob.FailureReason.GATE_EXPIRED
            if decision_status == ApprovalRequest.Status.EXPIRED
            else HarnessImplementationJob.FailureReason.GATE_B_REJECTED
        )
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
    _record_implementation_job(locked, "harness.implementation_job_failed", locked.created_by)
    _cleanup_worktree_best_effort(locked)
    from harness.implementation_execution import record_implementation_work_log

    record_implementation_work_log(locked)
