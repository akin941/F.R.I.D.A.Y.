"""The Graphify AST-only job (Phase 8B branch 5, brief D10): trigger, claim,
run, import, recovery.

- **Trigger** (`poll_graphify`, called by the runner-beat git producer
  `harness.ingestion` for every enabled repo): when the repo's
  `graphify_enabled` switch is on and the default branch's head differs
  from `last_graphify_sha`, one `GraphifyRun` is created and dispatched on
  the runner queue -- at most one run per head (requested or cloned sha)
  and at most one in flight per repo. The producer already knows the head
  from its commit read; when it read nothing (a quiet window, or the git
  source paused) the head comes from `runner.remote_head` (`ls-remote`).
  The trigger never raises into the producer (`ingestion._safely`).
- **Run** (`run_graphify_run`, the `harness.tasks.run_graphify_run` task):
  a lease-fenced claim, the existing read-only `clone_repo`, a
  `--network none` container (`runner.launch_graphify_container`), the
  hostile-`/out` read (`harness.safe_read`), schema validation and mapping
  (`harness.graphify`), then -- under the run's fence and the repo's row
  lock -- the memory port's temporal replace
  (`memory.graph.ports.import_code_graph`). Any refusal fails the run with
  nothing replaced. Container output is never logged or stored; the run
  row keeps counts and a stable error type only.
- **Recovery** (`recover_stale_graphify_runs`, runner beat): re-dispatches a
  `pending` row whose dispatch was lost and a `running` row whose lease
  expired; the claim removes the old container (deterministic name) first.
  A run that keeps dying fails after `GraphifyRun.MAX_ATTEMPTS` claims.
"""

import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from harness import container_lifecycle, graphify, runner, safe_read
from harness.models import GraphifyRun, HarnessRepo
from memory.graph import ports as graph_ports

logger = logging.getLogger(__name__)

IN_FLIGHT = (GraphifyRun.Status.PENDING, GraphifyRun.Status.RUNNING)
GRAPH_FILE = "graph.json"


def container_name(run_id):
    return f"friday-graphify-{run_id}"


def lease_seconds():
    # Beyond the Celery hard time limit of the task (`harness.tasks`), so a
    # live worker never loses its lease to the recovery scan.
    return (
        settings.HARNESS_MAX_JOB_SECONDS
        + settings.HARNESS_CELERY_FINALIZATION_BUFFER_SECONDS
        + settings.HARNESS_RECOVERY_GRACE_SECONDS
    )


# --- trigger -------------------------------------------------------------------


def _wants_graphify(repo):
    return bool(repo.enabled and repo.graphify_enabled)


def maybe_trigger_graphify(repo, *, head_sha):
    """Create (and on commit dispatch) one run for `head_sha`, or nothing.
    Returns the run or `None`."""
    if not head_sha or not _wants_graphify(repo):
        return None
    with transaction.atomic():
        locked = HarnessRepo.objects.select_for_update().filter(pk=repo.pk).first()
        if locked is None or not _wants_graphify(locked) or head_sha == locked.last_graphify_sha:
            return None
        runs = GraphifyRun.objects.filter(repo=locked)
        if runs.filter(status__in=IN_FLIGHT).exists():
            return None
        if runs.filter(Q(requested_sha=head_sha) | Q(commit_sha=head_sha)).exists():
            return None
        run = GraphifyRun.objects.create(
            workspace_id=locked.workspace_id, repo=locked, requested_sha=head_sha
        )
        transaction.on_commit(lambda: dispatch_graphify_run(run.pk))
    return run


def poll_graphify(repo, *, head_sha=None):
    """The producer's hook: `head_sha` is the tip its commit read saw, or
    `None` to ask the remote (only for a repo that wants Graphify)."""
    if not _wants_graphify(repo):
        return None
    if not head_sha:
        head_sha = runner.remote_head(
            git_url=repo.git_url, branch=repo.default_branch, clone_env={}
        )
    return maybe_trigger_graphify(repo, head_sha=head_sha)


def dispatch_graphify_run(run_id):
    """Publish to the runner queue; a publication failure is logged, never
    raised (the recovery scan re-dispatches). Eager mode runs inline."""
    from harness.tasks import run_graphify_run

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_graphify_run.delay(str(run_id))
        return
    try:
        run_graphify_run.delay(str(run_id))
    except Exception:
        logger.exception("Failed to publish graphify run %s", run_id)


