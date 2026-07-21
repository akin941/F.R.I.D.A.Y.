"""`HarnessReview` execution engine (Phase 3 branch 3, brief §boundary).
Celery-agnostic -- `harness.tasks` is a thin wrapper, mirroring
`harness.execution`/`harness.implementation_execution`'s existing split.
Its own narrowly-scoped execution function, not the discovery job's
engine and not a shared one (brief §boundary, round-1 MEDIUM finding,
accepted -- see `harness.implementation_execution`'s identical rationale).

Read-only: the review container gets a CLEAN, agent-config-untouched
checkout bind-mounted (`runner.prepare_review_clone`, Codex code-review
P1, branch 3 round 1) -- never `job.worktree_path/repo` directly, since
the agent had full write access to that worktree's `.git/config`/
`.gitattributes` right up until tests passed, and `git diff`'s own
config-driven mechanisms (`diff.external`/`textconv`) could hide the
real diff from the reviewer or execute arbitrary code inside the review
container. The reviewed range is bundled from the worktree (a local,
non-networked, config-insensitive operation, mirroring branch 1's own
push design) and fetched into a fresh clean clone instead.

Gateway-summarization pipeline for `summary`/`findings` reuses Phase 2
branch 3's exact fencing discipline (`neutralize_fence`/
`strip_invisible_formatting`) -- the review CLI's own transcript is
exactly as much low-trust content as the discovery briefing's CLI
transcript was, and `verdict` gets the identical strict-parse-or-
unavailable treatment: a malformed/wrong-shape gateway reply is NEVER
trusted as a bare approval, it fails the review (and the implementation
job) closed as `review_unavailable`.
"""

import json
import logging
import tempfile
import time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway, record_external_usage
from ai_core.models import PromptTemplate
from audit.services import record_audit
from harness import runner
from harness.execution import _resolve_cli_env
from harness.models import HarnessImplementationJob, HarnessReview
from memory.context import neutralize_fence, strip_invisible_formatting

logger = logging.getLogger(__name__)

_HARNESS_REVIEW_PROMPT_NAME = "jarvis_harness_review"
# Same bounding discipline as `harness.execution`'s identical constants --
# see that module's docstring for the full rationale.
_RAW_TRANSCRIPT_MAX_CHARS = 8000
_REVIEW_SUMMARY_MAX_CHARS = 2000
_REVIEW_FINDINGS_MAX_COUNT = 20
_REVIEW_FINDING_MAX_CHARS = 500


def select_review_agent(implementation_agent):
    """Picks a review agent that differs from the implementation job's
    own agent -- the mechanical cross-vendor-review enforcement ADR-008
    decision 3 requires (brief §boundary), enforced here explicitly
    rather than relying on `_SELECTABLE_IMPLEMENTATION_AGENTS`/
    `_SELECTABLE_REVIEW_AGENTS` happening to stay disjoint. Raises
    `HarnessInvalidTransition` if no eligible agent is currently
    selectable."""
    from harness.services import _SELECTABLE_REVIEW_AGENTS, HarnessInvalidTransition

    eligible = [agent for agent in _SELECTABLE_REVIEW_AGENTS if agent != implementation_agent]
    if not eligible:
        raise HarnessInvalidTransition(
            "No eligible cross-vendor review agent is currently selectable."
        )
    return eligible[0]


def dispatch_harness_review(job):
    """Creates the pending `HarnessReview` row and schedules its own
    Celery task -- the real implementation of the call site
    `harness.implementation_execution.run_harness_implementation_job`
    invokes once tests pass (branch 2 shipped this as a stub; this
    branch wires the real dispatch, per brief §boundary)."""
    agent = select_review_agent(job.agent)
    review = HarnessReview.objects.create(
        workspace=job.workspace,
        implementation_job=job,
        agent=agent,
        status=HarnessReview.Status.PENDING,
    )
    _dispatch_review_task(review.id)
    return review


