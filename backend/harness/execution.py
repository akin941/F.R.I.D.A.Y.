"""Harness job execution engine (Phase 2 branch 2, brief §2). Celery-agnostic
-- `harness.tasks` is a thin wrapper so this can be unit-tested without a
broker/worker, mirroring `agents.execution`/`agents.tasks`'s split.

One job, one container, read-only. Brakes (brief §2): runtime timeout
(`HARNESS_MAX_JOB_SECONDS`, enforced by the wait loop below and backed up
by the Celery task's own `soft_time_limit`), cost ceiling
(`HARNESS_MAX_JOB_COST` -- necessarily a post-hoc check: an external CLI's
cost is only known once it reports it at the end of a single-call job,
unlike `agents`' incrementing per-step `gateway_calls_used`; exceeding it
flags the job `budget_exceeded` for review, it cannot cancel spend that
already happened), kill switch (`HarnessJob.cancel_requested`, polled by
the wait loop, actual container teardown always run by the worker that
owns the container -- never by the request that set the flag), recovery
scan (`recover_stale_harness_jobs`, container-keyed via `container_id`).
"""

import json
import logging
import os
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway, record_external_usage
from ai_core.models import PromptTemplate
from audit.services import record_audit
from harness import container_lifecycle, ingestion, runner
from harness.models import HarnessJob
from harness.services import dispatch_harness_job
from memory.context import neutralize_fence, strip_invisible_formatting
from memory.services import DuplicateMemory, record_work_log

logger = logging.getLogger(__name__)

_HARNESS_BRIEFING_PROMPT_NAME = "friday_harness_briefing"
# Bounded at write time (brief §3, mirrors the firm-dossier block's
# bounded-line discipline, Phase 1 branch 3) -- a CLI transcript is
# untrusted, potentially-unbounded text; storing it with no cap is a real
# exposure surface, not a formatting nicety.
_RAW_TRANSCRIPT_MAX_CHARS = 8000
_WORK_LOG_TITLE_MAX_CHARS = 200
# The gateway's own reply is still untrusted content -- a prompt injection
# could persuade it to echo the transcript back inside an otherwise
# validly-shaped summary/findings payload (Codex code-review P2, branch 3
# round 3) -- so it gets the same write-time bounding as raw_transcript
# before it can reach an auto-approved work_log or a future embedding call.
_BRIEFING_SUMMARY_MAX_CHARS = 2000
_BRIEFING_FINDINGS_MAX_COUNT = 20
_BRIEFING_FINDING_MAX_CHARS = 500


def _claim_job(job_id):
    """Fenced claim: `pending` -> `running`. Returns the locked row, or
    `None` if the job can no longer be claimed (already cancelled between
    enqueue and claim -- `cancel_harness_job`'s pending-state transition,
    brief Codex round-4 fix -- or somehow already claimed/terminal)."""
    with transaction.atomic():
        locked = HarnessJob.objects.select_for_update(of=("self",)).filter(pk=job_id).first()
        if locked is None or locked.status != HarnessJob.Status.PENDING:
            return None
        locked.status = HarnessJob.Status.RUNNING
        locked.started_at = timezone.now()
        locked.save(update_fields=["status", "started_at", "updated_at"])
        return locked


def _resolve_cli_env(agent):
    """The CLI's own auth material (O-2), resolved from env -- never the
    worker's full inherited environment (that would hand the container
    every ambient secret, not just the one the CLI needs), never the git
    clone credential (which stays host-side, see `runner.clone_repo`).

    `CODEX_API_KEY` (Phase 3 branch 3, brief "Open question resolved:
    Codex CLI vendor terms" -- GO, API-key auth only): NEVER a stored
    ChatGPT session/OAuth token, and never read from whatever interactive
    `codex login` state happens to already be on the runner host -- that
    auth path is the one OpenAI's own maintainers left genuinely
    unresolved for automated use (see the brief's writeup), so this
    resolver simply never produces it. Compared by string value
    (`HarnessJob.Agent.CODEX_CLI`, not `HarnessReview.Agent.CODEX_CLI` --
    both are the same `"codex_cli"` string; importing `HarnessReview`
    here just to re-spell an equivalent comparison would be a pointless
    cross-model coupling)."""
    if agent == HarnessJob.Agent.CLAUDE_CODE:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return {"ANTHROPIC_API_KEY": api_key} if api_key else {}
    if agent == HarnessJob.Agent.CODEX_CLI:
        api_key = os.environ.get("CODEX_API_KEY", "")
        return {"CODEX_API_KEY": api_key} if api_key else {}
    return {}


