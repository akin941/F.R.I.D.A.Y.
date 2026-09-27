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
import os
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
from harness import runner
from harness.execution import _resolve_cli_env, redact_harness_secrets
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
    review = create_pending_review(job, select_review_agent(job.agent))
    _dispatch_review_task(review.id)
    return review


def create_pending_review(job, agent):
    """Creates the `pending` review row only. Phase 3 branch 5 (plan-review
    round 9 item 4): `run_harness_implementation_job` calls this inside the
    same transaction as the job's `pending_review` transition, then
    `dispatch_created_review` after commit -- a job can no longer sit in
    `pending_review` with no review row that could ever resolve it."""
    return HarnessReview.objects.create(
        workspace=job.workspace,
        implementation_job=job,
        agent=agent,
        status=HarnessReview.Status.PENDING,
    )


def dispatch_created_review(review):
    """Publishes the task for a review created by `create_pending_review`
    -- same fail-closed first-dispatch semantics as
    `dispatch_harness_review`."""
    _dispatch_review_task(review.id)


def _dispatch_review_task(review_id):
    """Original dispatch, called once at review creation. A broker
    failure here fails the review (and its job) closed immediately
    (Codex code-review P2, branch 3 round 2) -- correct at THIS call
    site specifically: this is the review's very first, only-ever
    scheduled attempt, so there is no reason to make a curator wait out
    a full recovery-scan interval to learn a freshly-created review
    never even got published. `recover_stale_harness_reviews` (Phase 3
    branch 4) exists now, but it re-dispatches a genuinely LOST publish
    via `_redispatch_review_task` below instead of this function --
    reusing this fail-closed behavior for a recovery-scan RETRY would
    convert a transient broker outage into a permanent
    `review_unavailable` on the very first scan pass during that outage
    (Codex code-review P2, branch 4 round 1)."""
    from harness.tasks import run_harness_review_job

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_harness_review_job.delay(str(review_id))
        return
    try:
        run_harness_review_job.delay(str(review_id))
    except Exception:
        logger.exception("Failed to publish harness review %s to the broker", review_id)
        resolve_pending_review_after_dispatch_failure(review_id)


def _redispatch_review_task(review_id):
    """Recovery-scan-only redispatch (Phase 3 branch 4, Codex
    code-review P2, round 1): unlike `_dispatch_review_task` above, a
    publish failure here is simply logged and left for the NEXT scan
    pass to retry -- mirrors `dispatch_harness_implement_execution`/
    `dispatch_harness_push_execution`'s identical swallow-and-log shape
    for their own recovery-scan redispatch cases. See
    `_dispatch_review_task`'s own docstring for why the two functions
    deliberately differ here despite calling the same underlying task."""
    from harness.tasks import run_harness_review_job

    try:
        run_harness_review_job.delay(str(review_id))
    except Exception:
        logger.exception("Failed to re-publish harness review %s to the broker", review_id)


def resolve_pending_review_after_dispatch_failure(review_id):
    """See `_dispatch_review_task`'s call site above for the full
    rationale. Fenced on `status=pending` -- the review was just created
    and never claimed, so there is no container/worktree to tear down,
    only the row's own status and the implementation job's.

    The review's own terminal write and the implementation job's
    consequent failure run inside ONE transaction (Codex code-review P2,
    branch 3 round 9: an earlier version committed the review as
    `failed` first, then failed the job as a SEPARATE, later
    transaction -- a crash in the gap between the two left a terminal,
    already-failed review with the job still stuck `pending_review`
    forever, invisible to every other recovery path since
    `resolve_running_review_after_unexpected_error` only ever looks at
    reviews still `running`. Mirrors round 3's identical combined-
    transaction fix for `run_harness_review_job`'s own finalize call
    sites, applied here too)."""
    from harness.services import fail_implementation_job_for_review

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
        fail_implementation_job_for_review(
            job=locked.implementation_job,
            review=locked,
            failure_reason=HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE,
        )


