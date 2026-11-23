"""`HarnessImplementationJob`/`HarnessReview` execution engine (Phase 3
branch 2, brief §boundary). Celery-agnostic -- `harness.tasks` is a thin
wrapper, mirroring `harness.execution`/`harness.tasks`'s existing split.

Deliberately NOT sharing a common engine with `harness.execution.
run_harness_job` (brief §boundary, round-1 MEDIUM finding, accepted): this
job type's lifecycle (writable worktree, wrapper-owned test result,
cross-vendor review, two approval gates, a host-side push) is materially
different from discovery's single read-only container. Phase 5 D12, with
three real call sites, extracted only the shared leaf layer (teardown,
removal, directory cleanup, the wait loop) into
`harness.container_lifecycle`; claim/finalize/recovery stay here.

Two Celery-task-backing entry points:

- `run_harness_implementation_job` -- Gate A already approved (status is
  `implementing`, set by `harness.tools.create_harness_implement_execution`):
  writable clone -> launch (read-write, branch 1's runner) -> wait/poll
  (mirrors `harness.execution._wait_for_container`'s brake shape) -> read
  the wrapper's own result file -> on a verified pass, produce a gateway
  `diff_summary` and move to `pending_review` -- the worktree survives
  this call's own cleanup (it must last through the Gate-B wait, brief
  §boundary), every OTHER terminal path cleans up immediately.
- `run_harness_push_job` -- Gate B already approved (status is `pushing`):
  bundle the agent's own worktree (a local operation) and push from a
  separate, host-only clean clone (branch 1's `runner.push_bundled_commit`)
  -- never from the worktree itself. Cleans up the worktree on every exit
  path, since nothing needs it to persist any further after this point.
"""

import base64
import binascii
import logging
import tempfile
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway, record_external_usage
from ai_core.models import PromptTemplate
from audit.services import record_audit
from harness import container_lifecycle, drivers, ingestion, runner
from harness.drivers import base as driver_base
from harness.drivers.codex_app_server import CodexAppServerDriver
from harness.drivers.schema import load_codex_snapshot
from harness.drivers.session import OUTCOME_COMPLETED, OUTCOME_STOPPED, DriverSession
from harness.execution import (
    _fence_and_bound_transcript,
    _parse_claude_code_output,
    _resolve_cli_env,
    redact_harness_secrets,
)
from harness.models import HarnessImplementationJob, HarnessReview
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.services import DuplicateMemory, record_work_log

logger = logging.getLogger(__name__)

# The one line the write-mode wrapper emits last, after its final
# quiescence (`docker/harness_write_mode.sh`): `<prefix> <base64 of the
# CLI's own captured stdout>`. Phase 3 branch 5 / plan-review round 9 item
# 2: container stdout is agent-writable while the agent is alive, so cost,
# usage and the implementation transcript are read only from the LAST such
# line -- anything printed before it is ignored, and a missing or malformed
# last frame fails closed (usage unavailable -> `runner_error`).
CLI_OUTPUT_PREFIX = "FRIDAY_HARNESS_CLI_OUTPUT_V1"
# The pre-rename marker, still emitted by images built before the Jarvis ->
# Friday rename. Accepted on read so such an image can still report; the
# last-frame rule is unchanged -- whichever accepted marker comes LAST wins.
LEGACY_CLI_OUTPUT_PREFIX = "JARVIS_HARNESS_CLI_OUTPUT_V1"
_CLI_OUTPUT_PREFIXES = (CLI_OUTPUT_PREFIX, LEGACY_CLI_OUTPUT_PREFIX)

# Literal-substring secret scan over the produced diff (Phase 3 branch 5):
# values shorter than this can't be matched without false positives.
_MIN_SECRET_SCAN_LENGTH = 12


def extract_framed_cli_output(raw_logs):
    """Returns the decoded CLI stdout from the last wrapper frame, or
    `None` if the last frame is absent or malformed. Never falls back to
    an earlier frame: only the wrapper can write after the agent is dead,
    so only the final frame is the wrapper's."""
    last = None
    for line in (raw_logs or "").splitlines():
        for prefix in _CLI_OUTPUT_PREFIXES:
            if line.startswith(prefix + " ") or line == prefix:
                last = (prefix, line)
                break
    if last is None:
        return None
    prefix, line = last
    payload = line[len(prefix) :].strip()
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return None


_IMPLEMENTATION_TERMINAL_STATUSES = (
    HarnessImplementationJob.Status.SUCCEEDED,
    HarnessImplementationJob.Status.FAILED,
    HarnessImplementationJob.Status.CANCELLED,
)