# Every credential a harness container can hold in its environment (the
# CLI keys resolved by `_resolve_cli_env`). Phase 3 branch 6 (review P2):
# CLI output is agent-influenced -- a goal or repo content can make the
# agent print its own key -- so every stored/gateway-bound transcript is
# scrubbed of these literal values first.
_HARNESS_SECRET_ENV_NAMES = ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY")
_MIN_REDACTABLE_SECRET_LENGTH = 12
REDACTED_SECRET = "[REDACTED]"


def harness_secret_values():
    return [
        value
        for name in _HARNESS_SECRET_ENV_NAMES
        if len(value := os.environ.get(name, "")) >= _MIN_REDACTABLE_SECRET_LENGTH
    ]


def redact_harness_secrets(text):
    """Replaces every known harness credential value in `text`. Literal
    matching only (an encoded key isn't caught) -- the scrub is defense in
    depth on top of never logging credentials anywhere else."""
    if not text:
        return text
    for value in harness_secret_values():
        text = text.replace(value, REDACTED_SECRET)
    return text


def _parse_claude_code_output(raw_output):
    """Best-effort parse of Claude Code's `--output-format json` result
    (brief: "reports a total_cost_usd/usage block in its result object").
    Falls back to zero-usage + `"usage_source": "unavailable"` (the
    brief's own stated fallback) for any shape this doesn't recognize --
    never fabricates a token/cost estimate.

    Uses `json.JSONDecoder().raw_decode()`, not `json.loads()` (Codex
    code-review P1, branch 2 round 5): discovery's own captured logs are
    always exactly one clean JSON document, but Phase 3's implementation
    containers run a SEPARATE test command after the CLI exits, and
    `capture_logs` returns the whole container stdout -- CLI JSON
    followed by whatever the test command itself printed. `json.loads`
    rejects any trailing non-whitespace content as a parse error, falling
    through to the zero-cost `unavailable` case -- which silently made
    `harness.implementation_execution`'s own cost brake (branch 2 round
    2) treat every real, over-budget run as free, the exact bypass this
    fix closes. `raw_decode` parses just the JSON prefix and ignores
    whatever valid or invalid text follows it, which is exactly right
    here since the CLI always runs, and finishes printing its own JSON,
    strictly before the test command ever starts."""
    unavailable = (0, 0, Decimal("0"), {"usage_source": "unavailable"})
    try:
        # `raw_decode` does not skip leading whitespace on its own (unlike
        # `json.loads`) -- `lstrip()` first so a leading blank line never
        # turns a genuinely well-formed prefix into a parse failure.
        parsed, _ = json.JSONDecoder().raw_decode(raw_output.lstrip())
    except (ValueError, TypeError):
        return unavailable
    if not isinstance(parsed, dict):
        return unavailable
    usage = parsed.get("usage")
    cost = parsed.get("total_cost_usd")
    if not isinstance(usage, dict) or cost is None:
        return unavailable
    try:
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        estimated_cost = Decimal(str(cost))
    except (TypeError, ValueError, InvalidOperation):
        return unavailable
    return input_tokens, output_tokens, estimated_cost, {"usage_source": "cli_reported"}