def _dispatch_review_task(review_id):
    from harness.tasks import run_harness_review_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_harness_review_job.delay(str(review_id))
        return
    try:
        run_harness_review_job.delay(str(review_id))
    except Exception:
        logger.exception("Failed to publish harness review %s to the broker", review_id)
        # Codex code-review P2, branch 3 round 2: unlike `HarnessJob`,
        # there is no periodic recovery scan for this job type yet
        # (branch 4's job) -- without resolving this immediately, a lost
        # dispatch would leave the review `pending` and the implementation
        # job `pending_review` forever, with nothing left to ever claim
        # and run it. Fails both closed right here instead of waiting on
        # a future sweep that doesn't exist yet.
        resolve_pending_review_after_dispatch_failure(review_id)


def resolve_pending_review_after_dispatch_failure(review_id):
    """See `_dispatch_review_task`'s call site above for the full
    rationale. Fenced on `status=pending` -- the review was just created
    and never claimed, so there is no container/worktree to tear down,
    only the row's own status and the implementation job's."""
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review_id, status=HarnessReview.Status.PENDING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessReview.Status.FAILED
        locked.save(update_fields=["status", "updated_at"])
    record_audit(
        action="harness.review_failed",
        actor=locked.implementation_job.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={"reason": "dispatch_failed"},
    )
    from harness.services import fail_implementation_job_for_review

    fail_implementation_job_for_review(
        job=locked.implementation_job,
        review=locked,
        failure_reason=HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE,
    )


def _claim_review(review_id):
    """Fenced claim: `pending` -> `running`. Mirrors
    `harness.execution._claim_job`'s exact shape."""
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review_id, status=HarnessReview.Status.PENDING)
            .first()
        )
        if locked is None:
            return None
        locked.status = HarnessReview.Status.RUNNING
        locked.started_at = timezone.now()
        locked.save(update_fields=["status", "started_at", "updated_at"])
        return locked


def _teardown_container(container_id):
    try:
        runner.stop_container(container_id, grace_seconds=settings.HARNESS_KILL_GRACE_SECONDS)
        if runner.container_is_running(container_id):
            runner.kill_container(container_id)
    except Exception:
        logger.exception("Failed to stop/kill harness review container %s", container_id)


def _safe_remove_container(container_name):
    try:
        runner.remove_container(container_name)
        return True
    except Exception:
        logger.exception(
            "Failed to remove harness review container %s (leaked on host)", container_name
        )
        return False


def _safe_cleanup_directory(path):
    """Both the ephemeral bundle tempdir and the clean review clone are
    genuinely temporary -- unlike `HarnessImplementationJob.worktree_path`,
    nothing persists a pointer to either in the database, so there is no
    `worktree_path`-style confirmed-cleanup discipline to apply here; a
    cleanup failure just leaks a temp directory on the runner host, the
    same lesser residual risk `harness.execution`'s own read-only clones
    already accept."""
    try:
        runner.cleanup_directory(path)
    except Exception:
        logger.exception("Failed to clean up harness review directory %s", path)


def _wait_for_review_container(container_id, started_monotonic):
    """Timeout-only brake -- no `cancel_requested` equivalent exists on
    `HarnessReview` itself (brief §boundary: cancelling the underlying
    `HarnessImplementationJob` while it's `pending_review` already
    transitions the JOB straight to `cancelled` -- see
    `harness.services.cancel_harness_implementation_job` -- this
    container's own review is simply discarded once it finishes, since
    `request_harness_push`/`fail_implementation_job_for_review` both
    re-check the job's live status before acting on the verdict)."""
    while runner.container_is_running(container_id):
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= settings.HARNESS_MAX_JOB_SECONDS:
            transcript = runner.capture_logs(container_id)
            _teardown_container(container_id)
            return "failed", transcript
        time.sleep(settings.HARNESS_CANCEL_POLL_SECONDS)
    transcript = runner.capture_logs(container_id)
    exit_code = runner.container_exit_code(container_id)
    if exit_code != 0:
        return "failed", transcript
    return "succeeded", transcript