def _claim_implementation_job(job_id):
    """`implementing` is already set by `create_harness_implement_execution`
    (called synchronously when Gate A is approved) -- this re-fetches
    under lock and stamps `started_at` for the brake below, mirroring
    `harness.execution._claim_job`'s fenced-claim shape one status
    transition later than that function's own `pending` -> `running`.

    `started_at__isnull=True` is the actual claim fence (Codex code-review
    P1, branch 2 round 3): unlike discovery, `implementing` is BOTH "ready
    to be claimed" and "actively running" here -- there is no separate
    status transition a second claim attempt would find already moved
    past. A Celery redelivery, or `dispatch()` somehow firing twice, would
    otherwise have both invocations successfully claim the SAME row (the
    bare `status=implementing` filter never stops matching), launch a
    second container under the identical deterministic name, and race
    each other's own terminal-state writes. `started_at` is set
    unconditionally by this same claim and never reset, so it is only
    ever null for a row nothing has claimed yet -- a second attempt finds
    it already populated and safely gets `None` back."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(
                pk=job_id,
                status=HarnessImplementationJob.Status.IMPLEMENTING,
                started_at__isnull=True,
            )
            .first()
        )
        if locked is None:
            return None
        locked.started_at = timezone.now()
        locked.save(update_fields=["started_at", "updated_at"])
        return locked


def _teardown_container(container_id):
    """`container_lifecycle.teardown_container` -- the same best-effort
    stop/kill `harness.execution._teardown_container` uses."""
    container_lifecycle.teardown_container(container_id, label="harness implementation container")


def _safe_remove_container(container_name):
    """`container_lifecycle.safe_remove_container` -- the same
    confirmed-removal contract `harness.execution._safe_remove_container`
    has."""
    return container_lifecycle.safe_remove_container(
        container_name, label="harness implementation container"
    )


def _safe_cleanup_directory(path):
    """Returns `True` if cleanup is confirmed, `False` if it genuinely
    failed (Codex code-review P2, branch 2 round 4: an earlier version
    had no return value at all, so `run_harness_push_job` cleared
    `worktree_path` unconditionally regardless of whether this actually
    succeeded -- mirrors `_safe_remove_container`'s identical
    confirmed-before-clearing discipline). Callers must not clear a
    persisted path/handle on a `False` return.

    Confirmed via `os.path.exists(path)` AFTER calling
    `runner.cleanup_directory`, rather than trusting that call not
    raising (Codex code-review P1, branch 2 round 5) -- see
    `container_lifecycle.cleanup_directory(confirm=True)`."""
    return container_lifecycle.cleanup_directory(path, confirm=True, label="harness directory")


# `container_lifecycle.wait_for_container` outcome -> this job's own
# `(status, failure_reason)` -- unchanged from the pre-extraction loop.
_WAIT_OUTCOME_STATUS = {
    container_lifecycle.WaitResult.TIMED_OUT: (
        "failed",
        HarnessImplementationJob.FailureReason.TIMEOUT,
    ),
    container_lifecycle.WaitResult.STOPPED: (
        "cancelled",
        HarnessImplementationJob.FailureReason.KILLED,
    ),
    container_lifecycle.WaitResult.EXITED_WITH_ERROR: (
        "failed",
        HarnessImplementationJob.FailureReason.RUNNER_ERROR,
    ),
    container_lifecycle.WaitResult.EXITED_CLEANLY: ("succeeded", ""),
}


def _wait_for_implementation_container(job_id, container_name, started_monotonic):
    """The same brake shape as `harness.execution._wait_for_container`
    (timeout, cancel-poll, post-exit cancel re-check, confirmed exit code;
    `container_lifecycle.wait_for_container`, Phase 5 D12) -- without
    transcript capture: the caller captures the logs itself, and the
    result is read separately, from the wrapper's own file, never from
    container stdout/stderr (brief §boundary)."""
    outcome = container_lifecycle.wait_for_container(
        container_name,
        started_monotonic=started_monotonic,
        teardown=_teardown_container,
        should_stop=lambda: HarnessImplementationJob.objects.filter(
            pk=job_id, cancel_requested=True
        ).exists(),
        recheck_stop_after_exit=True,
        capture_transcript=False,
    )
    return _WAIT_OUTCOME_STATUS[outcome.result]


# DriverError code -> this job's failure reason (Phase 10 D10: each
# robustness failure is deterministic). A cancel maps to `killed` through the
# `cancelled` status; the deadline to the existing `timeout`.
_DRIVER_FAILURE_REASONS = {
    driver_base.CODE_OVERSIZE_LINE: HarnessImplementationJob.FailureReason.DRIVER_OVERSIZE_LINE,
    driver_base.CODE_MALFORMED_JSON: HarnessImplementationJob.FailureReason.DRIVER_MALFORMED_JSON,
    driver_base.CODE_UNKNOWN_METHOD: HarnessImplementationJob.FailureReason.DRIVER_UNKNOWN_METHOD,
    driver_base.CODE_EARLY_EXIT: HarnessImplementationJob.FailureReason.DRIVER_EARLY_EXIT,
    driver_base.CODE_TOO_MANY_LINES: HarnessImplementationJob.FailureReason.DRIVER_TOO_MANY_LINES,
    driver_base.CODE_PROTOCOL_ERROR: HarnessImplementationJob.FailureReason.DRIVER_PROTOCOL_ERROR,
    driver_base.CODE_TURN_FAILED: HarnessImplementationJob.FailureReason.DRIVER_TURN_FAILED,
    driver_base.CODE_DEADLINE: HarnessImplementationJob.FailureReason.TIMEOUT,
}


def build_driver(driver_name, *, deadline):
    """The driver instance for `driver_name`. A seam: tests replace it to
    inject limits or a clock; production code always gets the pinned
    snapshot."""
    if driver_name == drivers.DRIVER_CODEX_APP_SERVER:
        return CodexAppServerDriver(
            snapshot=load_codex_snapshot(settings.HARNESS_CODEX_APP_SERVER_VERSION),
            deadline=deadline,
            poll_seconds=float(settings.HARNESS_CANCEL_POLL_SECONDS),
        )
    raise drivers.DriverConfigurationError(f"no driver implementation for {driver_name!r}")


def _run_with_driver(
    job, driver_name, *, image, workspace_dir, base_sha, cli_env, container_name, started_monotonic
):
    """Phase 10 D10: launch the interactive container, run the driver
    session (handshake, one turn with the goal, escalations through
    `harness_agent_action`, usage per turn), then end the protocol phase
    and let the wrapper finish exactly as on the CLI path: the job's
    existing wait loop (runtime brake, cancel flag, confirmed exit code)
    waits for the commit, the test run and the result file. Any driver
    failure tears the container down and fails closed with a
    deterministic reason. Returns `(status, failure_reason, report)`."""
    deadline = started_monotonic + settings.HARNESS_MAX_JOB_SECONDS
    try:
        driver = build_driver(driver_name, deadline=deadline)
    except (OSError, ValueError) as exc:
        raise drivers.DriverConfigurationError(f"driver snapshot unavailable: {exc}") from exc
    container = runner.launch_interactive_container(
        image=image,
        workspace_dir=workspace_dir,
        goal=job.goal,
        test_command=job.repo_test_command,
        base_sha=base_sha,
        cli_env=cli_env,
        name=container_name,
        driver=driver_name,
    )
    session = DriverSession(
        job=job,
        driver=driver,
        deadline=deadline,
        should_stop=lambda: HarnessImplementationJob.objects.filter(
            pk=job.pk, cancel_requested=True
        ).exists(),
    )
    report = session.run(container, job.goal)
    container.close_stdin()
    if report.outcome != OUTCOME_COMPLETED:
        container.kill()
        _teardown_container(container_name)
        if report.outcome == OUTCOME_STOPPED:
            return "cancelled", HarnessImplementationJob.FailureReason.KILLED, report
        reason = _DRIVER_FAILURE_REASONS.get(
            report.code, HarnessImplementationJob.FailureReason.DRIVER_PROTOCOL_ERROR
        )
        return "failed", reason, report
    try:
        status, failure_reason = _wait_for_implementation_container(
            job.pk, container_name, started_monotonic
        )
    except runner.RunnerError:
        # The report is returned either way, so the turns' own usage rows
        # stay the run's only ModelUsage rows.
        logger.exception("Harness implementation job %s wait failed", job.pk)
        status, failure_reason = "failed", HarnessImplementationJob.FailureReason.RUNNER_ERROR
    if status != "succeeded":
        container.kill()
    else:
        container.reap()
    return status, failure_reason, report


def _fail_and_cleanup(
    job,
    failure_reason,
    *,
    workspace_dir,
    removal_confirmed,
    started_monotonic,
    test_result=None,
    input_tokens=0,
    output_tokens=0,
    estimated_cost=None,
    context_report=None,
    record_usage=True,
):
    """Shared terminal-failure path for every non-success outcome below --
    cleans up the worktree immediately (brief round-2/3 MEDIUM finding:
    every terminal path does, not only the "obviously terminal" ones).

    Cost defaults to zero (mirrors `harness.execution._finalize`'s own
    failure accounting) for the failure modes reached BEFORE
    `capture_logs` ever ran (launch errors, timeout, mid-run
    cancellation) -- there is genuinely nothing to bill for those. Codex
    code-review P2, branch 2 round 2: an earlier version zeroed cost
    unconditionally, including the THREE call sites below that run AFTER
    cost has already been parsed (`test_result_unavailable` x2,
    `tests_failed`) -- the CLI agent already spent real tokens/cost by
    that point regardless of what the separate test command or wrapper
    result later did, so those callers now pass the real figures through
    instead of silently under-recording actual spend.

    Clears `worktree_path` once cleanup is confirmed (Codex code-review
    P2, branch 4 round 3): `run_harness_implementation_job` now persists
    `worktree_path` to the DB as soon as the clone succeeds (round 2's
    own fix, for the recovery scan's benefit), but this function was
    still only cleaning up `workspace_dir` on disk without ever clearing
    the now-stale DB pointer -- every terminal path reached through here
    (tests failed, result unavailable, budget exceeded, a launch error
    after the clone) left the row FAILED/CANCELLED while still pointing
    at a directory that no longer existed. Uses `_apply_worktree_cleanup`
    -- the same confirmed-cleanup discipline the push finalizers already
    use -- so a genuinely failed cleanup still leaves the path populated
    for `_sweep_leaked_terminal_worktree` to retry."""
    worktree_cleanup_confirmed = True
    if workspace_dir:
        worktree_cleanup_confirmed = _safe_cleanup_directory(workspace_dir)
    latency_ms = int((time.monotonic() - started_monotonic) * 1000)
    with transaction.atomic():
        locked = HarnessImplementationJob.objects.select_for_update(of=("self",)).get(pk=job.pk)
        if locked.status != HarnessImplementationJob.Status.IMPLEMENTING:
            # Idempotency guard against a double-call -- nothing else
            # transitions this job away from `implementing` except this
            # function and the success path below.
            return
        cancelled = locked.cancel_requested
        locked.status = (
            HarnessImplementationJob.Status.CANCELLED
            if cancelled
            else HarnessImplementationJob.Status.FAILED
        )
        locked.failure_reason = (
            HarnessImplementationJob.FailureReason.KILLED if cancelled else failure_reason
        )
        if test_result is not None:
            locked.test_result = test_result
        if removal_confirmed:
            locked.container_id = ""
        locked.elapsed_seconds = max(0, int(latency_ms / 1000))
        update_fields = [
            "status",
            "failure_reason",
            "container_id",
            "elapsed_seconds",
            "updated_at",
        ]
        if test_result is not None:
            update_fields.append("test_result")
        _apply_worktree_cleanup(
            locked, update_fields, worktree_cleanup_confirmed=worktree_cleanup_confirmed
        )
        locked.save(update_fields=update_fields)
        # `record_usage=False` for a driver run: its session already wrote
        # one ModelUsage row per turn (Phase 10 D10).
        if record_usage:
            record_external_usage(
                workspace=locked.workspace,
                purpose="harness.implementation",
                backend=f"harness_cli:{locked.agent}",
                routing_reason=locked.agent_routing_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost if estimated_cost is not None else Decimal("0"),
                latency_ms=latency_ms,
                success=False,
                actor=locked.created_by,
                error_type=locked.failure_reason,
                context_report=context_report
                if context_report is not None
                else {"usage_source": "unavailable"},
            )
    record_audit(
        action=f"harness.implementation_job_{locked.status}",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={"failure_reason": locked.failure_reason},
    )
    record_implementation_work_log(locked)


def dispatch_created_review(review):
    """Schedules the review task for a `HarnessReview` row created in the
    same transaction as the job's `pending_review` transition (Phase 3
    branch 5, plan-review round 9 item 4). Thin re-export from
    `harness.review_execution` so this module's tests can mock it at THIS
    import path. A crash between the commit and this call leaves a
    `pending` review, which `recover_stale_harness_reviews` re-dispatches."""
    from harness.review_execution import dispatch_created_review as _dispatch_created_review

    return _dispatch_created_review(review)


def run_harness_implementation_job(job_id):
    job = _claim_implementation_job(job_id)
    if job is None:
        return

    container_name = f"harness-impl-{job.pk}"
    HarnessImplementationJob.objects.filter(pk=job.pk).update(container_id=container_name)

    workspace_dir = None
    started_monotonic = time.monotonic()
    removal_confirmed = True
    raw_output = ""
    cli_env = {}
    driver_name = drivers.DRIVER_CLI
    driver_report = None
    try:
        # Phase 10 D10: the driver is chosen (and its d4 gate re-checked)
        # before anything is cloned; a misconfigured driver fails closed.
        driver_name = drivers.selected_driver(job.agent)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(driver=driver_name)
        # Codex code-review P2, branch 2 round 2: an earlier version
        # passed an empty `clone_env`, so any repo relying on
        # `HARNESS_GIT_SSH_KEY_PATH` (the same credential
        # `push_bundled_commit` already resolves) had no way to actually
        # clone -- it would either fail outright or silently fall back to
        # whatever ambient SSH access the runner host happens to have.
        # The same credential this job will need again at push time is
        # resolved up front and threaded through here too.
        #
        # `job.repo_git_url`/`job.repo_default_branch` (Codex code-review
        # P1, branch 2 round 4), NOT `job.repo.git_url`/`job.repo.
        # default_branch` -- those are LIVE reads through an editable FK;
        # `create_harness_implementation_job`'s self-modification hard
        # block only ever runs once, at creation, so reading the repo row
        # live here (potentially hours later, across the whole Gate A
        # wait) would let an edit to `HarnessRepo.git_url` made after that
        # one-time check silently redirect an already-approved job. The
        # snapshotted fields can never change after creation.
        workspace_dir = runner.clone_repo_writable(
            git_url=job.repo_git_url,
            default_branch=job.repo_default_branch,
            branch_name=job.branch_name,
            clone_env=runner._resolve_git_push_env(),
        )
        # Persisted as soon as the clone succeeds, not only later on the
        # success path (Codex code-review P2, branch 4 round 2): a worker
        # that hard-crashes anywhere between here and that later write
        # would otherwise leave `job.worktree_path` empty in the DB while
        # a real directory sits on disk -- `recover_stale_harness_
        # implementation_jobs`' own worker-crashed case reads this same
        # field to know what to clean up, and `_fail_and_cleanup`'s own
        # `if workspace_dir:` check silently skips cleanup entirely for
        # an empty string, leaking the directory with nothing left to
        # ever find its path again. Writing the same value again on the
        # success path below is harmless -- it's unchanged for the rest
        # of this function's execution.
        HarnessImplementationJob.objects.filter(pk=job.pk).update(worktree_path=workspace_dir)
        repo_dir = f"{workspace_dir}/repo"
        base_sha = runner.resolve_commit_sha(repo_dir, "HEAD")
        image = runner.image_for_agent(job.agent)
        cli_env = _resolve_cli_env(job.agent)
        if driver_name == drivers.DRIVER_CLI:
            runner.launch_implementation_container(
                image=image,
                workspace_dir=workspace_dir,
                goal=job.goal,
                # `job.repo_test_command` (Codex code-review P2, branch 2
                # round 6), NOT `job.repo.test_command` -- the live FK read
                # would let a curator edit the repo's `test_command` to
                # blank/whitespace after this job was already approved,
                # reaching the wrapper as a no-op that a shell exits 0 for.
                # Same snapshot-at-creation rationale as `repo_git_url`/
                # `repo_default_branch` above.
                test_command=job.repo_test_command,
                base_sha=base_sha,
                cli_env=cli_env,
                name=container_name,
            )
            status, failure_reason = _wait_for_implementation_container(
                job.pk, container_name, started_monotonic
            )
            # Captured on EVERY post-launch outcome (Codex code-review P2,
            # branch 6 round 2): a crashed/timed-out/cancelled CLI's output
            # is exactly the diagnostic the stored transcript exists for.
            # Always before the `finally` removal below.
            try:
                raw_output = runner.capture_logs(container_name)
            except runner.RunnerError:
                logger.exception("Harness implementation job %s logs unavailable", job_id)
        else:
            # Phase 10 D10: a long-lived driver session instead of the
            # one-shot CLI. Its transcript (the agent's own messages) and
            # per-turn usage come from the session, never from container
            # stdout after the protocol phase.
            status, failure_reason, driver_report = _run_with_driver(
                job,
                driver_name,
                image=image,
                workspace_dir=workspace_dir,
                base_sha=base_sha,
                cli_env=cli_env,
                container_name=container_name,
                started_monotonic=started_monotonic,
            )
            raw_output = driver_report.transcript
        # Cost accounting reads the same capture (mirrors
        # `harness.execution._finalize`): the CLI's own stdout is framed by
        # the wrapper, separate from its result file; it must be read before
        # `finally` removes the container (Codex code-review P2, branch 2
        # round 1).
    except runner.RunnerError:
        logger.exception("Harness implementation job %s failed to launch", job_id)
        status, failure_reason = "failed", HarnessImplementationJob.FailureReason.RUNNER_ERROR
    except drivers.DriverConfigurationError:
        logger.exception("Harness implementation job %s driver refused", job_id)
        status, failure_reason = "failed", HarnessImplementationJob.FailureReason.RUNNER_ERROR
    finally:
        removal_confirmed = _safe_remove_container(container_name)

    # A driver run records its own ModelUsage rows (one per turn, a failure
    # row for a turn or a run without a usage report); the CLI path records
    # one row at its terminal transition below. A driver run that never
    # reached its session (the clone or the launch failed) falls back to the
    # CLI path's single failure row, so it is still visible.
    driver_mode = driver_report is not None
    if status != "succeeded":
        if raw_output:
            # The raw logs themselves, bounded and scrubbed of CLI keys: on a
            # failed/killed container no frame can be trusted (the wrapper may
            # never have reached its post-quiescence frame, and anything
            # before it is agent-writable), so none is decoded here (Codex
            # code-review P2, branch 6 round 3).
            HarnessImplementationJob.objects.filter(pk=job.pk).update(
                raw_transcript=_fence_and_bound_transcript(raw_output)[0]
            )
        _fail_and_cleanup(
            job,
            failure_reason,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            record_usage=not driver_mode,
        )
        return

    input_tokens, output_tokens, estimated_cost, context_report = (
        0,
        0,
        Decimal("0"),
        {"usage_source": "unavailable"},
    )
    if driver_mode:
        cli_output = driver_report.transcript
        input_tokens = driver_report.input_tokens
        output_tokens = driver_report.output_tokens
        context_report = driver_report.context_report(driver_name)
    else:
        cli_output = extract_framed_cli_output(raw_output)
        if cli_output:
            input_tokens, output_tokens, estimated_cost, context_report = _parse_claude_code_output(
                cli_output
            )
    # Stored before any further decision (Phase 3 branch 6), mirroring the
    # discovery/review "persist the transcript first" discipline: the
    # wrapper-framed CLI output when present, else the bounded raw logs so
    # a failed run is still diagnosable. Low-trust either way.
    transcript_source = cli_output if cli_output is not None else raw_output
    if transcript_source:
        HarnessImplementationJob.objects.filter(pk=job.pk).update(
            raw_transcript=_fence_and_bound_transcript(transcript_source)[0]
        )

    # Shared cost kwargs for every failure path below this point (Codex
    # code-review P2, branch 2 round 2): the CLI already spent these real
    # tokens/cost by now regardless of what the wrapper result/test
    # command turn out to say, so every failure from here on records the
    # real figures instead of `_fail_and_cleanup`'s zeroed default.
    cost_kwargs = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": estimated_cost,
        "context_report": context_report,
        # Driver runs already recorded one row per turn.
        "record_usage": not driver_mode,
    }

    try:
        result = runner.read_implementation_result(workspace_dir)
    except runner.RunnerError:
        logger.exception("Harness implementation job %s result unavailable", job_id)
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.TEST_RESULT_UNAVAILABLE,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            **cost_kwargs,
        )
        return

    if isinstance(result, dict) and result.get("no_changes") is True:
        # The wrapper found the produced tree identical to the base tree
        # and never ran the tests (Phase 3 branch 5) -- nothing to review
        # or push.
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.NO_CHANGES,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            **cost_kwargs,
        )
        return

    tests = result.get("tests") if isinstance(result, dict) else None
    commit_sha = result.get("commit_sha") if isinstance(result, dict) else None
    changed_files = result.get("changed_files") if isinstance(result, dict) else None
    if (
        not isinstance(tests, dict)
        or not tests.get("ran")
        or not commit_sha
        or not isinstance(changed_files, list)
    ):
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.TEST_RESULT_UNAVAILABLE,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            **cost_kwargs,
        )
        return

    # `passed` is derived here, from the wrapper's own reported
    # `exit_code`, never trusted as a bare assertion (brief §boundary) --
    # the wrapper's JSON is Friday-authored, deterministic code baked into
    # the image, not the CLI agent's own self-report, but re-deriving the
    # single boolean this decision hinges on costs nothing and keeps the
    # trust boundary exactly where the brief states it.
    exit_code = tests.get("exit_code")
    passed = isinstance(exit_code, int) and exit_code == 0
    test_result = {
        "ran": True,
        "exit_code": exit_code,
        "passed": passed,
        "command": tests.get("command", ""),
    }

    if not passed:
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.TESTS_FAILED,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    # Cost brake (Codex code-review P2, branch 2 round 2; mirrors
    # `harness.execution._finalize`'s brake #2): an otherwise-passing run
    # whose reported cost exceeds the configured ceiling still fails
    # closed -- it cannot un-spend the cost, so the real tokens/cost are
    # recorded here too, not zeroed. Checked AFTER the test-outcome check
    # above (mirrors discovery's own ordering: a job that failed for a
    # more specific reason is reported as that reason, not budget, even
    # if it also happened to run over budget).
    # Driver runs report tokens but no cost (Codex app-server): the token
    # brake bounds them instead (`HARNESS_DRIVER_MAX_TOKENS`).
    if estimated_cost > Decimal(str(settings.HARNESS_MAX_JOB_COST)) or (
        driver_mode and driver_report.total_tokens > settings.HARNESS_DRIVER_MAX_TOKENS
    ):
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.BUDGET_EXCEEDED,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    # Codex code-review P2, branch 2 round 6: a missing/unparseable
    # `--output-format json` result (empty Docker logs, a shape
    # `_parse_claude_code_output` doesn't recognize) falls back to
    # `estimated_cost == 0`, which can never exceed the budget check
    # above -- silently letting an otherwise-passing run through as a
    # "successful free run" with no real cost accounting at all. Mirrors
    # `harness.execution._finalize`'s identical fail-closed check
    # (`usage_source != "cli_reported"` -> `runner_error`), checked in
    # the same order discovery uses (budget first, then availability).
    expected_usage_source = "driver_reported" if driver_mode else "cli_reported"
    if context_report.get("usage_source") != expected_usage_source:
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.RUNNER_ERROR,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    # Secret scan (Phase 3 branch 5, 2026-09-27 audit): the agent's own
    # shell had the CLI API key in its environment, and anything it
    # committed will be bundled for review and later pushed. A literal
    # match of any credential value in the produced diff fails the job
    # here, before the diff ever leaves the runner.
    secret_values = [
        value for value in cli_env.values() if value and len(value) >= _MIN_SECRET_SCAN_LENGTH
    ]
    try:
        leaked = runner.diff_contains_any(
            f"{workspace_dir}/repo", base_sha=base_sha, head_sha=commit_sha, needles=secret_values
        )
    except runner.RunnerError:
        logger.exception("Harness implementation job %s secret scan failed", job_id)
        leaked = True
    if leaked:
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.SECRET_LEAK,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    # Picked BEFORE the transition (plan-review round 9 item 4): if no
    # cross-vendor reviewer is selectable, the job fails as
    # `review_unavailable` instead of committing a `pending_review` that
    # nothing will ever review.
    from harness.review_execution import create_pending_review, select_review_agent
    from harness.services import HarnessInvalidTransition

    try:
        review_agent = select_review_agent(job.agent)
    except HarnessInvalidTransition:
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    diff_summary = _produce_diff_summary(
        job, changed_files, repo_dir=f"{workspace_dir}/repo", base_sha=base_sha, head_sha=commit_sha
    )
    latency_ms = int((time.monotonic() - started_monotonic) * 1000)

    cancelled_before_review = False
    review = None
    with transaction.atomic():
        locked = HarnessImplementationJob.objects.select_for_update(of=("self",)).get(pk=job.pk)
        if locked.status != HarnessImplementationJob.Status.IMPLEMENTING:
            # Already cancelled/superseded under lock -- the worktree this
            # call produced is no longer wanted by anything; clean it up
            # rather than leaking it (mirrors every other exit path here).
            _safe_cleanup_directory(workspace_dir)
            return
        # Codex code-review P2, branch 2 round 5: a cancel landing in the
        # gap between `_wait_for_implementation_container`'s own last
        # `cancel_requested` check and THIS lock would otherwise go
        # unnoticed -- `implementing`'s status never changes for a
        # flag-only cancel (this job type's active-loop statuses are
        # flag-only, see `cancel_harness_implementation_job`), so the
        # status check above alone can't catch it. Checked under the SAME
        # lock that would otherwise commit `pending_review`, mirroring
        # `harness.execution._finalize`'s identical locked cancel
        # re-check -- closes the race rather than narrowing it.
        if locked.cancel_requested:
            cancelled_before_review = True
        else:
            locked.status = HarnessImplementationJob.Status.PENDING_REVIEW
            locked.base_commit_sha = base_sha
            locked.produced_commit_sha = commit_sha
            locked.changed_files = changed_files
            locked.test_result = test_result
            locked.diff_summary = diff_summary
            locked.worktree_path = workspace_dir
            locked.worktree_expires_at = timezone.now() + timedelta(
                hours=settings.HARNESS_WORKTREE_RETENTION_HOURS
            )
            if removal_confirmed:
                locked.container_id = ""
            locked.elapsed_seconds = max(0, int(latency_ms / 1000))
            locked.save(
                update_fields=[
                    "status",
                    "base_commit_sha",
                    "produced_commit_sha",
                    "changed_files",
                    "test_result",
                    "diff_summary",
                    "worktree_path",
                    "worktree_expires_at",
                    "container_id",
                    "elapsed_seconds",
                    "updated_at",
                ]
            )
            # Same transaction as the transition: `pending_review` never
            # commits without the review that will resolve it.
            review = create_pending_review(locked, review_agent)
            if not driver_mode:
                record_external_usage(
                    workspace=locked.workspace,
                    purpose="harness.implementation",
                    backend=f"harness_cli:{locked.agent}",
                    routing_reason=locked.agent_routing_reason,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=estimated_cost,
                    latency_ms=latency_ms,
                    success=True,
                    actor=locked.created_by,
                    error_type="",
                    context_report=context_report,
                )

    if cancelled_before_review:
        _fail_and_cleanup(
            job,
            HarnessImplementationJob.FailureReason.KILLED,
            workspace_dir=workspace_dir,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            test_result=test_result,
            **cost_kwargs,
        )
        return

    record_audit(
        action="harness.implementation_job_pending_review",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={"review_id": str(review.id)},
    )
    dispatch_created_review(review)


_DIFF_SUMMARY_PROMPT_NAME = "friday_harness_diff_summary"
_DIFF_SUMMARY_MAX_CHARS = 1500
_DIFF_STAT_MAX_CHARS = 6000
_WORK_LOG_TITLE_MAX_CHARS = 200


def _fallback_diff_summary(job, changed_files):
    """Deterministic, bounded summary used whenever the gateway summary
    can't be produced -- never the raw, unbounded file list."""
    bounded = changed_files[:50]
    suffix = "" if len(changed_files) <= 50 else f" (+{len(changed_files) - 50} more)"
    return (
        f"Changed {len(changed_files)} file(s) for: {job.goal[:200]}\n"
        + "\n".join(bounded)
        + suffix
    )