def _harness_briefing_prompt_version():
    """Mirrors `agents.execution._agent_read_step_prompt_version`'s
    identical shape exactly -- a missing/misconfigured prompt template is
    a hard failure, not a silent degrade, since routing summarization
    through Friday's own gateway call (rather than the CLI's own
    self-summary) is the actual R2/prompt-injection mitigation this
    branch exists to provide (brief §3), not an optional nicety."""
    try:
        template = PromptTemplate.objects.get(name=_HARNESS_BRIEFING_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_HARNESS_BRIEFING_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_HARNESS_BRIEFING_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _fence_and_bound_transcript(raw_output):
    """Bounds the CLI's own output to a fixed character cap at write time
    (brief §3) so an unbounded transcript is never stored or sent to the
    gateway wholesale, THEN derives a separately neutralized copy for
    prompt use only (Codex code-review P3, branch 3 round 3: the earlier
    version stored the fenced/neutralized copy as `raw_transcript` itself,
    so a transcript containing a fence-like tag would be persisted
    mutated -- no longer the actual CLI output the field exists to
    retain for audit/debugging). Returns `(bounded_raw, fenced_for_prompt)`
    -- `bounded_raw` goes into `HarnessJob.briefing["raw_transcript"]`
    unmutated (bounding is not a security measure, just a storage cap);
    `fenced_for_prompt` is the untrusted, low-trust text (brief §3, R2
    mitigation) run through `neutralize_fence`/`strip_invisible_formatting`
    (`memory/context.py:341,359`) so it can never close/open a tag and
    smuggle text into the privileged part of the gateway prompt -- it is
    used ONLY to build the prompt, never persisted."""
    bounded_raw = redact_harness_secrets(raw_output)
    if len(bounded_raw) > _RAW_TRANSCRIPT_MAX_CHARS:
        bounded_raw = bounded_raw[: _RAW_TRANSCRIPT_MAX_CHARS - 1] + "…"
    fenced_for_prompt = neutralize_fence(strip_invisible_formatting(bounded_raw), "repo_transcript")
    return bounded_raw, fenced_for_prompt


def _parse_briefing_response(text):
    """Strict parse of the gateway's own JSON response. A malformed/
    non-JSON/wrong-shape reply is treated as NO summary (empty string,
    never the raw response text) -- Codex code-review P1, branch 3 round
    2: the fenced `<repo_transcript>` block is still attacker-influenced
    content (that's the whole reason it's fenced), so a prompt injection
    that talks the model out of returning the requested JSON shape must
    not fall through to having its raw reply trusted as the summary --
    that would smuggle exactly the untrusted content straight into an
    auto-approved work_log, defeating the one guarantee this gateway hop
    exists to provide. An empty summary makes `_finalize`'s `briefing.get
    ("summary")` check falsy, so no work_log is produced for a malformed
    response.

    Even a well-shaped response is bounded before being trusted (Codex
    code-review P2, branch 3 round 3): the gateway's own output is still
    untrusted content -- a prompt injection could persuade it to echo the
    transcript back inside a validly-shaped `summary`/`findings` JSON
    payload -- so both are capped the same way `raw_transcript` already
    is, before this content can ever reach an auto-approved work_log or
    a future embedding call."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return "", []
    if not isinstance(parsed, dict):
        return "", []
    summary = parsed.get("summary")
    findings = parsed.get("findings")
    if not isinstance(summary, str):
        return "", []
    if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
        findings = []
    if len(summary) > _BRIEFING_SUMMARY_MAX_CHARS:
        summary = summary[: _BRIEFING_SUMMARY_MAX_CHARS - 1] + "…"
    bounded_findings = []
    for item in findings[:_BRIEFING_FINDINGS_MAX_COUNT]:
        if len(item) > _BRIEFING_FINDING_MAX_CHARS:
            item = item[: _BRIEFING_FINDING_MAX_CHARS - 1] + "…"
        bounded_findings.append(item)
    return summary, bounded_findings


def _produce_briefing_summary(job, fenced_transcript):
    """The R2 mitigation (brief §3): `summary`/`findings` are produced by
    an explicit, in-process `InferenceGateway.complete()` call over the
    fenced transcript -- never taken as-is from the CLI agent's own
    self-summary, since the CLI's own opinion of "what's fact vs. what's
    recommendation" is not a boundary Friday controls or can verify.
    Returns `(summary, findings)`. Raises `GatewayError`/
    `ImproperlyConfigured` on failure -- the caller decides what a failed
    summarization attempt means for the job's own briefing (Codex
    code-review P2, branch 3 round 4: the raw transcript itself must
    still be stored even when this fails, so bounding/fencing the
    transcript happens in the caller, not in here, and can never be
    skipped just because summarization couldn't run)."""
    prompt_version = _harness_briefing_prompt_version()
    messages = [
        {"role": "system", "content": prompt_version.content},
        {
            "role": "user",
            "content": f"<repo_transcript>\n{fenced_transcript}\n</repo_transcript>",
        },
    ]
    gateway = InferenceGateway()
    result = gateway.complete(
        purpose="harness.briefing",
        messages=messages,
        actor=job.created_by,
        workspace=job.workspace,
        prompt_version=prompt_version,
        stream=False,
    )
    return _parse_briefing_response(result.text)


def _teardown_container(container_id):
    """Best-effort stop/kill (`container_lifecycle.teardown_container`).
    Kept as this module's own seam: the wait loop and the recovery paths
    below call it by this name."""
    container_lifecycle.teardown_container(container_id, label="harness container")


# `container_lifecycle.wait_for_container` outcome -> this job's own
# `(status, failure_reason)` -- unchanged from the pre-extraction loop.
_WAIT_OUTCOME_STATUS = {
    container_lifecycle.WaitResult.TIMED_OUT: ("failed", HarnessJob.FailureReason.TIMEOUT),
    container_lifecycle.WaitResult.STOPPED: ("cancelled", HarnessJob.FailureReason.KILLED),
    container_lifecycle.WaitResult.EXITED_WITH_ERROR: (
        "failed",
        HarnessJob.FailureReason.RUNNER_ERROR,
    ),
    container_lifecycle.WaitResult.EXITED_CLEANLY: ("succeeded", ""),
}


def _wait_for_container(job_id, container_id, started_monotonic):
    """Non-blocking wait loop around the **whole** container lifetime
    (Codex round-4 fix): polls `cancel_requested` and the runtime brake
    every `HARNESS_CANCEL_POLL_SECONDS`, not just once before/after the
    invocation -- a cancel during the one long phase (the CLI's own run)
    must be observed before the container exits on its own, matching the
    branch's own mid-run kill-switch test.

    The loop itself is `container_lifecycle.wait_for_container` (Phase 5
    D12): the transcript is captured on every outcome, and
    `cancel_requested` is re-checked once more after the container stops
    running (Codex code-review P1, branch 2 round 3) -- a cancel arriving
    in the gap between the loop's last "still running" check and the
    container's own natural exit would otherwise be silently dropped.
    Only a confirmed `0` exit code is a success; `None` fails closed as
    `runner_error` (Codex round-2 P2)."""
    outcome = container_lifecycle.wait_for_container(
        container_id,
        started_monotonic=started_monotonic,
        teardown=_teardown_container,
        should_stop=lambda: HarnessJob.objects.filter(pk=job_id, cancel_requested=True).exists(),
        recheck_stop_after_exit=True,
        capture_transcript=True,
    )
    status, failure_reason = _WAIT_OUTCOME_STATUS[outcome.result]
    return status, failure_reason, outcome.transcript


def run_harness_job(job_id):
    """Clone (read-only) -> launch container -> wait/poll -> capture ->
    teardown -> record usage -> finalize. Every exit path (success,
    failure, timeout, cancellation, launch error) removes the container
    and the ephemeral clone directory -- no leftovers on the runner disk.
    """
    job = _claim_job(job_id)
    if job is None:
        return

    # A deterministic name, persisted BEFORE the container is ever
    # launched (Codex code-review P1, branch 2 round 3): if the worker
    # crashes in the window between `docker run` succeeding and this row
    # being updated, the recovery scan would otherwise have no handle at
    # all to find the still-running container -- exactly the crash case
    # the recovery brake exists to cover. Docker addresses a container by
    # name interchangeably with its actual id, so committing this name up
    # front closes the window entirely rather than narrowing it.
    container_name = f"harness-job-{job.pk}"
    HarnessJob.objects.filter(pk=job.pk).update(container_id=container_name)

    clone_dir = None
    started_monotonic = time.monotonic()
    outcome = ("failed", HarnessJob.FailureReason.RUNNER_ERROR, "")
    try:
        clone_dir = runner.clone_repo(
            git_url=job.repo.git_url,
            default_branch=job.repo.default_branch,
            clone_env={},
        )
        image = runner.image_for_agent(job.agent)
        cli_env = _resolve_cli_env(job.agent)
        runner.launch_container(
            image=image, clone_dir=clone_dir, goal=job.goal, cli_env=cli_env, name=container_name
        )
        outcome = _wait_for_container(job.pk, container_name, started_monotonic)
    except runner.RunnerError:
        logger.exception("Harness job %s failed to launch", job_id)
    finally:
        # Cleanup failures must never prevent `_finalize` from running
        # with the real, already-computed `outcome` (Codex code-review
        # P2, branch 2 round 4): `remove_container`'s own `subprocess.run`
        # can raise `TimeoutExpired` if the Docker daemon itself is stuck,
        # and an exception raised inside `finally` would abort this whole
        # function before `_finalize` is ever reached below -- losing the
        # actual success/failure/token/cost detail this run already
        # determined, in favor of whatever generic "unexpected error"
        # handling the Celery task's own outer safety net falls back to.
        # `removal_confirmed` (Codex code-review P2, branch 2 round 6) is
        # threaded into `_finalize` so `HarnessJob.container_id` is only
        # ever cleared once removal is actually confirmed -- clearing it
        # unconditionally on a genuinely failed removal would orphan a
        # still-running container with no handle left to find it by.
        removal_confirmed = _safe_remove_container(container_name)
        if clone_dir:
            _safe_cleanup_directory(clone_dir)

    _finalize(job, outcome, started_monotonic, removal_confirmed=removal_confirmed)


def _safe_remove_container(container_name):
    """Returns `True` if the container is confirmed gone (removed, or
    already gone), `False` if removal genuinely failed -- callers must NOT
    clear `HarnessJob.container_id` on a `False` return (Codex code-review
    P2, branch 2 round 6)."""
    return container_lifecycle.safe_remove_container(container_name, label="harness container")


def _safe_cleanup_directory(clone_dir):
    """Best-effort: the read-only clone is genuinely temporary, nothing
    persists a pointer to it."""
    container_lifecycle.cleanup_directory(clone_dir, confirm=False, label="harness clone directory")


def _finalize(job, outcome, started_monotonic, removal_confirmed=True):
    status, failure_reason, raw_output = outcome
    success = status == "succeeded"
    latency_ms = int((time.monotonic() - started_monotonic) * 1000)

    input_tokens, output_tokens, estimated_cost, context_report = (0, 0, Decimal("0"), {})
    if job.agent == HarnessJob.Agent.CLAUDE_CODE and raw_output:
        input_tokens, output_tokens, estimated_cost, context_report = _parse_claude_code_output(
            raw_output
        )

    # Cost brake (brief §2, brake #2): necessarily post-hoc for a single
    # external call -- there is no incremental signal to stop mid-run on,
    # only the CLI's own final reported figure. Exceeding it flags the
    # otherwise-successful job for review; it cannot un-spend the cost.
    if success and estimated_cost > Decimal(str(settings.HARNESS_MAX_JOB_COST)):
        status, failure_reason = "failed", HarnessJob.FailureReason.BUDGET_EXCEEDED
        success = False

    # Codex code-review P2, branch 2 round 4: an otherwise-successful run
    # whose cost genuinely could not be confirmed -- `context_report`'s
    # `usage_source` is anything other than `"cli_reported"` (the CLI's own
    # output was malformed, empty, a shape this parser doesn't recognize,
    # or `raw_output` was empty so parsing was never even attempted) --
    # must not get a free pass past the brake above just because
    # `Decimal("0")` never compares greater than a positive budget.
    # `agent` is only selectable in this phase specifically because it's
    # expected to report cost reliably (brief, brake #2 selectability
    # rule) -- a run that fails to honor that expectation is a
    # runner-level failure, not a free (cost=0) success.
    if success and context_report.get("usage_source") != "cli_reported":
        status, failure_reason = "failed", HarnessJob.FailureReason.RUNNER_ERROR
        success = False

    # Cancellation is decided and persisted inside ONE held lock, together
    # with usage recording and the terminal status save (Codex code-review
    # P1, branch 2 round 3; P2, round 5: the round-3 fix re-checked
    # `cancel_requested` in a bare, unlocked query before this point --
    # closing the container-exit-vs-poll race, but leaving a narrower one
    # open between that unlocked check and the locked save below, where a
    # `cancel_harness_job` call landing in between would still see
    # `running` and flip the flag, only to have this function silently
    # overwrite it with a stale "succeeded"/usage row anyway). Because
    # `cancel_harness_job`'s own `_locked_job` takes the SAME row-level
    # `select_for_update`, holding this lock across both the re-check and
    # both writes below makes the two mutually exclusive by construction,
    # not just by re-checking and hoping the window was already closed: a
    # concurrent cancel either completes (and is visible to `locked.
    # cancel_requested` here) before this transaction starts, or blocks
    # until this transaction commits (and then finds `status` already
    # terminal, at which point cancelling is correctly a no-op).
    #
    # This terminal save happens BEFORE briefing production below (Codex
    # code-review P1, branch 3 round 1): the gateway call the briefing
    # needs is an unbounded-latency round-trip that can outlast the
    # Celery soft time limit on its own. If it ran first and got
    # interrupted mid-call, the outer task handler would find the job
    # still `running` and call `resolve_running_job_after_unexpected_error`,
    # discarding the real outcome and usage this run already earned.
    # Persisting the terminal state first means that function's own
    # `status=RUNNING` filter makes it a safe no-op by the time briefing
    # could possibly be interrupted -- the worst a slow/killed gateway
    # call can now do is leave `briefing` empty, not lose the job's actual
    # result.
    with transaction.atomic():
        locked = HarnessJob.objects.select_for_update(of=("self",)).filter(pk=job.pk).first()
        if locked is None:
            return
        if success and locked.cancel_requested:
            status, failure_reason, success = "cancelled", HarnessJob.FailureReason.KILLED, False

        record_external_usage(
            workspace=job.workspace,
            purpose="harness.discovery",
            backend=f"harness_cli:{job.agent}",
            routing_reason=job.agent_routing_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            latency_ms=latency_ms,
            success=success,
            actor=job.created_by,
            error_type="" if success else (failure_reason or status),
            context_report=context_report,
        )

        locked.status = status
        locked.failure_reason = failure_reason
        # Only clear the handle once removal is confirmed (Codex
        # code-review P2, branch 2 round 6) -- if it's still populated,
        # the recovery scan can find and retry against this exact
        # container on its next pass instead of the handle being lost.
        if removal_confirmed:
            locked.container_id = ""
        locked.elapsed_seconds = max(0, int(latency_ms / 1000))
        locked.save(
            update_fields=[
                "status",
                "failure_reason",
                "container_id",
                "elapsed_seconds",
                "updated_at",
            ]
        )
    # Codex code-review P2, branch 2 round 3: worker-side terminal
    # transitions (success, failure, timeout, budget-exceeded, mid-run
    # cancellation) only ever updated HarnessJob/ModelUsage -- the audit
    # trail had job creation and an owner-initiated cancel *request*, but
    # nothing recording how a run actually ended. Written after the
    # status above is durable, outside the lock (record_audit does not
    # need the row locked, only a stable action/metadata pair).
    record_audit(
        action=f"harness.job_{status}",
        actor=job.created_by,
        workspace=job.workspace,
        resource=job,
        metadata={"failure_reason": failure_reason} if failure_reason else {},
    )

    # Briefing production (brief §3): attempted whenever there's a
    # transcript to summarize, independent of the job's own success/
    # failure -- branch 4's UI still wants something to show for a failed
    # or cancelled job, not just a successful one. Run with NO database
    # lock held (mirrors `agents.execution.run_read_step`'s identical
    # "unbounded-latency gateway call, no lock" shape) -- a slow gateway
    # round-trip must never hold a Postgres row lock. A `GatewayError`
    # here does not retroactively change the job's own status (already
    # durable above); it just means no *summary* exists for this job.
    #
    # The bounded raw transcript is saved BEFORE the gateway call, not
    # after (Codex code-review P2, branch 3 round 5): round 4's fix
    # covered a `GatewayError`/`ImproperlyConfigured` raised BY the call,
    # but if the Celery soft time limit fires WHILE the call is still
    # blocked -- a realistic timeout path, not just a clean failure --
    # execution never reaches the single combined update below at all.
    # By then the job's own terminal status is already durable (this
    # function's earlier lock), so the outer recovery path is a correct
    # no-op -- but it also means nothing else will ever come back to
    # persist the transcript. Saving it up front, then overwriting with
    # the real summary/findings once known, means a mid-call interruption
    # loses only the summary, never the one stored CLI transcript for
    # audit/debugging.
    briefing = {}
    if raw_output:
        bounded_raw, fenced_transcript = _fence_and_bound_transcript(raw_output)
        briefing = {"summary": "", "findings": [], "raw_transcript": bounded_raw}
        HarnessJob.objects.filter(pk=job.pk).update(briefing=briefing)
        try:
            summary, findings = _produce_briefing_summary(job, fenced_transcript)
            briefing = {"summary": summary, "findings": findings, "raw_transcript": bounded_raw}
            HarnessJob.objects.filter(pk=job.pk).update(briefing=briefing)
        except GatewayError:
            logger.exception("Harness job %s briefing production failed", job.pk)
        except ImproperlyConfigured:
            logger.exception(
                "Harness job %s briefing production skipped -- prompt template misconfigured",
                job.pk,
            )

    # work_log production (brief §3): on job success ONLY -- content is
    # the gateway-produced `summary`, never `raw_transcript` (exactly the
    # kind of untrusted, potentially-large text ADR-005's negative
    # contract and the memory char-budget discipline both argue against
    # ever embedding wholesale). `sensitivity` is the job's own snapshotted
    # value (branch 2), never re-derived from the repo's current state.
    # No row at all if briefing production itself failed/was skipped --
    # there is nothing meaningful to log. `record_work_log`'s own
    # `DuplicateMemory` (identical fingerprint already exists in this
    # workspace) is a legitimate, silent no-op here, not an error this
    # function needs to handle -- two runs that happen to produce the
    # exact same summary text genuinely are duplicates.
    if success and briefing.get("summary"):
        try:
            record_work_log(
                workspace=job.workspace,
                title=job.goal[:_WORK_LOG_TITLE_MAX_CHARS],
                content=briefing["summary"],
                sensitivity=job.sensitivity,
            )
        except DuplicateMemory:
            pass

    # Phase 8A branch 4 (brief D5): every terminal job -- succeeded, failed
    # or cancelled -- becomes one `harness` episode. Best-effort: never
    # raises into this state machine.
    ingestion.record_discovery_outcome(job, cost=estimated_cost)


def _claim_stale_job(job_id, threshold):
    """Fenced claim for the recovery scan (Codex code-review P2, branch 2
    round 1): two overlapping scans must not both reach
    `record_external_usage()` for the same job -- transition the row to
    `failed` under lock FIRST, and only the scan whose `select_for_update`
    actually finds it still `running` (re-checked against `threshold`
    inside the same locked read, not just the caller's earlier queryset)
    proceeds to tear down the container and record usage. A losing scan
    (the row is no longer `running`, or no longer stale, by the time it
    gets the lock) gets `None` back and does nothing further -- no
    double-teardown, no duplicate `ModelUsage` row."""
    with transaction.atomic():
        locked = (
            HarnessJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status=HarnessJob.Status.RUNNING, started_at__lt=threshold)
            .first()
        )
        if locked is None:
            return None
        # `container_id` is deliberately NOT cleared here (Codex
        # code-review P2, branch 2 round 6): the fencing this claim needs
        # only requires the status transition -- clearing the handle
        # before teardown/removal is even attempted would lose it if that
        # removal genuinely fails. The caller clears it separately, only
        # once removal is confirmed.
        locked.status = HarnessJob.Status.FAILED
        locked.failure_reason = HarnessJob.FailureReason.TIMEOUT
        locked.save(update_fields=["status", "failure_reason", "updated_at"])
        return locked