def _build_review_goal(job):
    """The review CLI has full read access to the clean checkout
    `runner.prepare_review_clone` prepared (itself derived from a full,
    non-shallow clone) -- so it can run `git diff` itself against the
    immutable, host-resolved `base_commit_sha` the implementation phase
    already recorded, never a mutable ref the agent could have
    rewritten. `HEAD` in the clean clone is `produced_commit_sha`, the
    exact commit `prepare_review_clone` verified and checked out."""
    return (
        f"Review the code changes committed on this branch. The stated goal of the "
        f"change was: {job.goal}\n\n"
        f"Run `git diff {job.base_commit_sha}..HEAD` to see exactly what changed, then "
        f"review it for correctness, safety, and whether it actually accomplishes the "
        f"stated goal. Conclude with a clear verdict: approve the change, or request "
        f"changes and explain what's wrong."
    )


def _fence_and_bound_transcript(raw_output):
    """Mirrors `harness.execution._fence_and_bound_transcript` exactly --
    see that function's docstring for the full rationale."""
    bounded_raw = raw_output
    if len(bounded_raw) > _RAW_TRANSCRIPT_MAX_CHARS:
        bounded_raw = bounded_raw[: _RAW_TRANSCRIPT_MAX_CHARS - 1] + "…"
    fenced_for_prompt = neutralize_fence(strip_invisible_formatting(bounded_raw), "repo_transcript")
    return bounded_raw, fenced_for_prompt


