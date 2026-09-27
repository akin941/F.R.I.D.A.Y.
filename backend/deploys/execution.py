"""The deploy runner (brief D10): claims a `DeployRun`, drives it through
the four verbs, and writes exactly one terminal state. Celery-agnostic
(`deploys.tasks` wraps it) so the state machine is unit-tested without a
broker, an SSH server or a clock: `ssh.run_verb`, `health.check_url`,
`_sleep` and `_monotonic` are module-attribute seams.

    pending -> checking --(status verb: repo, self-repo, drift checks)-->
      deploying --(deploy verb)--> health_checking
        -> succeeded | succeeded_degraded
        -> health_failed                    (auto_rollback off)
        -> rolling_back --(rollback verb + health)--> rolled_back | rollback_failed
    any step before the deploy verb -> failed (nothing live changed) | cancelled
    runner lost / unexpected error after the deploy verb -> unknown_outcome

Rules that make this safe (ADR-010 decision 7):
- The runner acts on the decided snapshot (`run.plan`), and refuses before
  the deploy verb if the target's `config_hash` no longer matches it or the
  target was disabled -- checked again under the row lock that marks
  `deploy_started_at`, the point after which the run can neither be
  cancelled nor retried.
- Drift: the remote's live SHA must equal `expected_current_sha`, else
  `target_drifted` and nothing is touched. The remote repo must
  canonicalize to the target's repo and never to Jarvis's own.
- Health: two consecutive passes of the remote `health` verb succeed;
  three consecutive failures fail; a window that elapses without two
  consecutive passes fails (fail closed). A failing `health_url` alone
  never fails the run: it ends `succeeded_degraded`.
- Rollback only to `rollback_sha`, only within this run, only when the
  decided plan had `auto_rollback` on.
- Never retried after the deploy verb: a lost runner leaves
  `unknown_outcome`; the next request's drift check covers the rest.

Every post-claim write is fenced on `claim_token` + expected status
(`VaultExportRun`'s pattern) and renews the lease; losing the fence (a
cancel, or the recovery scan declaring the runner lost) stops the run
without another remote command.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from core.repo_identity import canonicalize_git_url
from deploys import health, ssh
from deploys.models import DeployRun, DeployTarget, compute_config_hash
from deploys.outcomes import record_terminal
from deploys.services import self_repo_error
from deploys.validation import APP_RE, SHA_RE, TargetFieldError

logger = logging.getLogger(__name__)

# Seams (tests replace them).
_sleep = time.sleep
_monotonic = time.monotonic

S = DeployRun.Status
E = DeployRun.ErrorCode

# The remote script's exit codes for a verb it refused or failed BEFORE
# changing anything live (deploy/remote/jarvis-deploy.sh): usage, refused
# (not an ancestor of the tracked branch, already live, bad config), build
# failed with the previous checkout restored, remote unavailable (fetch),
# and busy (another deploy holds the app's lock).
UNTOUCHED_DEPLOY_EXITS = {
    64: E.DEPLOY_REFUSED,
    65: E.DEPLOY_REFUSED,
    66: E.BUILD_FAILED,
    69: E.REMOTE_UNAVAILABLE,
    75: E.REMOTE_BUSY,
}

PHASE_DEPLOY = "deploy"
PHASE_ROLLBACK = "rollback"
_STATUS_KEYS = ("app", "sha", "branch", "remote", "previous")


class FenceLost(Exception):
    """This attempt no longer owns the run (cancelled, or declared lost)."""


# --- claim / fence ---


def _lease_deadline():
    return timezone.now() + timedelta(seconds=settings.DEPLOY_RUN_LEASE_SECONDS)


def claim_deploy_run(run_id):
    """`pending`, or `checking` with an expired lease and the deploy verb
    never sent (nothing touched, safe to redo the read-only status check).
    Returns the claim token, or None (a safe no-op)."""
    now = timezone.now()
    with transaction.atomic():
        run = (
            DeployRun.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=run_id)
                & (
                    Q(status=S.PENDING)
                    | Q(
                        status=S.CHECKING,
                        deploy_started_at__isnull=True,
                        lease_expires_at__lt=now,
                    )
                )
            )
            .first()
        )
        if run is None:
            return None
        token = uuid.uuid4()
        run.status = S.CHECKING
        run.claim_token = token
        run.lease_expires_at = _lease_deadline()
        run.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
    return token


def _fenced(run_id, token, statuses):
    run = (
        DeployRun.objects.select_for_update(of=("self",))
        .select_related("target", "workspace", "approval_request")
        .filter(pk=run_id, claim_token=token, status__in=list(statuses))
        .first()
    )
    if run is None:
        raise FenceLost(str(run_id))
    return run


def _mutate(run_id, token, statuses, **changes):
    """Fenced update that also renews the lease. `output_append` (a string)
    is appended to the bounded output; `check` is appended to
    `health_checks`."""
    with transaction.atomic():
        run = _fenced(run_id, token, statuses)
        fields = {"lease_expires_at", "updated_at"}
        output = changes.pop("output_append", None)
        if output:
            run.output = ssh.bound_output(f"{run.output}{output}")
            fields.add("output")
        check = changes.pop("check", None)
        if check is not None:
            run.health_checks = [*(run.health_checks or []), check]
            fields.add("health_checks")
        for name, value in changes.items():
            setattr(run, name, value)
            fields.add(name)
        run.lease_expires_at = _lease_deadline()
        run.save(update_fields=sorted(fields))
        return run


def _finish(run_id, token, statuses, status, error_code="", *, current_sha=None, output=None):
    """Terminal write + audit + work_log, fenced. `current_sha` (when not
    None) is what Jarvis now believes is live on the target; "" means
    unknown -- a new request then needs a status refresh first."""
    with transaction.atomic():
        run = _fenced(run_id, token, statuses)
        run.status = status
        run.error_code = error_code
        run.claim_token = None
        run.lease_expires_at = None
        run.finished_at = timezone.now()
        if output:
            run.output = ssh.bound_output(f"{run.output}{output}")
        run.save(
            update_fields=[
                "status",
                "error_code",
                "claim_token",
                "lease_expires_at",
                "finished_at",
                "output",
                "updated_at",
            ]
        )
        if current_sha is not None:
            _set_current_sha(run.target_id, current_sha)
        record_terminal(run)
        return run


def _set_current_sha(target_id, sha):
    target = DeployTarget.objects.select_for_update().get(pk=target_id)
    target.current_sha = sha
    target.save(update_fields=["current_sha", "updated_at"])


def _audit_step(run, action):
    record_audit(
        action=action,
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=run.workspace,
        resource=run,
        metadata={
            "target": str(run.target_id),
            "sha": run.sha,
            "rollback_sha": run.rollback_sha,
        },
    )


def _output_block(verb, result):
    code = "timeout" if result.timed_out else result.exit_code
    return f"$ {verb} (exit {code})\n{result.output}\n"


# --- status parsing ---


def parse_status(text):
    """`key=value` lines from the `status` verb; the first occurrence of
    each known key wins. Returns a dict, or None when the required fields
    are missing or malformed."""
    values = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in _STATUS_KEYS and key not in values:
            values[key] = value.strip()
    if not APP_RE.match(values.get("app", "")) or not SHA_RE.match(values.get("sha", "")):
        return None
    if not values.get("remote"):
        return None
    return values


def check_status(parsed, *, remote_app, repo_git_url):
    """The pre-deploy identity checks on a parsed status. Returns an error
    code or None."""
    if parsed is None or parsed["app"] != remote_app:
        return E.STATUS_UNPARSEABLE
    guard = self_repo_error(parsed["remote"])
    if guard is not None:
        return guard
    if canonicalize_git_url(parsed["remote"]) != canonicalize_git_url(repo_git_url):
        return E.REPO_MISMATCH
    return None


# --- health ---


@dataclass(frozen=True)
class HealthOutcome:
    passed: bool
    url_failed: bool = False
    error: str = ""


def _health_verb_ok(target_fields):
    try:
        result = ssh.run_verb(
            target_fields, "health", timeout=settings.DEPLOY_SSH_HEALTH_TIMEOUT_SECONDS
        )
    except (ssh.SSHConfigError, TargetFieldError):
        return False, None
    return result.ok, result


def run_health_phase(run_id, token, plan, *, phase, status):
    """Checks spaced evenly across the window (`interval_seconds`). Two
    consecutive verb passes -> passed; three consecutive failures -> failed;
    window elapsed first -> failed (fail closed: an alternating pattern
    never passes). A failing `health_url` never fails a check on its own;
    it only marks a passing phase degraded."""
    health_plan = plan["health_plan"]
    target_fields = plan["target"]
    window = float(health_plan["window_seconds"])
    interval = float(health_plan["interval_seconds"])
    pass_needed = int(health_plan["pass_threshold"])
    fail_needed = int(health_plan["fail_threshold"])
    url = health_plan.get("health_url") or ""
    deadline = _monotonic() + window
    passes = fails = 0
    streak_url_failed = False
    while True:
        _sleep(interval)
        verb_ok, result = _health_verb_ok(target_fields)
        url_ok = health.check_url(url) if url else None
        check = {
            "phase": phase,
            "at": timezone.now().isoformat(),
            "verb_ok": verb_ok,
            "url_ok": url_ok,
        }
        output = _output_block("health", result) if (result is not None and not verb_ok) else None
        _mutate(run_id, token, [status], check=check, output_append=output)
        if verb_ok:
            fails = 0
            passes += 1
            streak_url_failed = (streak_url_failed if passes > 1 else False) or url_ok is False
            if passes >= pass_needed:
                return HealthOutcome(passed=True, url_failed=streak_url_failed)
        else:
            passes = 0
            fails += 1
            if fails >= fail_needed:
                return HealthOutcome(passed=False, error=E.HEALTH_CHECKS_FAILED)
        if _monotonic() >= deadline:
            return HealthOutcome(passed=False, error=E.HEALTH_WINDOW_ELAPSED)


# --- the run ---


def _precheck(run):
    """Before any remote contact. Returns an error code or None."""
    plan = run.plan or {}
    target = run.target
    if not target.enabled:
        return E.TARGET_DISABLED
    if target.config_hash != plan.get("config_hash") or compute_config_hash(
        plan.get("target") or {}
    ) != plan.get("config_hash"):
        return E.TARGET_CHANGED
    return self_repo_error(plan["target"]["repo_git_url"])


def execute_deploy_run(run_id):
    """Entry point (`deploys.tasks.run_deploy`). A safe no-op if the run
    is not claimable."""
    token = claim_deploy_run(run_id)
    if token is None:
        return None
    try:
        _drive(run_id, token)
    except FenceLost:
        logger.info("deploy run %s: fence lost, stopping without further remote commands", run_id)
    except Exception:
        logger.exception("deploy run %s: unexpected failure", run_id)
        resolve_after_unexpected_error(run_id, token)
    return token


def _drive(run_id, token):
    run = DeployRun.objects.select_related("target").get(pk=run_id)
    plan = run.plan
    target_fields = plan["target"]

    # 1. Nothing contacted yet.
    error = _precheck(run)
    if error is not None:
        _finish(run_id, token, [S.CHECKING], S.FAILED, error)
        return

    # 2. status: identity + drift.
    try:
        result = ssh.run_verb(
            target_fields, "status", timeout=settings.DEPLOY_SSH_STATUS_TIMEOUT_SECONDS
        )
    except ssh.SSHConfigError:
        _finish(run_id, token, [S.CHECKING], S.FAILED, E.SSH_KEY_MISSING)
        return
    status_output = _output_block("status", result)
    if result.transport_failed:
        _finish(run_id, token, [S.CHECKING], S.FAILED, E.SSH_FAILED, output=status_output)
        return
    if not result.ok:
        _finish(run_id, token, [S.CHECKING], S.FAILED, E.STATUS_FAILED, output=status_output)
        return
    parsed = parse_status(result.output)
    error = check_status(
        parsed, remote_app=target_fields["remote_app"], repo_git_url=target_fields["repo_git_url"]
    )
    if error is None and parsed["sha"] != run.expected_current_sha:
        error = E.TARGET_DRIFTED
    if error is not None:
        _finish(run_id, token, [S.CHECKING], S.FAILED, error, output=status_output)
        return

    # 3. Point of no return, under the run lock (a cancel serializes here)
    #    and the target lock (config/enabled re-checked).
    with transaction.atomic():
        run = _fenced(run_id, token, [S.CHECKING])
        target = DeployTarget.objects.select_for_update().get(pk=run.target_id)
        late_error = None
        if not target.enabled:
            late_error = E.TARGET_DISABLED
        elif target.config_hash != plan["config_hash"]:
            late_error = E.TARGET_CHANGED
        if late_error is None:
            run.status = S.DEPLOYING
            run.deploy_started_at = timezone.now()
            run.lease_expires_at = _lease_deadline()
            run.output = ssh.bound_output(f"{run.output}{status_output}")
            run.save(
                update_fields=[
                    "status",
                    "deploy_started_at",
                    "lease_expires_at",
                    "output",
                    "updated_at",
                ]
            )
            _audit_step(run, "deploy.started")
    if late_error is not None:
        _finish(run_id, token, [S.CHECKING], S.FAILED, late_error, output=status_output)
        return

    # 4. deploy.
    try:
        result = ssh.run_verb(
            target_fields, "deploy", run.sha, timeout=settings.DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS
        )
    except ssh.SSHConfigError:
        # Refused before anything was spawned: nothing was sent.
        _finish(run_id, token, [S.DEPLOYING], S.FAILED, E.SSH_KEY_MISSING)
        return
    deploy_output = _output_block("deploy", result)
    if result.transport_failed:
        # ssh died or timed out: the verb may or may not have run.
        _finish(
            run_id,
            token,
            [S.DEPLOYING],
            S.UNKNOWN_OUTCOME,
            E.SSH_FAILED,
            current_sha="",
            output=deploy_output,
        )
        return
    if result.exit_code in UNTOUCHED_DEPLOY_EXITS:
        _finish(
            run_id,
            token,
            [S.DEPLOYING],
            S.FAILED,
            UNTOUCHED_DEPLOY_EXITS[result.exit_code],
            output=deploy_output,
        )
        return
    deploy_command_failed = result.exit_code != 0
    _mutate(run_id, token, [S.DEPLOYING], status=S.HEALTH_CHECKING, output_append=deploy_output)

    # 5. health.
    outcome = run_health_phase(run_id, token, plan, phase=PHASE_DEPLOY, status=S.HEALTH_CHECKING)
    if outcome.passed:
        if deploy_command_failed:
            status, error = S.SUCCEEDED_DEGRADED, E.DEPLOY_COMMAND_FAILED
        elif outcome.url_failed:
            status, error = S.SUCCEEDED_DEGRADED, E.HEALTH_URL_FAILED
        else:
            status, error = S.SUCCEEDED, ""
        _finish(run_id, token, [S.HEALTH_CHECKING], status, error, current_sha=run.sha)
        return
    if not plan.get("auto_rollback"):
        _finish(
            run_id, token, [S.HEALTH_CHECKING], S.HEALTH_FAILED, outcome.error, current_sha=run.sha
        )
        return

    # 6. rollback -- only to the decided rollback_sha, only in this run.
    run = _mutate(
        run_id,
        token,
        [S.HEALTH_CHECKING],
        status=S.ROLLING_BACK,
        rollback_started_at=timezone.now(),
    )
    _audit_step(run, "deploy.rollback_started")
    try:
        result = ssh.run_verb(
            target_fields,
            "rollback",
            run.rollback_sha,
            timeout=settings.DEPLOY_SSH_DEPLOY_TIMEOUT_SECONDS,
        )
    except ssh.SSHConfigError:
        _finish(
            run_id,
            token,
            [S.ROLLING_BACK],
            S.ROLLBACK_FAILED,
            E.ROLLBACK_COMMAND_FAILED,
            current_sha="",
        )
        return
    rollback_output = _output_block("rollback", result)
    if not result.ok:
        # A transport failure (ssh timeout / exit 255) leaves the remote's
        # state unknown, unlike a clean refusal -- a distinct code so the
        # banner does not overstate certainty. Either way the live SHA is
        # unknown now: cleared, so the next request needs a status refresh.
        _finish(
            run_id,
            token,
            [S.ROLLING_BACK],
            S.ROLLBACK_FAILED,
            E.ROLLBACK_OUTCOME_UNKNOWN if result.transport_failed else E.ROLLBACK_COMMAND_FAILED,
            current_sha="",
            output=rollback_output,
        )
        return
    _mutate(run_id, token, [S.ROLLING_BACK], output_append=rollback_output)
    rollback_outcome = run_health_phase(
        run_id, token, plan, phase=PHASE_ROLLBACK, status=S.ROLLING_BACK
    )
    if rollback_outcome.passed:
        _finish(
            run_id,
            token,
            [S.ROLLING_BACK],
            S.ROLLED_BACK,
            outcome.error,
            current_sha=run.rollback_sha,
        )
    else:
        # The rollback verb succeeded, so the checkout IS `rollback_sha`;
        # only its health fails. Recording it (rather than clearing to
        # unknown) keeps the drift check exact for the owner's next move.
        _finish(
            run_id,
            token,
            [S.ROLLING_BACK],
            S.ROLLBACK_FAILED,
            E.ROLLBACK_HEALTH_FAILED,
            current_sha=run.rollback_sha,
        )


def resolve_after_unexpected_error(run_id, token):
    """A bug or a Celery time limit mid-run: before the deploy verb nothing
    was touched (`failed`); after it the outcome is unknown and is never
    retried."""
    try:
        _finish(run_id, token, [S.CHECKING], S.FAILED, E.UNEXPECTED_ERROR)
        return
    except FenceLost:
        pass
    try:
        _finish(
            run_id,
            token,
            DeployRun.POST_DEPLOY_ACTIVE_STATUSES,
            S.UNKNOWN_OUTCOME,
            E.UNEXPECTED_ERROR,
            current_sha="",
        )
    except FenceLost:
        pass


# --- recovery ---


def mark_lost_run(run_id):
    """The recovery scan's verdict for a post-deploy run whose lease
    expired: `unknown_outcome`, terminal, never retried."""
    now = timezone.now()
    with transaction.atomic():
        run = (
            DeployRun.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("target", "workspace", "approval_request")
            .filter(
                pk=run_id,
                status__in=list(DeployRun.POST_DEPLOY_ACTIVE_STATUSES),
                lease_expires_at__lt=now,
            )
            .first()
        )
        if run is None:
            return False
        run.status = S.UNKNOWN_OUTCOME
        run.error_code = E.RUNNER_LOST
        run.claim_token = None
        run.lease_expires_at = None
        run.finished_at = now
        run.save(
            update_fields=[
                "status",
                "error_code",
                "claim_token",
                "lease_expires_at",
                "finished_at",
                "updated_at",
            ]
        )
        _set_current_sha(run.target_id, "")
        record_terminal(run)
    return True


def stale_run_ids():
    """(to re-dispatch, lost after the deploy verb)."""
    now = timezone.now()
    grace = now - timedelta(seconds=settings.DEPLOY_PENDING_RECOVERY_GRACE_SECONDS)
    redispatch = list(
        DeployRun.objects.filter(
            Q(status=S.PENDING, updated_at__lt=grace)
            | Q(status=S.CHECKING, deploy_started_at__isnull=True, lease_expires_at__lt=now)
        ).values_list("id", flat=True)
    )
    lost = list(
        DeployRun.objects.filter(
            status__in=list(DeployRun.POST_DEPLOY_ACTIVE_STATUSES), lease_expires_at__lt=now
        ).values_list("id", flat=True)
    )
    return redispatch, lost


# --- status refresh ---


def refresh_target_status(target_id):
    """The runner half of `services.request_target_status`: one read-only
    `status` verb. On success sets `current_sha` to the remote's live SHA
    (never while a run is active on the target)."""
    target = DeployTarget.objects.filter(pk=target_id).select_related("workspace").first()
    if target is None:
        return None
    fields = {
        "ssh_host": target.ssh_host,
        "ssh_port": target.ssh_port,
        "ssh_user": target.ssh_user,
        "host_public_key": target.host_public_key,
        "remote_app": target.remote_app,
    }
    sha = ""
    error = self_repo_error(target.repo_git_url)
    if error is None:
        try:
            result = ssh.run_verb(
                fields, "status", timeout=settings.DEPLOY_SSH_STATUS_TIMEOUT_SECONDS
            )
        except ssh.SSHConfigError:
            error = E.SSH_KEY_MISSING
        else:
            if result.transport_failed:
                error = E.SSH_FAILED
            elif not result.ok:
                error = E.STATUS_FAILED
            else:
                parsed = parse_status(result.output)
                error = check_status(
                    parsed, remote_app=target.remote_app, repo_git_url=target.repo_git_url
                )
                if error is None:
                    sha = parsed["sha"]
    with transaction.atomic():
        locked = DeployTarget.objects.select_for_update().get(pk=target.pk)
        busy = DeployRun.objects.filter(
            target=locked, status__in=DeployRun.ACTIVE_STATUSES
        ).exists()
        locked.last_status_at = timezone.now()
        locked.last_status_error = error or ""
        fields_to_save = ["last_status_at", "last_status_error", "updated_at"]
        if error is None and not busy:
            locked.current_sha = sha
            fields_to_save.append("current_sha")
        locked.save(update_fields=fields_to_save)
        record_audit(
            action="deploy.target.status",
            actor=None,
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=locked.workspace,
            resource=locked,
            metadata={
                "ok": error is None,
                "error_code": error or "",
                "sha": sha,
                "applied": error is None and not busy,
            },
        )
    return error