# --- claim, fence, finalize ------------------------------------------------------


def claim_graphify_run(run_id):
    """`pending` (or `running` with an expired lease) -> `running` with a
    fresh token. Returns `(run_id, token, previous_container)` or `None`."""
    now = timezone.now()
    with transaction.atomic():
        run = (
            GraphifyRun.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=run_id)
                & (
                    Q(status=GraphifyRun.Status.PENDING)
                    | Q(status=GraphifyRun.Status.RUNNING, lease_expires_at__lt=now)
                )
            )
            .first()
        )
        if run is None:
            return None
        previous = run.container_id
        exhausted = run.attempts >= GraphifyRun.MAX_ATTEMPTS
        if exhausted:
            _close(run, GraphifyRun.Status.FAILED, error_type="attempts_exhausted")
        else:
            token = uuid.uuid4()
            run.status = GraphifyRun.Status.RUNNING
            run.attempts += 1
            run.claim_token = token
            run.lease_expires_at = now + timedelta(seconds=lease_seconds())
            run.started_at = now
            run.save(
                update_fields=[
                    "status",
                    "attempts",
                    "claim_token",
                    "lease_expires_at",
                    "started_at",
                    "updated_at",
                ]
            )
    if exhausted:
        if previous:
            _teardown_container(previous)
            _remove_container(run_id, previous)
        return None
    return run_id, token, previous


def _fenced(run_id, token):
    return (
        GraphifyRun.objects.select_for_update()
        .filter(pk=run_id, status=GraphifyRun.Status.RUNNING, claim_token=token)
        .first()
    )


def _close(run, status, *, error_type="", error_message="", counts=None):
    run.status = status
    run.claim_token = None
    run.lease_expires_at = None
    run.finished_at = timezone.now()
    run.error_type = error_type[:50]
    run.error_message = error_message[:300]
    fields = [
        "status",
        "claim_token",
        "lease_expires_at",
        "finished_at",
        "error_type",
        "error_message",
    ]
    if counts is not None:
        run.counts = counts
        fields.append("counts")
    run.save(update_fields=[*fields, "updated_at"])


def _fail(run_id, token, error_type, error_message=""):
    with transaction.atomic():
        run = _fenced(run_id, token)
        if run is None:
            return False
        _close(run, GraphifyRun.Status.FAILED, error_type=error_type, error_message=error_message)
    logger.warning("Graphify run %s failed: %s", run_id, error_type)
    return True


class _RunFailed(Exception):
    def __init__(self, error_type, message=""):
        super().__init__(error_type)
        self.error_type = error_type
        self.message = message


# --- the run ----------------------------------------------------------------------


def _teardown_container(container_id):
    container_lifecycle.teardown_container(container_id, label="graphify container")


def _remove_container(run_id, name):
    if container_lifecycle.safe_remove_container(name, label="graphify container"):
        GraphifyRun.objects.filter(pk=run_id, container_id=name).update(container_id="")