def _review_prompt_version():
    """Mirrors `harness.execution._harness_briefing_prompt_version`'s
    identical shape -- a missing/misconfigured prompt template is a hard
    failure, not a silent degrade, since routing the verdict through
    Jarvis's own gateway call (rather than trusting the CLI's own
    self-report) is the actual R2/prompt-injection mitigation this
    pipeline exists to provide."""
    try:
        template = PromptTemplate.objects.get(name=_HARNESS_REVIEW_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_HARNESS_REVIEW_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_HARNESS_REVIEW_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _parse_review_response(text):
    """Strict parse of the gateway's own JSON response -- mirrors
    `harness.execution._parse_briefing_response`'s identical discipline,
    extended with `verdict`. A malformed/wrong-shape reply, OR a
    `verdict` that isn't exactly one of the two recognized values, is
    treated as NO verdict at all (never defaults to approval) -- the
    fenced transcript is still attacker-influenced content, so a prompt
    injection that talks the model out of returning the requested shape
    must not fall through to an ambiguous reply being trusted either
    way."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return "", [], ""
    if not isinstance(parsed, dict):
        return "", [], ""
    summary = parsed.get("summary")
    verdict = parsed.get("verdict")
    findings = parsed.get("findings")
    if not isinstance(summary, str) or verdict not in (
        HarnessReview.Verdict.APPROVED,
        HarnessReview.Verdict.CHANGES_REQUESTED,
    ):
        return "", [], ""
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        findings = []
    if len(summary) > _REVIEW_SUMMARY_MAX_CHARS:
        summary = summary[: _REVIEW_SUMMARY_MAX_CHARS - 1] + "…"
    bounded_findings = []
    for item in findings[:_REVIEW_FINDINGS_MAX_COUNT]:
        if len(item) > _REVIEW_FINDING_MAX_CHARS:
            item = item[: _REVIEW_FINDING_MAX_CHARS - 1] + "…"
        bounded_findings.append(item)
    return summary, bounded_findings, verdict


def _produce_review_summary(job, fenced_transcript):
    prompt_version = _review_prompt_version()
    messages = [
        {"role": "system", "content": prompt_version.content},
        {
            "role": "user",
            "content": f"<repo_transcript>\n{fenced_transcript}\n</repo_transcript>",
        },
    ]
    gateway = InferenceGateway()
    result = gateway.complete(
        purpose="harness.review",
        messages=messages,
        actor=job.created_by,
        workspace=job.workspace,
        prompt_version=prompt_version,
        stream=False,
    )
    return _parse_review_response(result.text)


def run_harness_review_job(review_id):
    review = _claim_review(review_id)
    if review is None:
        return
    job = review.implementation_job

    container_name = f"harness-review-{review.pk}"
    HarnessReview.objects.filter(pk=review.pk).update(container_id=container_name)

    started_monotonic = time.monotonic()
    removal_confirmed = True
    bundle_dir = None
    clean_clone_dir = None
    status, raw_output = "failed", ""
    try:
        # Codex code-review P1, branch 3 round 1: mounting the agent's
        # OWN worktree (`job.worktree_path/repo`) directly, and asking
        # the reviewer to run `git diff` inside it, would run git in a
        # repo whose `.git/config`/`.gitattributes` the agent had full
        # write access to right up until tests passed -- `diff.external`/
        # `textconv` are config-driven mechanisms that could hide the
        # real diff from the reviewer or execute arbitrary code inside
        # the review container, which has the review CLI's own
        # credentials in its environment. Bundle the reviewed range from
        # the worktree (a local, non-networked, config-insensitive
        # operation -- mirrors branch 1's own push design) and prepare a
        # clean, agent-config-untouched checkout from it instead; only
        # THAT gets mounted.
        bundle_dir = tempfile.mkdtemp(prefix="harness-review-bundle-")
        bundle_path = f"{bundle_dir}/bundle"
        repo_dir = f"{job.worktree_path}/repo"
        runner.create_bundle(
            repo_dir, base_sha=job.base_commit_sha, ref=job.branch_name, bundle_path=bundle_path
        )
        clean_clone_dir = runner.prepare_review_clone(
            git_url=job.repo_git_url,
            default_branch=job.repo_default_branch,
            branch_name=job.branch_name,
            bundle_path=bundle_path,
            base_sha=job.base_commit_sha,
            produced_sha=job.produced_commit_sha,
        )
        image = runner.image_for_agent(review.agent)
        cli_env = _resolve_cli_env(review.agent)
        runner.launch_container(
            image=image,
            clone_dir=clean_clone_dir,
            goal=_build_review_goal(job),
            cli_env=cli_env,
            name=container_name,
        )
        status, raw_output = _wait_for_review_container(container_name, started_monotonic)
    except runner.RunnerError:
        logger.exception("Harness review %s failed to launch", review_id)
    finally:
        removal_confirmed = _safe_remove_container(container_name)
        if bundle_dir:
            _safe_cleanup_directory(bundle_dir)
        if clean_clone_dir:
            _safe_cleanup_directory(clean_clone_dir)

    if status != "succeeded" or not raw_output:
        _finalize_review_unavailable(
            review, removal_confirmed=removal_confirmed, started_monotonic=started_monotonic
        )
        return

    bounded_raw, fenced_transcript = _fence_and_bound_transcript(raw_output)
    try:
        summary, findings, verdict = _produce_review_summary(job, fenced_transcript)
    except (GatewayError, ImproperlyConfigured):
        logger.exception("Harness review %s summarization failed", review_id)
        _finalize_review_unavailable(
            review,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            raw_transcript=bounded_raw,
        )
        return

    if verdict not in (HarnessReview.Verdict.APPROVED, HarnessReview.Verdict.CHANGES_REQUESTED):
        _finalize_review_unavailable(
            review,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
            raw_transcript=bounded_raw,
        )
        return

    _finalize_review_success(
        review,
        summary=summary,
        findings=findings,
        verdict=verdict,
        raw_transcript=bounded_raw,
        removal_confirmed=removal_confirmed,
        started_monotonic=started_monotonic,
    )

    # Advance/fail the implementation job based on the now-durable verdict
    # (Codex-review-hardened service functions -- both already re-check
    # the job's live status before acting, so a job cancelled while
    # review was running is a safe no-op here, not a special case this
    # module needs to detect itself).
    from harness.services import (
        HarnessInvalidTransition,
        fail_implementation_job_for_review,
        request_harness_push,
    )

    review.refresh_from_db()
    if verdict == HarnessReview.Verdict.APPROVED:
        try:
            request_harness_push(job=job, review=review)
        except HarnessInvalidTransition:
            logger.info(
                "Harness implementation job %s no longer awaiting review completion "
                "(review %s approved, but the job's status has since moved on)",
                job.pk,
                review.pk,
            )
    else:
        fail_implementation_job_for_review(job=job, review=review)


def _finalize_review_success(
    review, *, summary, findings, verdict, raw_transcript, removal_confirmed, started_monotonic
):
    latency_ms = int((time.monotonic() - started_monotonic) * 1000)
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review.pk, status=HarnessReview.Status.RUNNING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessReview.Status.SUCCEEDED
        locked.verdict = verdict
        locked.summary = summary
        locked.findings = findings
        locked.raw_transcript = raw_transcript
        if removal_confirmed:
            locked.container_id = ""
        locked.elapsed_seconds = max(0, int(latency_ms / 1000))
        locked.save(
            update_fields=[
                "status",
                "verdict",
                "summary",
                "findings",
                "raw_transcript",
                "container_id",
                "elapsed_seconds",
                "updated_at",
            ]
        )
        record_external_usage(
            workspace=locked.workspace,
            purpose="harness.review",
            backend=f"harness_cli:{locked.agent}",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=latency_ms,
            success=True,
            actor=locked.implementation_job.created_by,
            error_type="",
            # Codex CLI's read-only review invocation does not currently
            # request structured `--output-format json`-shaped cost
            # reporting the way Claude Code's discovery/implementation
            # invocations do (brief §boundary is silent on this --
            # verifying and wiring Codex CLI's own cost-reporting flag is
            # explicitly out of scope for this branch's review pipeline,
            # tracked as a follow-up rather than guessed at). Reviews
            # always record zero/unavailable cost for now; this is a
            # read-only pass with no write/push authority riding on it,
            # unlike the implementation phase's own budget enforcement.
            context_report={"usage_source": "unavailable"},
        )
    record_audit(
        action="harness.review_succeeded",
        actor=locked.implementation_job.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={"verdict": verdict},
    )


def _finalize_review_unavailable(
    review, *, removal_confirmed, started_monotonic, raw_transcript=""
):
    latency_ms = int((time.monotonic() - started_monotonic) * 1000)
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review.pk, status=HarnessReview.Status.RUNNING)
            .first()
        )
        if locked is None:
            return
        locked.status = HarnessReview.Status.FAILED
        locked.raw_transcript = raw_transcript
        if removal_confirmed:
            locked.container_id = ""
        locked.elapsed_seconds = max(0, int(latency_ms / 1000))
        locked.save(
            update_fields=[
                "status",
                "raw_transcript",
                "container_id",
                "elapsed_seconds",
                "updated_at",
            ]
        )
        record_external_usage(
            workspace=locked.workspace,
            purpose="harness.review",
            backend=f"harness_cli:{locked.agent}",
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=latency_ms,
            success=False,
            actor=locked.implementation_job.created_by,
            error_type="review_unavailable",
            context_report={"usage_source": "unavailable"},
        )
    record_audit(
        action="harness.review_failed",
        actor=locked.implementation_job.created_by,
        workspace=locked.workspace,
        resource=locked,
        metadata={},
    )
    from harness.services import fail_implementation_job_for_review

    fail_implementation_job_for_review(
        job=review.implementation_job,
        review=review,
        failure_reason=HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE,
    )


def resolve_running_review_after_unexpected_error(review_id):
    """Called by `harness.tasks.run_harness_review_job`'s outer safety
    net when `run_harness_review_job` above is interrupted by
    `SoftTimeLimitExceeded`, or any other unexpected exception NOT
    already caught by this module's own internal `runner.RunnerError`/
    `GatewayError`/`ImproperlyConfigured` handling (a Docker control
    timeout, a DB error mid-finalize, a genuine bug) -- without this, a
    review `_claim_review` already committed to `running` would stay
    stuck that way with no periodic recovery scan yet to reclaim it
    (Codex code-review P2, branch 3 round 2; mirrors `harness.execution.
    resolve_running_job_after_unexpected_error`'s identical shape and
    rationale, one job type over). Defensively re-attempts container
    teardown using the row's own `container_id`. A safe no-op if the
    review is no longer `running`."""
    review = HarnessReview.objects.filter(pk=review_id, status=HarnessReview.Status.RUNNING).first()
    if review is None:
        return
    removal_confirmed = True
    if review.container_id:
        _teardown_container(review.container_id)
        removal_confirmed = _safe_remove_container(review.container_id)
    _finalize_review_unavailable(
        review, removal_confirmed=removal_confirmed, started_monotonic=time.monotonic()
    )