_TERMINAL_STATUSES = (
    HarnessJob.Status.SUCCEEDED,
    HarnessJob.Status.FAILED,
    HarnessJob.Status.CANCELLED,
)


def _sweep_leaked_terminal_container(job_id):
    """One iteration of the recovery scan's third case below. Re-fetches
    under lock first (mirrors `_claim_stale_job`'s own discipline) so two
    overlapping scans don't both attempt removal of the same container --
    the Docker call itself happens OUTSIDE the lock, same as every other
    recovery case in this module. Returns `True` only once removal is
    actually confirmed."""
    with transaction.atomic():
        locked = (
            HarnessJob.objects.select_for_update(of=("self",))
            .filter(pk=job_id, status__in=_TERMINAL_STATUSES)
            .exclude(container_id="")
            .first()
        )
        if locked is None:
            return False
        container_id = locked.container_id
    if _safe_remove_container(container_id):
        HarnessJob.objects.filter(pk=job_id, container_id=container_id).update(container_id="")
        return True
    return False


def recover_stale_harness_jobs():
    """Periodic scan (brief §2, brake #4) covering three distinct ways a
    `HarnessJob` can get permanently stuck, or a container permanently
    leaked, with no live worker ever coming back to unstick it (mirrors
    `agents.execution.recover_stale_agent_execution`'s identical
    multi-case shape):

    1. Still `running` past `HARNESS_MAX_JOB_SECONDS` +
       `HARNESS_RECOVERY_GRACE_SECONDS` since it started, never finalized
       by its own worker (crash/OOM/hard-kill): swept using the job's own
       `container_id` to run the same stop/kill/rm sequence
       `run_harness_job` uses, rather than assuming a dead worker means a
       dead container -- an orphaned container keeps running, reading
       repo content and holding runner resources, with no other
       in-process handle to it. Teardown and usage recording happen AFTER
       the claim commits, only for the scan that actually won it -- no
       lock held across the Docker calls themselves, mirroring
       `cancel_harness_job`'s own short-fenced-flip-then-act shape.
    2. Still `pending` past `HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS` since
       creation: its `transaction.on_commit`-scheduled dispatch was lost
       (Codex code-review P2, branch 2 round 2 -- the broker was down at
       commit time, the exact swallowed-exception path in
       `harness.services.dispatch_harness_job`). Simply re-dispatched.
    3. Already terminal (`succeeded`/`failed`/`cancelled`) but still
       carrying a non-empty `container_id`, past
       `HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS` since its last
       update (Codex code-review P2, branch 2 round 7): its own
       `_safe_remove_container` call at finalize time genuinely failed
       (round 6's fix deliberately preserves `container_id` on that path
       rather than clearing it), and unlike case 1, a terminal job is never
       swept by the `status=running` scan above -- so without this case
       the leaked container would persist on the host indefinitely, with
       nothing ever revisiting it. Retried here via the same
       `_safe_remove_container` helper.
    """
    threshold = timezone.now() - timedelta(
        seconds=settings.HARNESS_MAX_JOB_SECONDS + settings.HARNESS_RECOVERY_GRACE_SECONDS
    )
    stale_ids = list(
        HarnessJob.objects.filter(
            status=HarnessJob.Status.RUNNING, started_at__lt=threshold
        ).values_list("pk", flat=True)
    )
    recovered = 0
    for job_id in stale_ids:
        job = _claim_stale_job(job_id, threshold)
        if job is None:
            continue
        container_id = job.container_id
        if container_id:
            _teardown_container(container_id)
            # _safe_remove_container, not runner.remove_container directly
            # (Codex code-review P2, branch 2 round 4): this call sits
            # inside a loop over every stale job in the batch -- an
            # unhandled Docker-control failure here must not abort
            # recovery of every OTHER stale job still waiting in
            # `stale_ids`. Only clear the handle once removal is confirmed
            # (round 6) -- a genuinely failed removal must not lose the
            # only remaining reference to a still-orphaned container.
            if _safe_remove_container(container_id):
                HarnessJob.objects.filter(pk=job.pk).update(container_id="")
        record_external_usage(
            workspace=job.workspace,
            purpose="harness.discovery",
            backend=f"harness_cli:{job.agent}",
            routing_reason=job.agent_routing_reason,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            latency_ms=0,
            success=False,
            actor=job.created_by,
            error_type="timeout",
            context_report={"usage_source": "unavailable", "recovered": True},
        )
        record_audit(
            action="harness.job_failed",
            actor=job.created_by,
            workspace=job.workspace,
            resource=job,
            metadata={"failure_reason": HarnessJob.FailureReason.TIMEOUT, "recovered": True},
        )
        ingestion.record_discovery_outcome(job)
        recovered += 1

    # Second, distinct recovery case (Codex code-review P2, branch 2 round
    # 2; mirrors agents.execution.recover_stale_agent_execution's identical
    # dispatch-lost case): a job still `pending` past
    # HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS had its on_commit-scheduled
    # dispatch lost (the broker was down at commit time -- the exact
    # swallowed-exception path in dispatch_harness_job). Simply
    # re-dispatched; `_claim_job`'s own fencing makes this safe even if the
    # original dispatch actually does arrive later.
    dispatch_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_DISPATCH_RECOVERY_GRACE_SECONDS
    )
    stale_pending_ids = list(
        HarnessJob.objects.filter(
            status=HarnessJob.Status.PENDING, created_at__lt=dispatch_cutoff
        ).values_list("pk", flat=True)
    )
    for job_id in stale_pending_ids:
        dispatch_harness_job(job_id)

    # Third, distinct recovery case (Codex code-review P2, branch 2 round
    # 7): a terminal job with a leftover `container_id` past
    # HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS since its last
    # update -- see this function's own docstring, case 3.
    leaked_cutoff = timezone.now() - timedelta(
        seconds=settings.HARNESS_LEAKED_CONTAINER_RECOVERY_GRACE_SECONDS
    )
    leaked_ids = list(
        HarnessJob.objects.filter(status__in=_TERMINAL_STATUSES, updated_at__lt=leaked_cutoff)
        .exclude(container_id="")
        .values_list("pk", flat=True)
    )
    for job_id in leaked_ids:
        _sweep_leaked_terminal_container(job_id)

    return recovered