def _claim_review(review_id):
    """Fenced claim: `pending` -> `running`. Mirrors
    `harness.execution._claim_job`'s exact shape, extended with a check
    on the implementation job's own live status (Codex code-review P2,
    branch 3 round 5): a curator can cancel the job (`pending_review` ->
    `cancelled`, worktree cleaned up immediately --
    `cancel_harness_implementation_job`) after this review row was
    already created but before this task claims it. Claiming based on
    the review row alone let a review still launch and spend review-CLI
    credentials bundling/reviewing a job no longer awaiting review (a
    wasted-cost/availability issue, not a security bypass --
    `request_harness_push`/`fail_implementation_job_for_review` both
    re-check the job's live status before acting on the verdict either
    way). Locks the job row too and refuses to claim -- failing the
    review closed instead, since nothing else will ever resolve a
    `pending` review whose job already moved on -- unless it is still
    `pending_review`."""
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review_id, status=HarnessReview.Status.PENDING)
            .first()
        )
        if locked is None:
            return None
        job_still_awaits_review = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(
                pk=locked.implementation_job_id,
                status=HarnessImplementationJob.Status.PENDING_REVIEW,
            )
            .exists()
        )
        if not job_still_awaits_review:
            locked.status = HarnessReview.Status.FAILED
            locked.save(update_fields=["status", "updated_at"])
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
    """Codex code-review P2, branch 3 round 4: a `docker rm` failure here
    leaks a stopped container -- still carrying the review CLI's own
    credentials (e.g. `CODEX_API_KEY`) in its environment -- indefinitely
    if nothing ever retries the removal. `recover_stale_harness_reviews`
    (Phase 3 branch 4) is that retry: its own leaked-terminal-container
    case periodically re-attempts removal via this exact function, the
    same shape `HarnessJob`'s analogous case already has."""
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


def _review_no_longer_wanted(job_id):
    """The implementation job left `pending_review` (cancelled, or failed
    by a recovery sweep) or has a cancel requested -- the review's verdict
    can no longer matter."""
    return not HarnessImplementationJob.objects.filter(
        pk=job_id,
        status=HarnessImplementationJob.Status.PENDING_REVIEW,
        cancel_requested=False,
    ).exists()