def _parse_diff_summary_response(text):
    """Strict parse, same discipline as `_parse_briefing_response`: a
    malformed reply is treated as no summary, never trusted raw."""
    import json

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return ""
    summary = parsed.get("summary") if isinstance(parsed, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        return ""
    if len(summary) > _DIFF_SUMMARY_MAX_CHARS:
        summary = summary[: _DIFF_SUMMARY_MAX_CHARS - 1] + "…"
    return summary


def _produce_diff_summary(job, changed_files, *, repo_dir, base_sha, head_sha):
    """Gateway-produced `diff_summary` (Phase 3 branch 6 -- the boundary
    section's intended design, which branch 2's placeholder deferred): one
    `InferenceGateway.complete()` call over the owner's goal plus the
    fenced `git diff --stat` (agent-influenced file names are untrusted
    data), recorded as ModelUsage by the gateway itself. Any failure --
    stat unreadable, prompt unconfigured, gateway error, malformed reply --
    falls back to the deterministic summary; the job never fails because
    of it."""
    try:
        stat = runner.diff_stat(repo_dir, base_sha=base_sha, head_sha=head_sha)
    except runner.RunnerError:
        logger.exception("Harness implementation job %s diff stat unavailable", job.pk)
        return _fallback_diff_summary(job, changed_files)
    if len(stat) > _DIFF_STAT_MAX_CHARS:
        stat = stat[: _DIFF_STAT_MAX_CHARS - 1] + "…"
    fenced_stat = neutralize_fence(strip_invisible_formatting(stat), "diff_stat")
    fenced_goal = neutralize_fence(strip_invisible_formatting(job.goal[:2000]), "goal")
    try:
        template = PromptTemplate.objects.get(name=_DIFF_SUMMARY_PROMPT_NAME)
        prompt_version = template.active_version
        if prompt_version is None:
            raise ImproperlyConfigured(f"{_DIFF_SUMMARY_PROMPT_NAME} has no active version")
        result = InferenceGateway().complete(
            purpose="harness.diff_summary",
            messages=[
                {"role": "system", "content": prompt_version.content},
                {
                    "role": "user",
                    "content": (
                        f"<goal>\n{fenced_goal}\n</goal>\n<diff_stat>\n{fenced_stat}\n</diff_stat>"
                    ),
                },
            ],
            actor=job.created_by,
            workspace=job.workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except (PromptTemplate.DoesNotExist, ImproperlyConfigured, GatewayError):
        logger.exception("Harness implementation job %s diff summary fell back", job.pk)
        return _fallback_diff_summary(job, changed_files)
    return _parse_diff_summary_response(result.text) or _fallback_diff_summary(job, changed_files)


def record_implementation_work_log(job):
    """ADR-008 decision 4's remaining harness producer (Phase 3 branch 6):
    a terminal implementation job that actually ran (Gate A approved)
    leaves an auto-approved, deletable `work_log` memory in its workspace
    -- the system-generated activity summary "what did we last do for this
    firm" relies on. Content is Friday-authored bookkeeping (status,
    branch, owner-written goal, the gateway-produced diff summary), never
    a CLI transcript. Best-effort: a duplicate or any error never affects
    the job itself.

    Phase 8A branch 4 (brief D5): the same hook also records the job's
    `harness` ingestion episode (owner-typed goal as owner text, the
    model-produced summaries as context) -- best-effort, never raises."""
    ingestion.record_implementation_outcome(job)
    status_label = {
        HarnessImplementationJob.Status.SUCCEEDED: "pushed",
        HarnessImplementationJob.Status.FAILED: "failed",
        HarnessImplementationJob.Status.CANCELLED: "cancelled",
    }.get(job.status)
    if status_label is None:
        return
    lines = [
        f"Harness implementation {status_label} for repo {job.repo_git_url}.",
        f"Goal: {job.goal[:500]}",
    ]
    if job.status == HarnessImplementationJob.Status.SUCCEEDED:
        lines.append(f"Branch pushed: {job.branch_name} at {job.produced_commit_sha[:12]}.")
        if job.diff_summary:
            lines.append(f"Change summary: {job.diff_summary[:1200]}")
    elif job.failure_reason:
        lines.append(f"Stopped: {job.failure_reason}.")
    try:
        record_work_log(
            workspace=job.workspace,
            title=f"Harness: {job.goal[: _WORK_LOG_TITLE_MAX_CHARS - 9]}",
            content="\n".join(lines),
            sensitivity=job.sensitivity,
        )
    except DuplicateMemory:
        pass
    except Exception:
        logger.exception("Harness implementation job %s work_log failed", job.pk)


def _claim_push_job(job_id):
    """Fenced claim for the push phase (Codex code-review P1, branch 2
    round 3) -- see `_claim_implementation_job`'s docstring for the full
    rationale, one field over: without this, a Celery redelivery (or
    `dispatch()` firing twice) while the row is still `pushing` would let
    two workers both pass an unfenced read and race through the
    irreversible `git push` together -- the loser could finalize the job
    `push_failed` moments after the winner actually pushed successfully.
    `push_started_at` is set unconditionally by this claim and never
    reset, so a second attempt finds it already populated and safely gets
    `None` back."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(
                pk=job_id,
                status=HarnessImplementationJob.Status.PUSHING,
                push_started_at__isnull=True,
            )
            .first()
        )
        if locked is None:
            return None
        locked.push_started_at = timezone.now()
        locked.save(update_fields=["push_started_at", "updated_at"])
        return locked


def run_harness_push_job(job_id):
    job = _claim_push_job(job_id)
    if job is None:
        return
    if not job.worktree_path:
        _finalize_push_failure(job, HarnessImplementationJob.FailureReason.PUSH_FAILED)
        return
    # Codex code-review P2, branch 2 round 4: without this, Gate B being
    # approved after `worktree_expires_at` has already passed (the
    # recovery sweep that's meant to catch this, branch 4, hasn't run yet
    # or is behind) would still bundle and push a worktree the retention
    # deadline says should no longer be trusted. Checked before any
    # cleanup attempt below, so the worktree's own removal still goes
    # through the same confirmed-cleanup path as every other exit.
    if job.worktree_expires_at is not None and timezone.now() > job.worktree_expires_at:
        cleanup_confirmed = _safe_cleanup_directory(job.worktree_path)
        _finalize_push_failure(
            job,
            HarnessImplementationJob.FailureReason.WORKTREE_EXPIRED,
            worktree_cleanup_confirmed=cleanup_confirmed,
        )
        return

    repo_dir = f"{job.worktree_path}/repo"
    bundle_dir = None
    cancelled = False
    push_error = None
    try:
        bundle_dir = tempfile.mkdtemp(prefix="harness-bundle-")
        bundle_path = f"{bundle_dir}/bundle"
        runner.create_bundle(
            repo_dir, base_sha=job.base_commit_sha, ref=job.branch_name, bundle_path=bundle_path
        )
        # Codex code-review P1, branch 2 round 2: `pushing` has no
        # polling loop at all (unlike `implementing`'s
        # `_wait_for_implementation_container`) -- a curator cancelling a
        # job after Gate B approval only ever set `cancel_requested`
        # (`cancel_harness_implementation_job` leaves `pushing` jobs in
        # place for exactly this loop to observe it), and nothing here
        # ever read that flag before now. This is the one meaningful
        # checkpoint: right before the actual network push, the single
        # irreversible remote-mutating step in this whole function.
        if HarnessImplementationJob.objects.filter(pk=job_id, cancel_requested=True).exists():
            cancelled = True
        else:
            runner.push_bundled_commit(
                git_url=job.repo_git_url,
                default_branch=job.repo_default_branch,
                branch_name=job.branch_name,
                bundle_path=bundle_path,
                base_sha=job.base_commit_sha,
                produced_sha=job.produced_commit_sha,
            )
    except runner.RunnerError as exc:
        logger.exception("Harness push job %s failed", job_id)
        push_error = str(exc)
    finally:
        # Codex code-review P2, branch 2 round 4: an earlier version
        # finalized (clearing `worktree_path` unconditionally) from
        # WITHIN the `except`/inline branches, before this `finally` ever
        # ran -- so a cleanup failure right here could still have already
        # lost the DB's only pointer to the leaked directory. Finalizing
        # is now deferred until after this block has actually run,
        # everywhere, and threads its confirmed/failed outcome through.
        if bundle_dir:
            _safe_cleanup_directory(bundle_dir)
        worktree_cleanup_confirmed = _safe_cleanup_directory(job.worktree_path)

    if push_error is not None:
        _finalize_push_failure(
            job,
            HarnessImplementationJob.FailureReason.PUSH_FAILED,
            error=push_error,
            worktree_cleanup_confirmed=worktree_cleanup_confirmed,
        )
        return
    if cancelled:
        _finalize_push_cancelled(job, worktree_cleanup_confirmed=worktree_cleanup_confirmed)
        return
    _finalize_push_success(job, worktree_cleanup_confirmed=worktree_cleanup_confirmed)


def _apply_worktree_cleanup(locked, update_fields, *, worktree_cleanup_confirmed):
    """Shared by all three finalize functions below (Codex code-review
    P2, branch 2 round 4): only clear `worktree_path` once cleanup is
    actually confirmed -- mirrors `harness.services.
    _cleanup_worktree_best_effort`'s identical discipline. Leaving the
    path populated on a failed cleanup is what lets a future recovery
    sweep find and retry the genuinely leaked directory."""
    if worktree_cleanup_confirmed:
        locked.worktree_path = ""
        update_fields.append("worktree_path")


def _finalize_push_success(job, *, worktree_cleanup_confirmed=True):
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job.pk, status=HarnessImplementationJob.Status.PUSHING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.SUCCEEDED
        locked.push_result = {"pushed": True, "branch_name": locked.branch_name}
        update_fields = ["status", "push_result", "updated_at"]
        _apply_worktree_cleanup(
            locked, update_fields, worktree_cleanup_confirmed=worktree_cleanup_confirmed
        )
        locked.save(update_fields=update_fields)
    record_audit(
        action="harness.implementation_job_succeeded",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={},
    )
    record_implementation_work_log(locked)


def _finalize_push_cancelled(job, *, worktree_cleanup_confirmed=True):
    """Codex code-review P1, branch 2 round 2 -- see `run_harness_push_job`'s
    own comment: a cancellation observed right before the network push
    never actually pushed anything, so this is a plain cancellation, not
    a failure."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job.pk, status=HarnessImplementationJob.Status.PUSHING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.CANCELLED
        locked.failure_reason = HarnessImplementationJob.FailureReason.KILLED
        locked.push_result = {"pushed": False}
        update_fields = ["status", "failure_reason", "push_result", "updated_at"]
        _apply_worktree_cleanup(
            locked, update_fields, worktree_cleanup_confirmed=worktree_cleanup_confirmed
        )
        locked.save(update_fields=update_fields)
    record_audit(
        action="harness.implementation_job_cancelled",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={},
    )
    record_implementation_work_log(locked)


def _finalize_push_failure(job, failure_reason, *, error="", worktree_cleanup_confirmed=True):
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job.pk, status=HarnessImplementationJob.Status.PUSHING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = failure_reason
        # Git's own stderr, scrubbed of credentials and bounded before it is
        # stored and shown to the owner (Phase 3 branch 6 review).
        locked.push_result = {"pushed": False, "error": redact_harness_secrets(error)[:500]}
        update_fields = ["status", "failure_reason", "push_result", "updated_at"]
        _apply_worktree_cleanup(
            locked, update_fields, worktree_cleanup_confirmed=worktree_cleanup_confirmed
        )
        locked.save(update_fields=update_fields)
    record_audit(
        action="harness.implementation_job_failed",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={"failure_reason": failure_reason},
    )
    record_implementation_work_log(locked)


def recover_stale_harness_implementation_jobs():
    """Periodic scan (Phase 3 branch 4) covering the same class of
    permanently-stuck cases `harness.execution.recover_stale_harness_jobs`
    already covers for discovery, mapped onto `HarnessImplementationJob`'s
    two ACTIVE-LOOP phases (`implementing`, `pushing`) -- each of which
    has its own "worker crashed mid-run" AND "the gate-approval-triggered
    dispatch was lost" variant. Unlike discovery, which self-dispatches
    at creation, each phase here only starts once a human approves a
    gate, and `harness.tools.dispatch_harness_implement_execution`/
    `dispatch_harness_push_execution` swallow a broker-publish failure
    the same way discovery's own dispatch does -- a job whose gate was
    approved right as the broker was down is durably `implementing`/
    `pushing` (that transition already committed, inside the approval's
    own transaction) with no Celery task ever actually launched to claim
    it.

    1. `implementing`, `started_at` unset, past
       `HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS` since its own
       `updated_at` (the status transition itself) -- lost dispatch,
       simply re-dispatched; `_claim_implementation_job`'s own
       `started_at__isnull=True` fence makes this safe even if the
       original dispatch actually does arrive later.
    2. `implementing`, `started_at` set, past `HARNESS_MAX_JOB_SECONDS`
       + `HARNESS_RECOVERY_GRACE_SECONDS` since then -- worker crashed
       mid-run; failed closed via `_claim_stale_implementing_job` (Codex
       code-review P2, branch 4 round 4), which re-checks status AND
       transitions atomically under one lock BEFORE any container
       teardown or worktree cleanup is attempted -- an earlier version
       cleaned up first and only checked afterward, so a live worker
       genuinely still finishing at that exact moment could have its
       still-needed worktree deleted before the check ever ran, even
       though the resulting DB write itself would have correctly
       no-op'd. Claiming first means an overlapping scan iteration (or
       a live worker that actually does finish moments later) safely
       finds nothing to do at all, rather than deleting something out
       from under a race it hasn't actually won.
    3. `pushing`, `push_started_at` unset -- same lost-dispatch shape as
       case 1, one gate later.
    4. `pushing`, `push_started_at` set, same worker-crashed-mid-run
       shape as case 2, and the same claim-before-cleanup fix (round 4)
       applied one phase later, via `_claim_stale_pushing_job` -- no
       container involved in this phase at all (the actual `git push`
       is a host-side operation, branch 1's own design, never inside a
       Docker container), so only the worktree itself needs protecting
       from a premature deletion.
    5. The worktree-retention backstop (brief §boundary, this branch):
       a job sitting `pending_gate_b` -- idle, waiting on a human Gate B
       decision, nothing left touching its worktree -- whose
       `worktree_expires_at` has already passed. `run_harness_push_job`
       already fails this closed reactively if Gate B is ever actually
       approved past the deadline (branch 2 round 4), but nothing
       reclaims the abandoned directory, or fails the job, if the
       decision just never comes; see `_sweep_expired_worktree_job`'s
       own docstring for why this is deliberately scoped to
       `pending_gate_b` only, not `pending_review` too.
    6. A terminal job (`succeeded`/`failed`/`cancelled`) still carrying a
       non-empty `worktree_path`, past
       `HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS` since its last
       update (Codex code-review P2, branch 4 round 3): every finalize
       path's own cleanup attempt (`_apply_worktree_cleanup`'s confirmed-
       before-clearing discipline) genuinely failed, and unlike case 2,
       a terminal job is never swept by the `status=implementing` scan
       above -- without this case the directory would persist on the
       host indefinitely, with nothing ever revisiting it. Mirrors
       `harness.execution.recover_stale_harness_jobs`'s identical
       leaked-terminal-container case, one resource type over -- reusing
       the same grace-period setting despite its container-specific
       name, since the underlying "retry a confirmed-failed cleanup"
       shape is identical.
    7. A terminal job still carrying a non-empty `container_id`, same
       grace period (Codex code-review P2, branch 4 round 5): case 6's
       own analogue for `_recover_stale_implementing_job`'s (or any
       other finalize path's) confirmed-before-clearing container
       removal -- a genuinely failed `docker rm` otherwise leaves a
       stopped container, still carrying the CLI's own credentials in
       its environment, on the runner host indefinitely. Mirrors
       `harness.execution`'s and `harness.review_execution`'s identical
       leaked-terminal-container cases, one job type over.
    8. `pending_review` with no `pending`/`running` review, past
       `HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS` since its last update
       (Phase 3 branch 5, 2026-09-27 audit): before this branch the review
       row was created after `pending_review` committed, so a crash in
       between (or a reviewer that stopped being selectable) left the job
       waiting forever on a review nothing would ever run. New jobs create
       the row atomically; this backstop fails any such job closed as
       `review_unavailable` (worktree cleaned up) -- a succeeded/failed
       review always moves the job on in its own transaction, so no
       active review means nothing will."""
    dispatch_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS
    )
    running_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_MAX_JOB_SECONDS + settings.HARNESS_RECOVERY_GRACE_SECONDS
    )

    # Case 1: implementing, dispatch lost.
    stale_implementing_dispatch_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at__isnull=True,
            updated_at__lt=dispatch_cutoff,
        ).values_list("pk", flat=True)
    )
    for job_id in stale_implementing_dispatch_ids:
        from harness.tools import dispatch_harness_implement_execution

        dispatch_harness_implement_execution(str(job_id))

    # Case 2: implementing, worker crashed mid-run.
    stale_implementing_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.IMPLEMENTING,
            started_at__isnull=False,
            started_at__lt=running_cutoff,
        ).values_list("pk", flat=True)
    )
    for job_id in stale_implementing_ids:
        _recover_stale_implementing_job(job_id, running_cutoff)

    # Case 3: pushing, dispatch lost.
    stale_pushing_dispatch_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.PUSHING,
            push_started_at__isnull=True,
            updated_at__lt=dispatch_cutoff,
        ).values_list("pk", flat=True)
    )
    for job_id in stale_pushing_dispatch_ids:
        from harness.tools import dispatch_harness_push_execution

        dispatch_harness_push_execution(str(job_id))

    # Case 4: pushing, worker crashed mid-run.
    stale_pushing_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.PUSHING,
            push_started_at__isnull=False,
            push_started_at__lt=running_cutoff,
        ).values_list("pk", flat=True)
    )
    for job_id in stale_pushing_ids:
        _recover_stale_pushing_job(job_id, running_cutoff)

    # Case 5: worktree-retention backstop.
    expired_worktree_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.PENDING_GATE_B,
            worktree_expires_at__lt=timezone.now(),
        ).values_list("pk", flat=True)
    )
    for job_id in expired_worktree_ids:
        _sweep_expired_worktree_job(job_id)

    # Case 8: pending_review with no active review to resolve it.
    orphaned_review_ids = list(
        HarnessImplementationJob.objects.filter(
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            updated_at__lt=dispatch_cutoff,
        )
        .exclude(reviews__status__in=(HarnessReview.Status.PENDING, HarnessReview.Status.RUNNING))
        .values_list("pk", flat=True)
    )
    for job_id in orphaned_review_ids:
        from harness.services import fail_implementation_job_for_review

        job = HarnessImplementationJob.objects.filter(pk=job_id).first()
        if job is None:
            continue
        # Re-checked under the job lock by the service itself; a review
        # created in the meantime still wins because the service only
        # acts on `pending_review`, and the next scan would see it active.
        if job.reviews.filter(
            status__in=(HarnessReview.Status.PENDING, HarnessReview.Status.RUNNING)
        ).exists():
            continue
        fail_implementation_job_for_review(
            job=job,
            review=None,
            failure_reason=HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE,
        )

    # Case 6: terminal job, leaked worktree.
    leaked_worktree_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS
    )
    leaked_worktree_ids = list(
        HarnessImplementationJob.objects.filter(
            status__in=_IMPLEMENTATION_TERMINAL_STATUSES, updated_at__lt=leaked_worktree_cutoff
        )
        .exclude(worktree_path="")
        .values_list("pk", flat=True)
    )
    for job_id in leaked_worktree_ids:
        _sweep_leaked_terminal_worktree(job_id)

    # Case 7: terminal job, leaked container.
    leaked_container_ids = list(
        HarnessImplementationJob.objects.filter(
            status__in=_IMPLEMENTATION_TERMINAL_STATUSES, updated_at__lt=leaked_worktree_cutoff
        )
        .exclude(container_id="")
        .values_list("pk", flat=True)
    )
    for job_id in leaked_container_ids:
        _sweep_leaked_terminal_implementation_container(job_id)