def resolve_running_job_after_unexpected_error(job_id):
    """Called by `harness.tasks.run_harness_job`'s outer safety net when
    `run_harness_job` above is interrupted by `SoftTimeLimitExceeded` (the
    Celery soft time limit firing) or any other unexpected exception, after
    this module's own `try`/`finally` has already had a chance to tear down
    the container/clone directory in the normal case. Without this, a job
    `_claim_job` already committed to `running` would stay `running` until
    the recovery scan reclaims it after `HARNESS_RECOVERY_GRACE_SECONDS` --
    not stuck forever, but stalled for that whole window when the failure
    can be resolved immediately (Codex code-review P2, branch 2 round 1;
    mirrors `agents.execution.resolve_running_step_after_unexpected_error`).
    Defensively re-attempts container teardown using the row's own
    `container_id` in case the interrupted run's own `finally` didn't
    reach it. A safe no-op if the job is no longer `running` (already
    finalized normally, or by a previous call to this same function)."""
    job = HarnessJob.objects.filter(pk=job_id, status=HarnessJob.Status.RUNNING).first()
    if job is None:
        return
    # Only clear the handle once removal is confirmed (Codex code-review
    # P2, branch 2 round 6) -- a genuinely failed removal must not lose
    # the only remaining reference to a still-orphaned container.
    removal_confirmed = True
    if job.container_id:
        _teardown_container(job.container_id)
        removal_confirmed = _safe_remove_container(job.container_id)
    record_external_usage(
        workspace=job.workspace,
        purpose="harness.discovery",
        backend=f"harness_cli:{job.agent}",
        routing_reason=job.agent_routing_reason,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=Decimal("0"),
        latency_ms=0,
        success=False,
        actor=job.created_by,
        error_type="timeout",
        context_report={"usage_source": "unavailable", "unexpected_error": True},
    )
    update_fields = {
        "status": HarnessJob.Status.FAILED,
        "failure_reason": HarnessJob.FailureReason.TIMEOUT,
    }
    if removal_confirmed:
        update_fields["container_id"] = ""
    updated = HarnessJob.objects.filter(pk=job_id, status=HarnessJob.Status.RUNNING).update(
        **update_fields
    )
    if updated:
        record_audit(
            action="harness.job_failed",
            actor=job.created_by,
            workspace=job.workspace,
            resource=job,
            metadata={"failure_reason": HarnessJob.FailureReason.TIMEOUT, "unexpected_error": True},
        )
        ingestion.record_discovery_outcome(job)