def _parse_committed_at(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise _RunFailed("bad_commit_date") from exc
    if timezone.is_naive(parsed):
        raise _RunFailed("bad_commit_date")
    return parsed


_WAIT_FAILURES = {
    container_lifecycle.WaitResult.TIMED_OUT: "timeout",
    container_lifecycle.WaitResult.EXITED_WITH_ERROR: "container_failed",
    container_lifecycle.WaitResult.STOPPED: "container_failed",
}


def _run_container(run, token, clone_dir, out_dir):
    name = container_name(run.pk)
    GraphifyRun.objects.filter(pk=run.pk, claim_token=token).update(container_id=name)
    try:
        runner.launch_graphify_container(
            image=settings.HARNESS_IMAGE_GRAPHIFY, clone_dir=clone_dir, out_dir=out_dir, name=name
        )
        outcome = container_lifecycle.wait_for_container(
            name,
            started_monotonic=time.monotonic(),
            teardown=_teardown_container,
            capture_transcript=False,
        )
    except runner.RunnerError as exc:
        raise _RunFailed("runner_error", "The graphify container could not run.") from exc
    finally:
        _remove_container(run.pk, name)
    failure = _WAIT_FAILURES.get(outcome.result)
    if failure is not None:
        raise _RunFailed(failure)


def _read_snapshot(out_dir):
    try:
        data = safe_read.read_bounded_regular_file(
            out_dir, GRAPH_FILE, limit=graphify.MAX_GRAPH_BYTES
        )
    except safe_read.UnsafeOutput as exc:
        raise _RunFailed("unsafe_output", f"graph.json refused: {exc.reason}") from exc
    try:
        return graphify.map_graph(graphify.parse_graph_bytes(data))
    except graphify.GraphifyRefused as exc:
        raise _RunFailed(f"graph_{exc.reason}", str(exc)) from exc


def _import(run_id, token, *, sha, committed_at, snapshot, stats):
    with transaction.atomic():
        run = _fenced(run_id, token)
        if run is None:
            return False  # the lease moved on; never import twice
        repo = HarnessRepo.objects.select_for_update().get(pk=run.repo_id)
        if not _wants_graphify(repo):
            raise _RunFailed("repo_disabled")
        try:
            counts = graph_ports.import_code_graph(
                workspace=run.workspace,
                repo_ref=str(repo.pk),
                commit_sha=sha,
                committed_at=committed_at,
                graph=snapshot,
            )
        except graph_ports.CodeGraphInvalid as exc:
            raise _RunFailed(f"import_{exc.reason}") from exc
        run.commit_sha = sha
        run.committed_at = committed_at
        run.save(update_fields=["commit_sha", "committed_at", "updated_at"])
        _close(run, GraphifyRun.Status.SUCCEEDED, counts={**stats, **counts})
        repo.last_graphify_sha = sha
        repo.save(update_fields=["last_graphify_sha", "updated_at"])
    return True


def _process(run_id, token, previous_container):
    run = GraphifyRun.objects.select_related("repo", "workspace").get(pk=run_id)
    if previous_container:
        # A reclaimed run: the dead worker's container may still exist.
        _teardown_container(previous_container)
        _remove_container(run_id, previous_container)
    if not _wants_graphify(run.repo):
        raise _RunFailed("repo_disabled")
    out_dir = tempfile.mkdtemp(prefix="harness-graphify-out-")
    os.chmod(out_dir, 0o700)
    clone_dir = None
    try:
        try:
            clone_dir = runner.clone_repo(
                git_url=run.repo.git_url, default_branch=run.repo.default_branch, clone_env={}
            )
            sha, committed_iso = runner.head_commit(clone_dir)
        except runner.RunnerError as exc:
            raise _RunFailed("clone_failed", "The repository could not be cloned.") from exc
        committed_at = _parse_committed_at(committed_iso)
        _run_container(run, token, clone_dir, out_dir)
        snapshot, stats = _read_snapshot(out_dir)
    finally:
        if clone_dir is not None:
            container_lifecycle.cleanup_directory(clone_dir, confirm=False, label="graphify clone")
        container_lifecycle.cleanup_directory(out_dir, confirm=False, label="graphify out dir")
    return _import(
        run_id, token, sha=sha, committed_at=committed_at, snapshot=snapshot, stats=stats
    )


def run_graphify_run(run_id):
    """Claim and run one Graphify run. Never raises: every failure ends as
    a `failed` row with a stable `error_type`."""
    claimed = claim_graphify_run(run_id)
    if claimed is None:
        return None
    claimed_id, token, previous = claimed
    try:
        return _process(claimed_id, token, previous)
    except _RunFailed as exc:
        _fail(claimed_id, token, exc.error_type, exc.message)
    except Exception:
        # The exception text is never persisted (it could quote output).
        logger.exception("Unexpected failure in graphify run %s", claimed_id)
        _fail(claimed_id, token, "unexpected", "Unexpected error -- see worker logs.")
    return False


# --- recovery -----------------------------------------------------------------------


def recover_stale_graphify_runs(now=None):
    """Re-dispatch lost or stale runs (the claim fences, so a duplicate
    dispatch is harmless). Returns how many were re-dispatched."""
    now = now or timezone.now()
    grace = now - timedelta(seconds=settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS)
    stale = list(
        GraphifyRun.objects.filter(
            Q(status=GraphifyRun.Status.PENDING, updated_at__lt=grace)
            | Q(status=GraphifyRun.Status.RUNNING, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    for run_id in stale:
        dispatch_graphify_run(run_id)
    return len(stale)