def _claim_stale_implementing_job(job_id, threshold):
    """Fenced claim for case 2 above (Codex code-review P2, branch 4
    round 4), mirroring `harness.execution._claim_stale_job`'s identical
    discipline: the re-check AND the transition to a terminal status
    happen atomically, under the SAME lock. An earlier version read the
    row unlocked first, then tore down its container and cleaned up its
    worktree, and only THEN called `_fail_and_cleanup` for the real,
    locked check -- meaning a live worker that was genuinely still
    finishing (about to succeed, transitioning `implementing` ->
    `pending_review`, right as this scan caught a stale read) could have
    its still-needed worktree deleted before that check ever ran, even
    though the DB write itself would then correctly no-op. Claiming
    first closes this: either this claim wins the race (the worker's own
    later write safely no-ops, finding status no longer `implementing`),
    or the worker already won (this claim finds nothing, returning
    `None` before ANY cleanup -- container teardown, directory removal
    -- is ever attempted)."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(
                pk=job_id,
                status=HarnessImplementationJob.Status.IMPLEMENTING,
                started_at__lt=threshold,
            )
            .first()
        )
        if locked is None:
            return None
        cancelled = locked.cancel_requested
        locked.status = (
            HarnessImplementationJob.Status.CANCELLED
            if cancelled
            else HarnessImplementationJob.Status.FAILED
        )
        locked.failure_reason = (
            HarnessImplementationJob.FailureReason.KILLED
            if cancelled
            else HarnessImplementationJob.FailureReason.TIMEOUT
        )
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
        return locked


def _recover_stale_implementing_job(job_id, threshold):
    """One iteration of case 2 above. Claims the row FIRST -- see
    `_claim_stale_implementing_job`'s own docstring -- then tears down
    its container and cleans up its worktree, then records the same
    usage/audit accounting `_fail_and_cleanup` would for a live
    in-process failure. Duplicated rather than shared with that
    function: by this point the transition has already happened via the
    claim, and `_fail_and_cleanup`'s own internal `status == implementing`
    gate would otherwise just no-op against a row this claim already
    moved past."""
    job = _claim_stale_implementing_job(job_id, threshold)
    if job is None:
        return
    removal_confirmed = True
    if job.container_id:
        _teardown_container(job.container_id)
        removal_confirmed = _safe_remove_container(job.container_id)
    worktree_cleanup_confirmed = (
        _safe_cleanup_directory(job.worktree_path) if job.worktree_path else True
    )
    update_fields = {}
    if removal_confirmed:
        update_fields["container_id"] = ""
    if worktree_cleanup_confirmed:
        update_fields["worktree_path"] = ""
    if update_fields:
        HarnessImplementationJob.objects.filter(pk=job.pk).update(**update_fields)
    record_external_usage(
        workspace=job.workspace,
        purpose="harness.implementation",
        backend=f"harness_cli:{job.agent}",
        routing_reason=job.agent_routing_reason,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=Decimal("0"),
        latency_ms=0,
        success=False,
        actor=job.created_by,
        error_type=job.failure_reason,
        context_report={"usage_source": "unavailable", "recovered": True},
    )
    # Codex code-review P3, branch 4 round 5: `_claim_stale_implementing_
    # job` stores `cancelled` (not `failed`) when the job had `cancel_
    # requested=True` at claim time -- the audit action must agree with
    # the status it's actually describing, mirroring `_record_
    # implementation_job`'s own cancelled-vs-failed action split
    # elsewhere in this module.
    audit_action = (
        "harness.implementation_job_cancelled"
        if job.status == HarnessImplementationJob.Status.CANCELLED
        else "harness.implementation_job_failed"
    )
    record_audit(
        action=audit_action,
        actor=job.created_by,
        workspace=job.workspace,
        resource=job,
        metadata={"failure_reason": job.failure_reason, "recovered": True},
    )
    record_implementation_work_log(job)


def _claim_stale_pushing_job(job_id, threshold):
    """Fenced claim for case 4 above, mirroring
    `_claim_stale_implementing_job`'s identical discipline one phase
    later -- see that function's own docstring for the full race this
    closes. Never the `cancelled` branch `_claim_stale_implementing_job`
    has: `_finalize_push_failure` (the live in-process equivalent this
    mirrors) has no such branch either -- a cancellation mid-push is
    handled entirely by its own dedicated checkpoint right before the
    network push, a genuinely different code path from a stuck/crashed
    push this recovery case exists for."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(
                pk=job_id,
                status=HarnessImplementationJob.Status.PUSHING,
                push_started_at__lt=threshold,
            )
            .first()
        )
        if locked is None:
            return None
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = HarnessImplementationJob.FailureReason.TIMEOUT
        locked.push_result = {
            "pushed": False,
            "error": "recovered: no live worker finalized this push in time",
        }
        locked.save(update_fields=["status", "failure_reason", "push_result", "updated_at"])
        return locked