def _wait_for_review_container(container_id, started_monotonic, job_id=None):
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
        # Phase 3 branch 5 (2026-09-27 audit): cancelling the job used to
        # leave an already-launched review running -- reading agent-authored
        # code with the review CLI's credentials -- until it finished or hit
        # the time brake. It is torn down as soon as the job stops waiting.
        if job_id is not None and _review_no_longer_wanted(job_id):
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
    exact commit `prepare_review_clone` verified and checked out.

    `--text --no-ext-diff --no-textconv` (Codex code-review P1, branch 3
    round 3): round 1's clean-clone fix closes CONFIG-driven diff
    manipulation (`diff.external`/`textconv`, which need a matching
    `.git/config` entry the clean clone never has), but a bare `git diff`
    is still governed by the reviewed commit's own TRACKED
    `.gitattributes` -- a committed `*.py -diff` line makes git treat
    matching files as binary for diff purposes with zero config needed
    at all, printing `Binary files ... differ` instead of the real
    changed lines, letting an implementation hide exactly the content
    under review while still being eligible for an "approved" verdict.
    `--text` unconditionally overrides that binary/`-diff` classification
    (verified empirically: without it, a `-diff`-marked file's real
    changes never appear in the diff at all; with it, they do,
    regardless of what the file's own attributes claim) -- `--no-ext-diff`/
    `--no-textconv` are additional defense-in-depth against the
    config-driven mechanisms round 1 already neutralizes structurally."""
    return (
        f"Review the code changes committed on this branch. The stated goal of the "
        f"change was: {job.goal}\n\n"
        f"Run `git diff --text --no-ext-diff --no-textconv {job.base_commit_sha}..HEAD` to "
        f"see exactly what changed (always use these exact flags -- do not run a bare "
        f"`git diff`, since a committed `.gitattributes` could otherwise hide real changes "
        f"behind a binary-file placeholder), then review it for correctness, safety, and "
        f"whether it actually accomplishes the stated goal. Conclude with a clear verdict: "
        f"approve the change, or request changes and explain what's wrong."
    )


def _fence_and_bound_transcript(raw_output):
    """Bounding/fencing discipline mirrors `harness.execution.
    _fence_and_bound_transcript`'s (see that function's docstring for the
    shared rationale), but the truncation SHAPE deliberately diverges
    (Codex code-review P2, branch 3 round 5): discovery's own transcript
    front-loads the context that matters, so a head-only cut is fine
    there, but `_build_review_goal` asks the review CLI to conclude with
    its verdict at the END of the transcript -- a head-only cut silently
    drops that verdict for any review whose transcript exceeds the
    budget, making the gateway see an inconclusive transcript and fail an
    otherwise-valid review as `review_unavailable`. Keeps a head+tail
    window instead so the opening context AND the trailing verdict both
    survive."""
    bounded_raw = redact_harness_secrets(raw_output)
    if len(bounded_raw) > _RAW_TRANSCRIPT_MAX_CHARS:
        head_chars = _RAW_TRANSCRIPT_MAX_CHARS // 2
        tail_chars = _RAW_TRANSCRIPT_MAX_CHARS - head_chars - 1
        bounded_raw = bounded_raw[:head_chars] + "…" + bounded_raw[-tail_chars:]
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
    # Codex code-review P2, branch 3 round 14: a malformed `findings`
    # value (a string/object instead of a list -- model drift or a
    # prompt injection that talked the model out of the requested shape)
    # used to be silently replaced with `[]` while the surrounding
    # `summary`/`verdict` were still trusted, letting an approval proceed
    # despite the response not actually matching the schema this
    # function exists to enforce. Now fails the whole response closed
    # instead, same as an invalid `summary`/`verdict` already does.
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        return "", [], ""
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


# Codex CLI (and, if `_SELECTABLE_REVIEW_AGENTS` ever grows to include Claude
# Code as a reviewer, Claude Code too) walks from the checkout root down to
# its own working directory, honoring `AGENTS.override.md`/`AGENTS.md` (or
# `CLAUDE.md`) as trusted project-level instructions at EVERY directory level
# it passes through -- confirmed against Codex CLI's own documented discovery
# behavior. Since the review container's cwd IS the clean-clone checkout root
# and the checked-out commit is exactly the diff under review, an
# implementation commit that adds one of these files gets it read and obeyed
# by the reviewer BEFORE the actual review goal is ever applied -- letting
# the code under review instruct its own reviewer to approve regardless of
# content, or to suppress findings, defeating the entire point of a
# cross-vendor review gate.
_AGENT_INSTRUCTION_FILENAMES_LOWER = frozenset(
    name.lower() for name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
)


def _strip_agent_instruction_files(directory):
    """Removes every file matching a known agent-instruction-file
    convention from the clean clone's ENTIRE working tree (not just its
    root -- Codex CLI's own discovery walks every directory level between
    the checkout root and its cwd, and the checkout root and the CLI's cwd
    are the same directory here, but a subdirectory-rooted fallback name
    could still be planted) before the review container is ever launched,
    so there is nothing left for it to discover regardless of where in the
    tree the implementation committed one.

    Matches filenames case-INSENSITIVELY (Codex code-review P1, branch 3
    round 7): a runner host with a case-insensitive filesystem (the macOS
    default) resolves a lookup for `AGENTS.md` to a committed `agents.md`
    just as readily as an exact-case match -- the review CLI would still
    discover and honor it even though an earlier exact-case comparison
    left it in the checkout untouched.

    Raises `runner.RunnerError` if any matching file can't be removed -- a
    review that might still be running with attacker-supplied instructions
    in place must fail closed, not launch anyway on a best-effort basis."""
    for root, _dirs, files in os.walk(directory):
        for filename in files:
            if filename.lower() not in _AGENT_INSTRUCTION_FILENAMES_LOWER:
                continue
            path = os.path.join(root, filename)
            try:
                os.remove(path)
            except OSError as exc:
                raise runner.RunnerError(
                    f"Failed to remove agent-instruction file {path} from review checkout"
                ) from exc


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
        # Codex code-review P1, branch 3 round 4: strip agent-instruction
        # files (AGENTS.md/AGENTS.override.md/CLAUDE.md) from the clean
        # clone before the review container ever sees them -- see
        # `_strip_agent_instruction_files`'s own docstring for the full
        # attack this closes.
        _strip_agent_instruction_files(clean_clone_dir)
        image = runner.image_for_agent(review.agent)
        cli_env = _resolve_cli_env(review.agent)
        runner.launch_container(
            image=image,
            clone_dir=clean_clone_dir,
            goal=_build_review_goal(job),
            cli_env=cli_env,
            name=container_name,
        )
        status, raw_output = _wait_for_review_container(
            container_name, started_monotonic, job_id=job.pk
        )
    except runner.RunnerError:
        logger.exception("Harness review %s failed to launch", review_id)
    finally:
        removal_confirmed = _safe_remove_container(container_name)
        if bundle_dir:
            _safe_cleanup_directory(bundle_dir)
        if clean_clone_dir:
            _safe_cleanup_directory(clean_clone_dir)

    # Codex code-review P1, branch 3 round 3: every path below this point
    # wraps BOTH the review's own terminal-state write AND the
    # implementation job's consequent transition in ONE outer
    # `transaction.atomic()` -- an earlier version committed the review
    # as `succeeded`/`failed` first, then advanced/failed the job as a
    # SEPARATE, later transaction. A worker crash (or any exception) in
    # the gap between the two left a terminal, already-decided review
    # with an implementation job still stuck `pending_review` forever --
    # `resolve_running_review_after_unexpected_error`'s own rescue only
    # ever looks at reviews still `running`, so a review that had already
    # committed as terminal was invisible to it. Nesting these calls
    # inside one outer atomic block means either BOTH writes land, or
    # (if anything raises) NEITHER does -- a crash mid-way leaves the
    # review still `running`, which the unexpected-error resolver DOES
    # correctly catch and retry.
    if status != "succeeded" or not raw_output:
        # Codex code-review P2, branch 3 round 8: a container that timed
        # out or exited non-zero can still have emitted real logs before
        # failing (`_wait_for_review_container` returns them as
        # `raw_output` either way) -- an earlier version discarded them
        # here, leaving `raw_transcript` empty for the operator on common
        # `review_unavailable` causes like a missing `CODEX_API_KEY` or a
        # mid-run CLI crash, even though the diagnostic was captured.
        bounded_raw = _fence_and_bound_transcript(raw_output)[0] if raw_output else ""
        with transaction.atomic():
            _finalize_review_unavailable(
                review,
                removal_confirmed=removal_confirmed,
                started_monotonic=started_monotonic,
                raw_transcript=bounded_raw,
            )
        return

    bounded_raw, fenced_transcript = _fence_and_bound_transcript(raw_output)
    # Persisted BEFORE the gateway call, not after (Codex code-review P2,
    # branch 3 round 13, mirrors harness.execution.run_harness_job's
    # identical round-5 fix): if a Celery soft time limit or worker crash
    # interrupts `_produce_review_summary` below, execution never reaches
    # any of this function's own finalize call sites, and
    # `resolve_running_review_after_unexpected_error`'s rescue only has
    # this DB row to work from -- without this early write, the only
    # captured reviewer transcript for exactly the slow/timeout cases an
    # operator most needs to diagnose would be lost.
    HarnessReview.objects.filter(pk=review.pk).update(raw_transcript=bounded_raw)
    try:
        summary, findings, verdict = _produce_review_summary(job, fenced_transcript)
    except (GatewayError, ImproperlyConfigured):
        logger.exception("Harness review %s summarization failed", review_id)
        with transaction.atomic():
            _finalize_review_unavailable(
                review,
                removal_confirmed=removal_confirmed,
                started_monotonic=started_monotonic,
                raw_transcript=bounded_raw,
            )
        return

    if verdict not in (HarnessReview.Verdict.APPROVED, HarnessReview.Verdict.CHANGES_REQUESTED):
        with transaction.atomic():
            _finalize_review_unavailable(
                review,
                removal_confirmed=removal_confirmed,
                started_monotonic=started_monotonic,
                raw_transcript=bounded_raw,
            )
        return

    from harness.services import (
        HarnessInvalidTransition,
        fail_implementation_job_for_review,
        request_harness_push,
    )

    with transaction.atomic():
        _finalize_review_success(
            review,
            summary=summary,
            findings=findings,
            verdict=verdict,
            raw_transcript=bounded_raw,
            removal_confirmed=removal_confirmed,
            started_monotonic=started_monotonic,
        )
        review.refresh_from_db()
        # Advance/fail the implementation job based on the now-durable
        # verdict (Codex-review-hardened service functions -- both
        # already re-check the job's live status before acting, so a job
        # cancelled while review was running is a safe no-op here, not a
        # special case this module needs to detect itself).
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
    # Same combined-transaction discipline as `run_harness_review_job`'s
    # own calls (Codex code-review P1, branch 3 round 3) -- the review's
    # terminal write and the implementation job's consequent failure must
    # land together, or neither does.
    #
    # `raw_transcript=review.raw_transcript` (Codex code-review P2,
    # branch 3 round 13): `_finalize_review_unavailable`'s own default
    # (`""`) would otherwise CLOBBER the transcript `run_harness_review_
    # job` may have already persisted before the interruption that
    # brought execution here -- passing the row's own freshly-read
    # current value through preserves it, defaulting naturally to `""`
    # when nothing was ever written.
    with transaction.atomic():
        _finalize_review_unavailable(
            review,
            removal_confirmed=removal_confirmed,
            started_monotonic=time.monotonic(),
            raw_transcript=review.raw_transcript,
        )


_REVIEW_TERMINAL_STATUSES = (
    HarnessReview.Status.SUCCEEDED,
    HarnessReview.Status.FAILED,
)


def recover_stale_harness_reviews():
    """Periodic scan (Phase 3 branch 4), mirroring `harness.execution.
    recover_stale_harness_jobs`'s three-case shape for `HarnessReview`.
    The synchronous rescues branch 3 already added
    (`resolve_pending_review_after_dispatch_failure` on a `.delay()` call
    that itself raises; `resolve_running_review_after_unexpected_error`
    on a Python-level exception/soft-time-limit inside the task) only
    cover interruptions the WORKER process itself observes and reacts
    to. Neither helps a review whose task message was silently dropped
    by the broker after a successful publish, or a HARD kill (OOM,
    SIGKILL) that leaves nothing to run any exception handler at all --
    this scan is the backstop for exactly those two cases, plus the
    leaked-container sweep round 4 explicitly deferred here.

    1. `pending` past `HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS` since
       creation -- the message was lost after a successful `.delay()`
       (distinct from `.delay()` itself raising, already handled
       synchronously) -- simply re-dispatched; `_claim_review`'s own
       `status=pending` fence makes this safe even if the original
       dispatch actually does arrive later.
    2. `running` past `HARNESS_MAX_JOB_SECONDS` +
       `HARNESS_RECOVERY_GRACE_SECONDS` since `started_at` -- a hard
       kill left nothing to call `resolve_running_review_after_
       unexpected_error` itself; called here instead, which already
       tears down any leftover container and fails the review (and its
       job) closed.
    3. Already terminal (`succeeded`/`failed`) but still carrying a
       non-empty `container_id`, past
       `HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS` since its last
       update -- `_safe_remove_container`'s own removal genuinely failed
       at finalize time; retried here, same as discovery's identical
       case 3."""
    dispatch_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS
    )
    stale_pending_ids = list(
        HarnessReview.objects.filter(
            status=HarnessReview.Status.PENDING, created_at__lt=dispatch_cutoff
        ).values_list("pk", flat=True)
    )
    for review_id in stale_pending_ids:
        _redispatch_review_task(review_id)

    running_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_MAX_JOB_SECONDS + settings.HARNESS_RECOVERY_GRACE_SECONDS
    )
    stale_running_ids = list(
        HarnessReview.objects.filter(
            status=HarnessReview.Status.RUNNING, started_at__lt=running_cutoff
        ).values_list("pk", flat=True)
    )
    for review_id in stale_running_ids:
        resolve_running_review_after_unexpected_error(review_id)

    leaked_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS
    )
    leaked_ids = list(
        HarnessReview.objects.filter(
            status__in=_REVIEW_TERMINAL_STATUSES, updated_at__lt=leaked_cutoff
        )
        .exclude(container_id="")
        .values_list("pk", flat=True)
    )
    for review_id in leaked_ids:
        _sweep_leaked_terminal_review_container(review_id)


def _sweep_leaked_terminal_review_container(review_id):
    """One iteration of case 3 above. Re-fetches under lock first
    (mirrors `harness.execution._sweep_leaked_terminal_container`'s
    identical discipline) so two overlapping scans don't both attempt
    removal of the same container -- the Docker call itself happens
    OUTSIDE the lock, same as every other recovery case in this
    module."""
    with transaction.atomic():
        locked = (
            HarnessReview.objects.select_for_update(of=("self",))
            .filter(pk=review_id, status__in=_REVIEW_TERMINAL_STATUSES)
            .exclude(container_id="")
            .first()
        )
        if locked is None:
            return False
        container_id = locked.container_id
    if _safe_remove_container(container_id):
        HarnessReview.objects.filter(pk=review_id, container_id=container_id).update(
            container_id=""
        )
        return True
    return False
