"""Pull requests for pushed harness branches (Phase 5 branch 5, brief D7).

`request_pull_request` is the only place the system-only gate
`github_open_pr` is minted (`create_request(..., system_created=True)`),
and only for a `succeeded` `HarnessImplementationJob` -- a branch that went
through Gate A, tests, cross-vendor review and Gate B, and was pushed. The
payload snapshots the repo, `head=branch_name`, `base=repo_default_branch`,
`head_sha=produced_commit_sha`, an owner-editable title and a Friday-
authored body (goal, the model-written diff summary labelled as such, the
test result and the review verdict). The decision stays explicit, separate,
owner-only and web-only (ADR-008 decision 3); at execution the connectors
task re-reads the remote head and fails `head_moved` unless it still equals
`head_sha`.

`resolve_repo_scope` is the harness half of the GitHub scope check,
registered into `connectors.github` at startup (connectors never import
harness, ADR-010).
"""

import json
import re

from django.db import transaction

from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.services import record_audit
from connectors import github
from connectors.manifests.github import GATE_OPEN_PR, PR_BODY_MAX, PR_TITLE_MAX
from connectors.models import ConnectorWriteExecution
from core.channels import CHANNEL_WEB
from core.repo_identity import canonicalize_git_url
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.services import HarnessPermissionDenied, HarnessServiceError, _require_curator
from tools.registry import ReadToolError

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
# Execution states after which the job still "has" its PR (or might):
# a new request would risk a second PR for the same branch.
_BLOCKING_EXECUTION = {
    ConnectorWriteExecution.Status.PENDING,
    ConnectorWriteExecution.Status.RUNNING,
    ConnectorWriteExecution.Status.SUCCEEDED,
    ConnectorWriteExecution.Status.UNKNOWN_OUTCOME,
}


class HarnessPullRequestNotReady(HarnessServiceError):
    """The job has no pushed branch to open a PR for (not `succeeded`, or
    no produced commit) -> 400."""

    code = "harness.pull_request_not_ready"


class HarnessPullRequestExists(HarnessServiceError):
    """A PR request for this job is pending, or approved and not failed
    -> 409."""

    code = "harness.pull_request_exists"


class HarnessGithubUnavailable(HarnessServiceError):
    """Not a github.com repo, no enabled GitHub binding, a tool not pinned
    `ok`, or the repo outside the workspace's scope -> 400."""

    code = "harness.github_unavailable"


def resolve_repo_scope(workspace, canonical):
    """`connectors.github` resolver: the `RepoScope` of the HarnessRepo of
    `workspace` whose remote canonicalizes to `canonical`, else `None`."""
    rows = HarnessRepo.objects.filter(workspace=workspace).values_list(
        "git_url", "sensitive", "default_branch"
    )
    for git_url, sensitive, default_branch in rows:
        if canonicalize_git_url(git_url) == canonical:
            return github.RepoScope(sensitive=sensitive, default_branch=default_branch)
    return None


def _require_web_owner(actor, workspace, channel, action):
    if channel != CHANNEL_WEB:
        raise HarnessPermissionDenied("Pull requests are handled from the web app only.")
    if getattr(actor, "is_agent", False):
        raise HarnessPermissionDenied("Agent accounts cannot handle pull requests.")
    _require_curator(actor, workspace, action)


def default_title(job):
    first = next((line.strip() for line in job.goal.splitlines() if line.strip()), "")
    return clean_title(first) or f"Friday harness job {job.id}"


def clean_title(title):
    return _CONTROL_RE.sub(" ", title or "").strip()[:PR_TITLE_MAX]


def _review_line(job):
    review = (
        job.reviews.filter(status=HarnessReview.Status.SUCCEEDED).order_by("-created_at").first()
    )
    if review is None:
        return "no completed review recorded"
    return f"{review.verdict or 'unknown'} (reviewer: {review.agent}, implementer: {job.agent})"


def _test_line(job):
    result = job.test_result or {}
    if not result:
        return "no test result recorded"
    passed = result.get("passed") is True
    return f"{'passed' if passed else 'failed'} (exit code {result.get('exit_code')!s})"


def build_body(job):
    """Friday-authored PR body. The diff summary is model-written and
    labelled so; everything else is Friday's own bookkeeping."""
    body = "\n".join(
        [
            "## Goal",
            "",
            job.goal.strip(),
            "",
            "## Change summary (model-written, not verified by Friday)",
            "",
            (job.diff_summary or "").strip() or "(none)",
            "",
            "## Tests",
            "",
            _test_line(job),
            "",
            "## Review",
            "",
            _review_line(job),
            "",
            "---",
            f"Opened by Friday for harness implementation job `{job.id}` at commit "
            f"`{job.produced_commit_sha}`, after an explicit owner decision.",
        ]
    )
    return body[:PR_BODY_MAX]


def _blocking(approval_request):
    if approval_request is None:
        return False
    if approval_request.status == ApprovalRequest.Status.PENDING:
        return True
    if approval_request.status != ApprovalRequest.Status.APPROVED:
        return False
    execution = ConnectorWriteExecution.objects.filter(approval_request=approval_request).first()
    return execution is None or execution.status in _BLOCKING_EXECUTION


