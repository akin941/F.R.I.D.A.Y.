"""Deploy services (Phase 5 branch 6, ADR-010 decision 7, brief D10).

Owner-only throughout (`User.is_owner`, human, owner of the workspace):
- targets are created and edited over the web channel only;
- `request_deploy` runs over web or CLI and mints the system-created
  `deploy` gate (`create_request(..., system_created=True)`, ADR-010
  decision 2) -- the only call site that may, pinned by grep in tests;
- a run can be cancelled (web only) only before the `deploy` verb;
- a `status` refresh (web only) asks the runner for the remote's live SHA,
  the "first status" of the target-setup runbook.

The deploy decision itself stays in `approvals` (explicit, separate,
audited, owner-only, web-only). Nothing here decides anything.
"""

import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from approvals.models import ApprovalRequest
from approvals.services import create_request
from audit.services import record_audit
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from core.repo_identity import is_self_repo
from deploys.models import CONFIG_HASH_FIELDS, DeployRun, DeployTarget
from deploys.validation import TargetFieldError, normalize_sha
from workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)

DEPLOY_TOOL_NAME = "deploy"
PAYLOAD_VERSION = 1
HEALTH_PASS_THRESHOLD = 2
HEALTH_FAIL_THRESHOLD = 3

CI_SUCCESS = "success"
CI_UNKNOWN = "unknown"
CI_CONCLUSIONS = frozenset({CI_SUCCESS, "failure", "pending", CI_UNKNOWN})
COMMIT_SOURCE_MANUAL = "manual"
COMMIT_TITLE_MAX_CHARS = 200

_REQUEST_CHANNELS = frozenset({CHANNEL_WEB, CHANNEL_CLI})


class DeployServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class DeployPermissionDenied(DeployServiceError):
    code = "deploy.permission_denied"


class DeployInvalidTransition(DeployServiceError):
    code = "deploy.invalid_transition"


class DeployCiRequired(DeployInvalidTransition):
    """The target requires a green CI run for the SHA and none is known --
    including the manual-SHA path, whose CI conclusion is `unknown`."""

    code = "deploy.ci_required"


def _require_owner(actor, workspace, action):
    if not (
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_owner", False)
        and actor.is_active
        and not getattr(actor, "is_agent", False)
    ):
        raise DeployPermissionDenied(f"Only the installation owner can do this ({action}).")
    role = (
        WorkspaceMember.objects.filter(workspace=workspace, user=actor)
        .values_list("role", flat=True)
        .first()
    )
    if role != WorkspaceMember.Role.OWNER:
        raise DeployPermissionDenied(f"Only the workspace owner can do this ({action}).")


def _require_web(channel, action):
    if channel != CHANNEL_WEB:
        raise DeployPermissionDenied(f"{action} is only available from the web app.")


def self_repo_error(repo_git_url):
    """The ADR-004 self-modification guard, applied to deploys (brief D10
    out-of-scope: "deploying Jarvis itself"). Fails closed with no
    identifiers configured, exactly like harness implementation jobs.
    Returns an error code or None."""
    if not settings.HARNESS_SELF_REPO_IDENTIFIERS:
        return DeployRun.ErrorCode.SELF_REPO_UNCONFIGURED
    if is_self_repo(repo_git_url, self_repo_identifiers=settings.HARNESS_SELF_REPO_IDENTIFIERS):
        return DeployRun.ErrorCode.SELF_REPO
    return None


_SELF_REPO_MESSAGES = {
    DeployRun.ErrorCode.SELF_REPO_UNCONFIGURED: (
        "HARNESS_SELF_REPO_IDENTIFIERS is not configured; deploys are refused until this "
        "installation's own remote(s) are set (ADR-004 self-modification guard)."
    ),
    DeployRun.ErrorCode.SELF_REPO: (
        "This repo resolves to Jarvis's own installation; deploying Jarvis itself is refused."
    ),
}


# --- targets ---


def create_target(*, workspace, actor, channel, **fields):
    """`fields` are already validated by the serializer
    (`deploys.validation`)."""
    _require_web(channel, "Creating a deploy target")
    _require_owner(actor, workspace, "creating a deploy target")
    if is_self_repo(
        fields.get("repo_git_url", ""),
        self_repo_identifiers=settings.HARNESS_SELF_REPO_IDENTIFIERS,
    ):
        raise DeployInvalidTransition(_SELF_REPO_MESSAGES[DeployRun.ErrorCode.SELF_REPO])
    with transaction.atomic():
        if DeployTarget.objects.filter(workspace=workspace, name=fields.get("name")).exists():
            raise DeployInvalidTransition("A deploy target with this name already exists.")
        target = DeployTarget.objects.create(workspace=workspace, created_by=actor, **fields)
        record_audit(
            action="deploy.target.created",
            actor=actor,
            workspace=workspace,
            resource=target,
            metadata={
                "name": target.name,
                "environment": target.environment,
                "remote_app": target.remote_app,
                "enabled": target.enabled,
                "config_hash": target.config_hash,
            },
        )
    return target


