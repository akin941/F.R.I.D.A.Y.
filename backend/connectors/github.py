"""GitHub connector support (Phase 5 branch 5, brief D7): the repository
scope check, per-call data classification and the Jarvis-internal reads the
harness PR panel and the deploy snapshot use.

**Scope.** Every GitHub call names an `owner/repo`. Before any network
traffic, `github.com/<owner>/<repo>` is canonicalized with
`core.repo_identity.canonicalize_git_url` and must equal the canonical
remote of a `HarnessRepo` of the *calling* workspace -- firm A never reads
firm B's repositories, and a repo nobody registered is never reachable
even if the PAT could see it. `connectors` may not import `harness`
(ADR-010: harness stays a leaf), so the harness registers the lookup at
startup through `register_repo_resolver`; with no resolver registered every
call is out of scope (fail closed).

**Data class.** A `sensitive` `HarnessRepo` makes that call `personal`
(`classify`): web-only, personal-data providers only (D5).
"""

import re
from dataclasses import dataclass

from core.repo_identity import canonicalize_git_url

CONNECTOR = "github"
GITHUB_HOST = "github.com"
# GitHub's own limits: a login is 1-39 alphanumerics or single hyphens; a
# repository name is 1-100 of [A-Za-z0-9._-], never `.` or `..`.
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A git ref Jarvis sends as a branch name: conservative subset of
# `git check-ref-format` (no `..`, no leading `-`/`/`, no control chars).
BRANCH_RE = re.compile(r"^(?!-)(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,255}(?<!/)(?<!\.lock)$")


@dataclass(frozen=True)
class RepoScope:
    """What the harness tells connectors about an in-scope repository."""

    sensitive: bool
    default_branch: str


_repo_resolver = None


def register_repo_resolver(resolver):
    """`resolver(workspace, canonical_remote) -> RepoScope | None`, set once
    by `harness.apps.HarnessConfig.ready` (and by tests)."""
    global _repo_resolver
    _repo_resolver = resolver


def get_repo_resolver():
    return _repo_resolver


def canonical_remote(owner, repo):
    return canonicalize_git_url(f"https://{GITHUB_HOST}/{owner}/{repo}")


def split_github_remote(git_url):
    """`(owner, repo)` of a github.com remote in any spelling (scp-like,
    https, ssh.github.com, `.git` suffix), else `None`."""
    canonical = canonicalize_git_url(git_url or "")
    parts = canonical.split("/")
    if len(parts) != 3 or parts[0] != GITHUB_HOST:
        return None
    owner, repo = parts[1], parts[2]
    if not OWNER_RE.match(owner) or not REPO_RE.match(repo) or repo in {".", ".."}:
        return None
    return owner, repo


def resolve_scope(workspace, owner, repo):
    """The calling workspace's `RepoScope` for `owner/repo`, or `None`."""
    if _repo_resolver is None or workspace is None:
        return None
    if not isinstance(owner, str) or not isinstance(repo, str):
        return None
    return _repo_resolver(workspace, canonical_remote(owner, repo))


def scope_check(workspace, args):
    """`ToolPolicy.scope_check` of every GitHub tool (runs before any call)."""
    from connectors.manifest import ScopeError

    if resolve_scope(workspace, args.get("owner"), args.get("repo")) is None:
        raise ScopeError("repository is not registered in this workspace")


def classify(workspace, args):
    """`ToolPolicy.classify`: `personal` for a `sensitive` repo."""
    scope = resolve_scope(workspace, args.get("owner"), args.get("repo"))
    return "personal" if scope is not None and scope.sensitive else "firm"


# --- result shapes (parsed defensively: server JSON is low-trust) ---

_PR_URL_RE = re.compile(r"/pull/(\d+)(?:$|[/?#])")
_CI_FAILURES = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}


def _str(value, limit=500):
    return value[:limit] if isinstance(value, str) else ""


def pull_request_ref(data):
    """`{"number", "url"}` from `create_pull_request`'s (or
    `pull_request_read get`'s) JSON; missing fields empty."""
    if not isinstance(data, dict):
        return {"number": None, "url": ""}
    url = _str(data.get("html_url")) or _str(data.get("url"))
    number = data.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        match = _PR_URL_RE.search(url)
        number = int(match.group(1)) if match else None
    if url and not url.startswith(f"https://{GITHUB_HOST}/"):
        url = ""
    return {"number": number, "url": url}


def commit_sha(data):
    """The SHA of `get_commit`'s JSON, lowercased, or `""`."""
    if not isinstance(data, dict):
        return ""
    sha = _str(data.get("sha"), 64).lower()
    return sha if SHA_RE.match(sha) else ""


def commit_title(data):
    """First line of the commit message (repo text, low-trust)."""
    if not isinstance(data, dict):
        return ""
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    message = _str(commit.get("message"), 4000) or _str(data.get("message"), 4000)
    return message.splitlines()[0].strip() if message.strip() else ""


def ci_state(data, *, head_sha=""):
    """Collapse `actions_list list_workflow_runs` JSON into one conclusion:
    `success` | `failure` | `pending` | `unknown`, plus bounded run rows.
    Only runs for `head_sha` count when it is given."""
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    rows = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        sha = _str(run.get("head_sha"), 64).lower()
        if head_sha and sha != head_sha:
            continue
        rows.append(
            {
                "name": _str(run.get("name"), 200),
                "status": _str(run.get("status"), 40),
                "conclusion": _str(run.get("conclusion"), 40),
                "url": _str(run.get("html_url")),
            }
        )
    rows = rows[:20]
    if not rows:
        state = "unknown"
    elif any(row["conclusion"] in _CI_FAILURES for row in rows):
        state = "failure"
    elif any(row["status"] != "completed" for row in rows):
        state = "pending"
    elif all(row["conclusion"] in {"success", "skipped", "neutral"} for row in rows):
        state = "success"
    else:
        state = "unknown"
    return {"state": state, "runs": rows}


# --- Jarvis-internal reads (same refusals, pins and audit as chat reads) ---


def _read(workspace, actor, tool, args, channel):
    from connectors.manifest import get_manifest
    from connectors.reads import perform_read

    manifest = get_manifest(CONNECTOR)
    policy = manifest.get_tool(tool) if manifest is not None else None
    if policy is None:
        from tools.registry import ReadToolError

        raise ReadToolError("not_available")
    return perform_read(
        manifest,
        policy,
        workspace=workspace,
        actor=actor,
        args=args,
        channel=channel,
        as_data=True,
    ).structured


def read_ci(*, workspace, actor, owner, repo, head_sha, branch="", channel):
    """CI conclusion of `head_sha` (optionally on `branch`). Raises
    `tools.registry.ReadToolError` on any refusal/failure."""
    args = {"owner": owner, "repo": repo}
    if branch:
        args["workflow_runs_filter"] = {"branch": branch}
    return ci_state(_read(workspace, actor, "github_ci_status", args, channel), head_sha=head_sha)


def read_commit(*, workspace, actor, owner, repo, ref, channel):
    """`get_commit` JSON for a SHA or branch name (its `sha` argument)."""
    return _read(
        workspace, actor, "github_branch_head", {"owner": owner, "repo": repo, "sha": ref}, channel
    )


def workspace_has_github(workspace):
    from connectors.services import enabled_binding

    return enabled_binding(workspace, CONNECTOR) is not None