def _recover_stale_pushing_job(job_id, threshold):
    """One iteration of case 4 above. Claims the row FIRST -- see
    `_claim_stale_pushing_job`'s own docstring -- before ever attempting
    worktree cleanup, for the identical reason
    `_recover_stale_implementing_job` does: a live worker that's
    genuinely still finishing (about to succeed, or mid-push) right as
    this scan catches a stale read must never have its worktree deleted
    out from under it before it's actually done with it (`run_harness_
    push_job` bundles from the worktree before the actual network
    push)."""
    job = _claim_stale_pushing_job(job_id, threshold)
    if job is None:
        return
    worktree_cleanup_confirmed = (
        _safe_cleanup_directory(job.worktree_path) if job.worktree_path else True
    )
    if worktree_cleanup_confirmed:
        HarnessImplementationJob.objects.filter(pk=job.pk).update(worktree_path="")
    record_audit(
        action="harness.implementation_job_failed",
        actor=job.created_by,
        workspace=job.workspace,
        resource=job,
        metadata={"failure_reason": job.failure_reason, "recovered": True},
    )
    record_implementation_work_log(job)


def _sweep_expired_worktree_job(job_id):
    """One iteration of case 5 above. Deliberately scoped to
    `pending_gate_b` only, not `pending_review` too: a job still
    `pending_review` may have an ACTIVELY RUNNING review reading from
    that same worktree right now (`runner.create_bundle` bundles
    directly from `job.worktree_path/repo`) -- deleting the directory
    out from under an in-flight review would be a genuine race, not
    just a theoretical one. A review stuck long enough to matter here is
    caught, and resolved, much sooner by the review recovery scan's own
    timeout case anyway (`HARNESS_MAX_JOB_SECONDS` +
    `HARNESS_RECOVERY_GRACE_SECONDS`, on the order of minutes --
    worktree retention is `HARNESS_WORKTREE_RETENTION_HOURS`, on the
    order of days), so by the time a worktree could plausibly still be
    needed by a genuinely-abandoned review this far past its own
    deadline, the review has already been resolved one way or another
    and the job has already moved on from `pending_review`.

    Also revokes the now-orphaned `gate_b_request` (Phase 3 branch 4,
    `approvals.services.revoke_request`) -- otherwise a curator would
    keep seeing a Gate B request sitting in their inbox for a job the
    backend has already failed closed."""
    job = HarnessImplementationJob.objects.filter(
        pk=job_id,
        status=HarnessImplementationJob.Status.PENDING_GATE_B,
        worktree_expires_at__lt=timezone.now(),
    ).first()
    if job is None:
        return
    worktree_cleanup_confirmed = (
        _safe_cleanup_directory(job.worktree_path) if job.worktree_path else True
    )
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status=HarnessImplementationJob.Status.PENDING_GATE_B)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessImplementationJob.Status.FAILED
        locked.failure_reason = HarnessImplementationJob.FailureReason.WORKTREE_EXPIRED
        update_fields = ["status", "failure_reason", "updated_at"]
        _apply_worktree_cleanup(
            locked, update_fields, worktree_cleanup_confirmed=worktree_cleanup_confirmed
        )
        locked.save(update_fields=update_fields)
        gate_b_request = locked.gate_b_request
    record_audit(
        action="harness.implementation_job_failed",
        actor=locked.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={
            "failure_reason": HarnessImplementationJob.FailureReason.WORKTREE_EXPIRED,
            "recovered": True,
        },
    )
    record_implementation_work_log(locked)
    if gate_b_request is not None:
        from harness.services import _revoke_gate_request_best_effort

        # No actor (Codex code-review P2, branch 4 round 6): this is a
        # passive worktree-retention timeout, not a human choosing to
        # withdraw the request -- passing `locked.created_by` would
        # misattribute it as an explicit decision by the job's owner in
        # the approvals audit trail. `revoke_request`/`record_revoked`
        # record a `None` actor as `SYSTEM`, the correct classification
        # here.
        _revoke_gate_request_best_effort(gate_b_request)