_EDITABLE_FIELDS = frozenset(CONFIG_HASH_FIELDS) | {"enabled"}


def edit_target(*, target, actor, channel, **fields):
    _require_web(channel, "Editing a deploy target")
    _require_owner(actor, target.workspace, "editing a deploy target")
    unknown = set(fields) - _EDITABLE_FIELDS
    if unknown:
        raise DeployInvalidTransition(f"Not editable: {sorted(unknown)}.")
    if "repo_git_url" in fields and is_self_repo(
        fields["repo_git_url"], self_repo_identifiers=settings.HARNESS_SELF_REPO_IDENTIFIERS
    ):
        raise DeployInvalidTransition(_SELF_REPO_MESSAGES[DeployRun.ErrorCode.SELF_REPO])
    with transaction.atomic():
        locked = DeployTarget.objects.select_for_update().get(pk=target.pk)
        if (
            "name" in fields
            and fields["name"] != locked.name
            and DeployTarget.objects.filter(workspace=locked.workspace_id, name=fields["name"])
            .exclude(pk=locked.pk)
            .exists()
        ):
            raise DeployInvalidTransition("A deploy target with this name already exists.")
        before = locked.config_hash
        changed = sorted(name for name, value in fields.items() if getattr(locked, name) != value)
        for name, value in fields.items():
            setattr(locked, name, value)
        locked.save()
        record_audit(
            action="deploy.target.updated",
            actor=actor,
            workspace=locked.workspace,
            resource=locked,
            metadata={
                "changed": changed,
                "config_changed": before != locked.config_hash,
                "enabled": locked.enabled,
                "config_hash": locked.config_hash,
            },
        )
    return locked


def request_target_status(*, target, actor, channel):
    """Queues a `status` verb on the runner (read-only on the VPS). Its
    result sets `current_sha` -- the first status of a new target, or a
    re-sync after `unknown_outcome`/`rollback_failed`/drift."""
    _require_web(channel, "Refreshing a deploy target's status")
    _require_owner(actor, target.workspace, "refreshing a deploy target's status")
    if DeployRun.objects.filter(target=target, status__in=DeployRun.ACTIVE_STATUSES).exists():
        raise DeployInvalidTransition("A deploy is running on this target; try again after it.")
    record_audit(
        action="deploy.target.status_requested",
        actor=actor,
        workspace=target.workspace,
        resource=target,
        metadata={"channel": channel},
    )
    target_id = str(target.id)
    transaction.on_commit(lambda: dispatch_status_refresh(target_id))


def dispatch_status_refresh(target_id):
    from deploys.tasks import refresh_deploy_target_status

    if settings.CELERY_TASK_ALWAYS_EAGER:
        refresh_deploy_target_status.delay(target_id)
        return
    try:
        refresh_deploy_target_status.delay(target_id)
    except Exception:
        logger.exception("Failed to publish status refresh for deploy target %s", target_id)


# --- the deploy request (gate) ---


@dataclass(frozen=True)
class CommitSnapshot:
    """What the decider is told about the commit, captured at request
    time. `title` is repo text (low-trust, shown labelled as such);
    `ci_conclusion` one of `CI_CONCLUSIONS`."""

    title: str
    ci_conclusion: str
    source: str


def resolve_commit_snapshot(target, sha):
    """The seam branch 5 (`feat/p5-github`) wires: when the workspace has a
    GitHub binding, the commit title and CI conclusion come from
    `connectors` reads (`github_*`) for `target.repo_git_url` at `sha`.
    Until then -- and for any target without such a binding -- this is the
    manual-SHA path: no title, CI `unknown`, so a target with
    `require_ci_success` refuses the request (brief D10)."""
    return CommitSnapshot(title="", ci_conclusion=CI_UNKNOWN, source=COMMIT_SOURCE_MANUAL)


_TITLE_STRIP_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_title(title):
    title = _TITLE_STRIP_RE.sub(" ", title or "").strip()
    return title[:COMMIT_TITLE_MAX_CHARS]


def health_plan_for(target):
    checks = max(3, settings.DEPLOY_HEALTH_CHECKS_PER_WINDOW)
    window = target.health_timeout_seconds
    return {
        "window_seconds": window,
        "checks": checks,
        "interval_seconds": round(window / checks, 3),
        "pass_threshold": HEALTH_PASS_THRESHOLD,
        "fail_threshold": HEALTH_FAIL_THRESHOLD,
        "health_url": target.health_url,
    }


