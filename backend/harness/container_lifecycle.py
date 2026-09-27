"""Shared leaf layer of the harness container lifecycle (Phase 5 branch 3,
brief D12). The three execution engines (`harness.execution`,
`harness.implementation_execution`, `harness.review_execution`) each kept
an identical copy of teardown/remove and a brake loop that differed only
in its stop predicate, transcript capture and a post-exit cancel
re-check; those pieces live here, parameterized by exactly those
differences. Claim/finalize/recovery stay in each engine -- they differ
in substance, not just in parameters.

Nothing here knows about any job model or `FailureReason`: `wait_for_
container` returns a `WaitOutcome` and each caller maps it to its own
reason. Every Docker call goes through the `harness.runner` module
attribute (never a `from harness.runner import ...` binding), so the
existing `monkeypatch.setattr(<engine>.runner, ...)` test seams still
reach it.
"""

import enum
import logging
import os
import time
from dataclasses import dataclass

from django.conf import settings

from harness import runner

logger = logging.getLogger(__name__)


class WaitResult(enum.StrEnum):
    EXITED_CLEANLY = "exited_cleanly"
    # Non-zero exit code, or `None` (the exit state could not be read) --
    # only a confirmed `0` counts as a clean exit.
    EXITED_WITH_ERROR = "exited_with_error"
    TIMED_OUT = "timed_out"
    # The caller's stop predicate fired, either while the container was
    # still running (it was torn down) or -- with `recheck_stop_after_exit`
    # -- in the gap between the last poll and the container's own exit.
    STOPPED = "stopped"


@dataclass(frozen=True)
class WaitOutcome:
    result: WaitResult
    # The container's captured logs when `capture_transcript=True`; always
    # `""` otherwise (the caller captures them itself, if at all).
    transcript: str = ""


def teardown_container(container_id, *, label):
    """Best-effort stop/kill -- never lets a Docker-control failure (e.g.
    `subprocess.run`'s own `TimeoutExpired` if the daemon itself is stuck)
    escape and abort whatever caller is in the middle of computing a job's
    outcome (Codex code-review P2, branch 2 round 4). Every caller still
    unconditionally removes the container afterward on its own exit path;
    this only best-effort stops the process inside. `label` names the
    container kind in the log line (e.g. "harness review container")."""
    try:
        runner.stop_container(container_id, grace_seconds=settings.HARNESS_KILL_GRACE_SECONDS)
        if runner.container_is_running(container_id):
            runner.kill_container(container_id)
    except Exception:
        logger.exception("Failed to stop/kill %s %s", label, container_id)


def safe_remove_container(container_name, *, label):
    """Returns `True` if the container is confirmed gone (removed, or
    already gone), `False` if removal genuinely failed -- callers must NOT
    clear a persisted `container_id` on a `False` return (Codex
    code-review P2, branch 2 round 6): the recovery scans retry removal
    through that handle."""
    try:
        runner.remove_container(container_name)
        return True
    except Exception:
        logger.exception("Failed to remove %s %s (leaked on host)", label, container_name)
        return False


def cleanup_directory(path, *, confirm, label):
    """Two deliberately different disciplines (brief D12):

    - `confirm=True` -- returns `True` only when `path` is confirmed gone
      from disk, `False` otherwise; callers must not clear a persisted
      path on `False`. Confirmation comes from `os.path.exists` AFTER the
      call, never from the call not raising: `runner.cleanup_directory`
      never raises by design (Codex code-review P1, Phase 3 branch 2
      round 5), so a `try/except` would always report success.
    - `confirm=False` -- best-effort for genuinely temporary directories
      nothing persists a pointer to: any exception is logged and
      swallowed, and nothing is returned (a failure leaks a temp
      directory on the runner host, an accepted residual risk)."""
    if confirm:
        runner.cleanup_directory(path)
        if os.path.exists(path):
            logger.error("Failed to fully clean up %s %s (still present on disk)", label, path)
            return False
        return True
    try:
        runner.cleanup_directory(path)
    except Exception:
        logger.exception("Failed to clean up %s %s", label, path)
    return None


def wait_for_container(
    container_id,
    *,
    started_monotonic,
    teardown,
    should_stop=None,
    recheck_stop_after_exit=False,
    capture_transcript=True,
):
    """Non-blocking wait loop around the **whole** container lifetime:
    polls the runtime brake (`HARNESS_MAX_JOB_SECONDS`, measured from
    `started_monotonic`) and `should_stop` every
    `HARNESS_CANCEL_POLL_SECONDS`, so a stop request during the one long
    phase (the CLI's own run) is observed before the container exits on
    its own (Codex round-4 fix, Phase 2 branch 2).

    - `teardown(container_id)` stops a container the loop gives up on.
      Passed by the caller (its module-level `_teardown_container`) so
      each engine's own seam stays patchable.
    - `should_stop()` -- the caller's stop predicate (a cancel flag, or
      "the job no longer wants this review"); `None` means timeout-only.
    - `recheck_stop_after_exit` -- consult `should_stop` once more after
      the container stopped running (Codex code-review P1, Phase 2 branch
      2 round 3): a stop arriving between the loop's last "still running"
      check and the container's own natural exit would otherwise be
      silently dropped. Checked before the exit code, so it wins over it.
    - `capture_transcript` -- capture the container's logs on every
      outcome (before teardown on the brake paths, while it is still
      inspectable). With `False` no logs are read here.

    A container that is no longer running only stopped -- a crash and a
    clean exit look identical to `container_is_running` -- so its exit
    code decides: only a confirmed `0` is `EXITED_CLEANLY`; a non-zero or
    `None` code (inspect failed or returned something unparsable, a
    Docker-control failure, not evidence of a clean exit) fails closed as
    `EXITED_WITH_ERROR` (Codex round-2 P2, Phase 2 branch 2).

    `runner.RunnerError` from any Docker call propagates to the caller,
    exactly as before the extraction."""
    while runner.container_is_running(container_id):
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= settings.HARNESS_MAX_JOB_SECONDS:
            transcript = runner.capture_logs(container_id) if capture_transcript else ""
            teardown(container_id)
            return WaitOutcome(WaitResult.TIMED_OUT, transcript)
        if should_stop is not None and should_stop():
            transcript = runner.capture_logs(container_id) if capture_transcript else ""
            teardown(container_id)
            return WaitOutcome(WaitResult.STOPPED, transcript)
        time.sleep(settings.HARNESS_CANCEL_POLL_SECONDS)
    transcript = runner.capture_logs(container_id) if capture_transcript else ""
    if recheck_stop_after_exit and should_stop is not None and should_stop():
        return WaitOutcome(WaitResult.STOPPED, transcript)
    exit_code = runner.container_exit_code(container_id)
    if exit_code != 0:
        return WaitOutcome(WaitResult.EXITED_WITH_ERROR, transcript)
    return WaitOutcome(WaitResult.EXITED_CLEANLY, transcript)