def request_pull_request(*, job, actor, channel, title=None):
    """Mint `github_open_pr` for a `succeeded` job; returns the pending
    `ApprovalRequest`. Refuses before any row is written."""
    workspace = job.workspace
    _require_web_owner(actor, workspace, channel, "opening a pull request")
    with transaction.atomic():
        locked = (
            HarnessImplementationJob.objects.select_for_update(of=("self",))
            .filter(pk=job.pk, workspace=workspace)
            .select_related("pull_request_request")
            .first()
        )
        if locked is None:
            raise HarnessPermissionDenied("Job no longer exists.")
        if locked.status != HarnessImplementationJob.Status.SUCCEEDED:
            raise HarnessPullRequestNotReady("Only a succeeded (pushed) job can get a PR.")
        head_sha = (locked.produced_commit_sha or "").lower()
        if not locked.branch_name or not _SHA_RE.match(head_sha):
            raise HarnessPullRequestNotReady("The job has no pushed commit.")
        if _blocking(locked.pull_request_request):
            raise HarnessPullRequestExists("A pull request for this job is already requested.")
        remote = github.split_github_remote(locked.repo_git_url)
        if remote is None:
            raise HarnessGithubUnavailable("The job's repository is not on github.com.")
        if not github.workspace_has_github(workspace):
            raise HarnessGithubUnavailable("GitHub is not enabled for this workspace.")
        owner, repo = remote
        chosen = clean_title(title) if title is not None else default_title(locked)
        if not chosen:
            raise HarnessPullRequestNotReady("The pull request title is empty.")
        payload = {
            "arguments": {
                "owner": owner,
                "repo": repo,
                "title": chosen,
                "body": build_body(locked),
                "head": locked.branch_name,
                "base": locked.repo_default_branch or "main",
                "draft": False,
                "maintainer_can_modify": False,
                "reviewers": [],
            },
            "context": {
                "head_sha": head_sha,
                "origin_ref": f"harness_implementation_job:{locked.id}",
            },
        }
        try:
            approval_request = create_request(
                workspace=workspace,
                actor=actor,
                tool_name=GATE_OPEN_PR,
                payload=payload,
                # The owner clicked "open PR": who produced the request.
                origin=ApprovalRequest.Origin.HUMAN,
                channel=channel,
                # ADR-010 decision 2: minted only here, by its owning service.
                system_created=True,
            )
        except InvalidTransition as exc:
            raise HarnessGithubUnavailable(str(exc)) from exc
        locked.pull_request_request = approval_request
        locked.save(update_fields=["pull_request_request", "updated_at"])
        record_audit(
            action="harness.pull_request.requested",
            actor=actor,
            workspace=workspace,
            resource=locked,
            metadata={"approval_request": str(approval_request.id), "head_sha": head_sha},
        )
    return approval_request


def _pull_request_ref(execution):
    if execution is None or execution.status != ConnectorWriteExecution.Status.SUCCEEDED:
        return None
    try:
        data = json.loads((execution.result or {}).get("text") or "")
    except (TypeError, ValueError):
        data = None
    return github.pull_request_ref(data)


def pull_request_state(*, job, actor, channel):
    """The job's PR panel: its latest `github_open_pr` request and
    execution, the opened PR's number/URL, and the live CI state of the
    pushed commit (an audited `github_ci_status` read; any refusal or
    failure shows as `unknown` with its closed-vocabulary code)."""
    workspace = job.workspace
    _require_web_owner(actor, workspace, channel, "viewing this pull request")
    approval_request = job.pull_request_request
    execution = (
        ConnectorWriteExecution.objects.filter(approval_request=approval_request).first()
        if approval_request is not None
        else None
    )
    state = {
        "job_status": job.status,
        "head": job.branch_name,
        "base": job.repo_default_branch,
        "head_sha": job.produced_commit_sha,
        "can_request": job.status == HarnessImplementationJob.Status.SUCCEEDED
        and not _blocking(approval_request),
        "request": None,
        "execution": None,
        "pull_request": _pull_request_ref(execution),
        "ci": None,
    }
    if approval_request is not None:
        state["request"] = {
            "id": str(approval_request.id),
            "status": approval_request.status,
            "created_at": approval_request.created_at.isoformat(),
        }
    if execution is not None:
        state["execution"] = {
            "status": execution.status,
            "error_type": execution.error_type,
            "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
        }
    remote = github.split_github_remote(job.repo_git_url)
    if (
        job.status == HarnessImplementationJob.Status.SUCCEEDED
        and remote is not None
        and _SHA_RE.match((job.produced_commit_sha or "").lower())
        and github.workspace_has_github(workspace)
    ):
        try:
            state["ci"] = github.read_ci(
                workspace=workspace,
                actor=actor,
                owner=remote[0],
                repo=remote[1],
                head_sha=job.produced_commit_sha.lower(),
                branch=job.branch_name,
                channel=channel,
            )
        except ReadToolError as exc:
            state["ci"] = {"state": "unknown", "runs": [], "error": exc.code}
    return state