def build_payload(target, sha, snapshot):
    """Everything the decider sees and the runner acts on (brief D10):
    target fields + `config_hash`, SHA, commit title (repo text) and CI
    conclusion at request time, `expected_current_sha`, `rollback_sha`,
    health plan and verb sequence."""
    verbs = ["status", "deploy", "health"]
    if target.auto_rollback:
        verbs += ["rollback", "health"]
    return {
        "version": PAYLOAD_VERSION,
        "target_id": str(target.id),
        "config_hash": target.config_hash,
        "target": {field: getattr(target, field) for field in CONFIG_HASH_FIELDS},
        "sha": sha,
        "commit": {
            "title": _clean_title(snapshot.title),
            "ci_conclusion": snapshot.ci_conclusion,
            "source": snapshot.source,
        },
        "expected_current_sha": target.current_sha,
        # The SHA the drift check will prove live -- the only state an
        # automatic rollback can ever restore (ADR-010 decision 7).
        "rollback_sha": target.current_sha,
        "auto_rollback": target.auto_rollback,
        "health_plan": health_plan_for(target),
        "verbs": verbs,
    }


def request_deploy(*, workspace, actor, target, sha, channel):
    """Mints the `deploy` gate for `sha` on `target`. Returns the pending
    `ApprovalRequest`. Refuses (DeployInvalidTransition / DeployCiRequired)
    before any row is written: a disabled target, the self-repo guard, an
    unknown live SHA (run `status` first), the SHA already live, or a
    required CI conclusion that is not `success`."""
    if channel not in _REQUEST_CHANNELS:
        raise DeployPermissionDenied("Deploys can be requested only from the web app or the CLI.")
    _require_owner(actor, workspace, "requesting a deploy")
    if target.workspace_id != workspace.id:
        raise DeployPermissionDenied("Target does not belong to this workspace.")
    try:
        sha = normalize_sha(sha)
    except TargetFieldError as exc:
        raise DeployInvalidTransition(str(exc)) from exc
    with transaction.atomic():
        locked = DeployTarget.objects.select_for_update().get(pk=target.pk)
        if not locked.enabled:
            raise DeployInvalidTransition("Deploy target is not enabled.")
        guard = self_repo_error(locked.repo_git_url)
        if guard is not None:
            raise DeployInvalidTransition(_SELF_REPO_MESSAGES[guard])
        if not locked.current_sha:
            raise DeployInvalidTransition(
                "The target's live SHA is unknown; run a status check on it first."
            )
        if sha == locked.current_sha:
            raise DeployInvalidTransition("This SHA is already live on the target.")
        snapshot = resolve_commit_snapshot(locked, sha)
        if snapshot.ci_conclusion not in CI_CONCLUSIONS:
            snapshot = CommitSnapshot(snapshot.title, CI_UNKNOWN, snapshot.source)
        if locked.require_ci_success and snapshot.ci_conclusion != CI_SUCCESS:
            raise DeployCiRequired(
                f"This target requires a successful CI run for the commit; its CI state is "
                f"{snapshot.ci_conclusion!r}."
            )
        approval_request = create_request(
            workspace=workspace,
            actor=actor,
            tool_name=DEPLOY_TOOL_NAME,
            payload=build_payload(locked, sha, snapshot),
            # The owner typed or clicked it: who produced the request.
            origin=ApprovalRequest.Origin.HUMAN,
            channel=channel,
            # ADR-010 decision 2: `deploy` is a system-only gate, minted
            # only here, by its owning service.
            system_created=True,
        )
        record_audit(
            action="deploy.requested",
            actor=actor,
            workspace=workspace,
            resource=locked,
            metadata={
                "approval_request": str(approval_request.id),
                "sha": sha,
                "expected_current_sha": locked.current_sha,
                "ci_conclusion": snapshot.ci_conclusion,
                "commit_source": snapshot.source,
                "channel": channel,
            },
        )
    return approval_request


# --- cancel ---


def cancel_deploy_run(*, run, actor, channel):
    """Only before the `deploy` verb (brief D10): a `pending` run, or one
    still in its `status`/drift check. The runner's fenced transition to
    `deploying` and this cancel serialize on the row lock; whichever
    commits first wins, so a cancel that returns success guarantees the
    deploy verb is never sent."""
    from deploys.outcomes import record_terminal

    _require_web(channel, "Cancelling a deploy")
    _require_owner(actor, run.workspace, "cancelling a deploy")
    with transaction.atomic():
        locked = DeployRun.objects.select_for_update().select_related("target").get(pk=run.pk)
        cancellable = locked.status == DeployRun.Status.PENDING or (
            locked.status == DeployRun.Status.CHECKING and locked.deploy_started_at is None
        )
        if not cancellable:
            raise DeployInvalidTransition(
                "A deploy can be cancelled only before the deploy command is sent."
            )
        locked.status = DeployRun.Status.CANCELLED
        locked.claim_token = None
        locked.lease_expires_at = None
        locked.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
        record_terminal(locked, actor=actor)
    return locked