def _sweep_leaked_terminal_worktree(job_id):
    """One iteration of case 6 above. Re-fetches under lock first
    (mirrors `harness.execution._sweep_leaked_terminal_container`'s
    identical discipline) so two overlapping scans don't both attempt
    removal of the same directory -- the filesystem call itself happens
    OUTSIDE the lock, same as every other recovery case in this
    module."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status__in=_IMPLEMENTATION_TERMINAL_STATUSES)
            .exclude(worktree_path="")
            .first()
        )
        if locked is None:
            return False
        worktree_path = locked.worktree_path
    if _safe_cleanup_directory(worktree_path):
        HarnessImplementationJob.objects.filter(pk=job_id, worktree_path=worktree_path).update(
            worktree_path=""
        )
        return True
    return False


def _sweep_leaked_terminal_implementation_container(job_id):
    """One iteration of case 7 above (Codex code-review P2, branch 4
    round 5). Re-fetches under lock first, same discipline as
    `_sweep_leaked_terminal_worktree` above -- so two overlapping scans
    don't both attempt removal of the same container -- the Docker call
    itself happens OUTSIDE the lock, same as every other recovery case
    in this module."""
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status__in=_IMPLEMENTATION_TERMINAL_STATUSES)
            .exclude(container_id="")
            .first()
        )
        if locked is None:
            return False
        container_id = locked.container_id
    if _safe_remove_container(container_id):
        HarnessImplementationJob.objects.filter(pk=job_id, container_id=container_id).update(
            container_id=""
        )
        return True
    return False
