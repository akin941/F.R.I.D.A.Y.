"""GitHub -- the official `github-mcp-server` (Phase 5 branch 5, brief D7).

Sidecar: `ghcr.io/github/github-mcp-server:v1.12.2` pinned by digest, run in
**stdio mode inside its own sidecar behind our stdio<->HTTP bridge** (the
server's HTTP mode takes the token from each request's `Authorization`
header, i.e. Friday would hold the PAT -- D1 forbids it). The PAT lives in
the sidecar's env only (`GITHUB_PERSONAL_ACCESS_TOKEN`); the server starts
with `--tools=` naming only the server tools below, and the bridge's
`BRIDGE_ALLOWED_TOOLS` repeats the list, so merge, file, branch, issue and
workflow-trigger tools are not even registered. Three ceilings: server
`--tools`, this manifest, PAT scope (`contents:read`, `pull_requests:write`,
`actions:read`, `metadata:read`, registered repos only).

Every tool's scope check canonicalizes `owner/repo` to a `HarnessRepo` of
the calling workspace before any call (`connectors.github.scope_check`);
a `sensitive` repo makes that call `personal` (`classify`).

The one write, `create_pull_request`, is the system-only gate
`github_open_pr` (`user_invocable=False`): minted only by the harness from
a `succeeded` implementation job. `reviewers` and `maintainer_can_modify`
are fixed by Friday (empty / false) -- the serializer refuses anything else
-- and shown in the dialog. Before the send the task re-reads the branch
head and fails `head_moved` unless it still equals the approved SHA.

Schema pins: the `inputSchema`s below are recorded for v1.12.2 as this
branch's reconstruction; the pin check on enable compares them with the
live server's `tools/list` and disables any tool that differs (fail
closed) until a reviewed commit updates the pin (`docs/mcp/github.md`).
"""

from rest_framework import serializers

from connectors import github
from connectors.manifest import (
    KIND_MCP,
    STATUS_ACTIVE,
    TOOL_READ,
    TOOL_WRITE,
    WORKSPACE_COMPANY,
    WORKSPACE_PERSONAL,
    ConnectorManifest,
    ToolPolicy,
    schema_hash,
)
from core.channels import CHANNEL_CLI, CHANNEL_WEB

SIDECAR_IMAGE = (
    "ghcr.io/github/github-mcp-server:v1.12.2"
    "@sha256:508a0857ec762b1ab1cece29193345b501fab1dd9d1228a7b617062954cecac6"
)
GATE_OPEN_PR = "github_open_pr"
PR_TITLE_MAX = 256
PR_BODY_MAX = 60000

_OWNER = {"type": "string", "description": "Repository owner"}
_REPO = {"type": "string", "description": "Repository name"}

# The server tools' `inputSchema`s as pinned (v1.12.2). The pin is the
# sha256 of each schema's canonical JSON (`connectors.manifest.schema_hash`).
SERVER_SCHEMAS = {
    "pull_request_read": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": [
                    "get",
                    "get_diff",
                    "get_status",
                    "get_files",
                    "get_review_comments",
                    "get_reviews",
                    "get_comments",
                    "get_check_runs",
                ],
            },
            "owner": _OWNER,
            "repo": _REPO,
            "pullNumber": {"type": "number", "description": "Pull request number"},
            "page": {"type": "number", "minimum": 1},
            "perPage": {"type": "number", "minimum": 1, "maximum": 100},
        },
        "required": ["method", "owner", "repo", "pullNumber"],
    },
    "actions_list": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": [
                    "list_workflows",
                    "list_workflow_runs",
                    "list_workflow_jobs",
                    "list_workflow_run_artifacts",
                ],
            },
            "owner": _OWNER,
            "repo": _REPO,
            "resource_id": {"type": "string"},
            "workflow_runs_filter": {
                "type": "object",
                "properties": {
                    "actor": {"type": "string"},
                    "branch": {"type": "string"},
                    "event": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
            "page": {"type": "number", "minimum": 1},
            "per_page": {"type": "number", "minimum": 1, "maximum": 100},
        },
        "required": ["method", "owner", "repo"],
    },
    "get_commit": {
        "type": "object",
        "properties": {
            "owner": _OWNER,
            "repo": _REPO,
            "sha": {"type": "string", "description": "Commit SHA, branch name, or tag name"},
            "include_diff": {"type": "boolean", "default": True},
            "page": {"type": "number", "minimum": 1},
            "perPage": {"type": "number", "minimum": 1, "maximum": 100},
        },
        "required": ["owner", "repo", "sha"],
    },
    "create_pull_request": {
        "type": "object",
        "properties": {
            "owner": _OWNER,
            "repo": _REPO,
            "title": {"type": "string"},
            "body": {"type": "string"},
            "head": {"type": "string"},
            "base": {"type": "string"},
            "draft": {"type": "boolean"},
            "maintainer_can_modify": {"type": "boolean"},
            "reviewers": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["owner", "repo", "title", "head", "base"],
    },
}
SERVER_TOOLS = tuple(SERVER_SCHEMAS)


class _RepoArgs(serializers.Serializer):
    owner = serializers.RegexField(github.OWNER_RE, max_length=39)
    repo = serializers.RegexField(github.REPO_RE, max_length=100)

    def validate_repo(self, value):
        if value in {".", ".."}:
            raise serializers.ValidationError("invalid repository name")
        return value


class PullRequestStatusArgs(_RepoArgs):
    # Methods restricted to metadata/status (no diff, files or comments --
    # attacker-authored text Friday has no use for).
    method = serializers.ChoiceField(
        choices=["get", "get_status", "get_check_runs"], required=False, default="get"
    )
    pullNumber = serializers.IntegerField(min_value=1, max_value=10**9)  # noqa: N815


class _RunsFilter(serializers.Serializer):
    branch = serializers.RegexField(github.BRANCH_RE, max_length=255)


class CiStatusArgs(_RepoArgs):
    method = serializers.ChoiceField(
        choices=["list_workflow_runs"], required=False, default="list_workflow_runs"
    )
    workflow_runs_filter = _RunsFilter(required=False)
    per_page = serializers.IntegerField(min_value=1, max_value=50, required=False, default=30)


class BranchHeadArgs(_RepoArgs):
    # A branch name or a 40-hex commit SHA; never with the diff.
    sha = serializers.RegexField(github.BRANCH_RE, max_length=255)
    include_diff = serializers.BooleanField(required=False, default=False)

    def validate_include_diff(self, value):
        if value:
            raise serializers.ValidationError("diffs are not read")
        return False


class OpenPullRequestArgs(_RepoArgs):
    title = serializers.CharField(max_length=PR_TITLE_MAX, trim_whitespace=True)
    body = serializers.CharField(max_length=PR_BODY_MAX, trim_whitespace=False)
    head = serializers.RegexField(github.BRANCH_RE, max_length=255)
    base = serializers.RegexField(github.BRANCH_RE, max_length=255)
    draft = serializers.BooleanField(required=False, default=False)
    # Fixed by Friday (brief Step-0): nobody is auto-requested, and the
    # head branch is not opened to upstream maintainers.
    maintainer_can_modify = serializers.BooleanField(required=False, default=False)
    reviewers = serializers.ListField(
        child=serializers.CharField(max_length=39), required=False, default=list, max_length=0
    )

    def validate_maintainer_can_modify(self, value):
        if value:
            raise serializers.ValidationError("fixed to false")
        return False

    def validate(self, attrs):
        if attrs["head"] == attrs["base"]:
            raise serializers.ValidationError({"head": "head equals base"})
        return attrs


class OpenPullRequestContext(serializers.Serializer):
    """Snapshot beyond the arguments: the SHA the owner approves (checked
    against the remote head before the send) and the opaque back-reference
    of the harness job that produced it (ADR-010: connectors never import
    harness; the link is a string)."""

    head_sha = serializers.RegexField(github.SHA_RE)
    origin_ref = serializers.RegexField(r"^[a-z_]{1,40}:[0-9a-f-]{36}$")


def render_open_pr(arguments):
    return [
        {"label": "Depo", "value": f"{arguments['owner']}/{arguments['repo']}"},
        {"label": "Dal (head)", "value": arguments["head"]},
        {"label": "Hedef dal (base)", "value": arguments["base"]},
        {"label": "Başlık", "value": arguments["title"]},
        {"label": "Açıklama", "value": arguments["body"]},
        {"label": "Taslak", "value": "evet" if arguments.get("draft") else "hayır"},
        {"label": "İnceleyiciler (sabit)", "value": "(yok)"},
        {"label": "maintainer_can_modify (sabit)", "value": "false"},
    ]


def head_unmoved(manifest, binding, payload):
    """`ToolPolicy.precondition` of `github_open_pr`: the remote head of
    `head` must still be the approved `head_sha`. Any failure to confirm it
    refuses the send (nothing is sent either way)."""
    from django.conf import settings

    from connectors import mcp_client
    from connectors.reads import parse_json_result
    from connectors.services import tool_available

    policy = manifest.get_tool("github_branch_head")
    if not tool_available(binding, policy):
        return "pin_mismatch"
    arguments = payload.get("arguments") or {}
    expected = (payload.get("context") or {}).get("head_sha", "")
    try:
        result = mcp_client.call_tool(
            manifest,
            server_tool=policy.server_tool,
            arguments={
                "owner": arguments.get("owner"),
                "repo": arguments.get("repo"),
                "sha": arguments.get("head"),
                "include_diff": False,
            },
            schema_sha256=policy.schema_sha256,
            timeout=settings.CONNECTOR_READ_TIMEOUT_SECONDS,
        )
    except mcp_client.ConnectorError as exc:
        return {"tool_missing": "pin_mismatch"}.get(exc.code, exc.code)
    if result.is_error:
        return "tool_error"
    actual = github.commit_sha(parse_json_result(result))
    if not expected or actual != expected:
        return "head_moved"
    return None


def _read(name, server_tool, description, serializer):
    return ToolPolicy(
        name=name,
        server_tool=server_tool,
        kind=TOOL_READ,
        description=description,
        argument_serializer=serializer,
        schema_sha256=schema_hash(SERVER_SCHEMAS[server_tool]),
        scope_check=github.scope_check,
        classify=github.classify,
    )


MANIFEST = ConnectorManifest(
    name="github",
    version=1,
    display_name="GitHub",
    kind=KIND_MCP,
    status=STATUS_ACTIVE,
    data_class="firm",
    workspace_types=frozenset({WORKSPACE_PERSONAL, WORKSPACE_COMPANY}),
    read_channels=frozenset({CHANNEL_WEB, CHANNEL_CLI}),
    url_env="GITHUB_MCP_URL",
    sidecar_image=SIDECAR_IMAGE,
    tools=(
        _read(
            "github_pr_status",
            "pull_request_read",
            "Status of one pull request in a GitHub repository registered in this "
            "workspace: title, state, mergeability, combined status or check runs. "
            "Arguments: owner, repo, pullNumber, optional method "
            "(get | get_status | get_check_runs).",
            PullRequestStatusArgs,
        ),
        _read(
            "github_ci_status",
            "actions_list",
            "Recent GitHub Actions workflow runs (CI) of a repository registered in "
            "this workspace, optionally for one branch. Arguments: owner, repo, "
            "optional workflow_runs_filter.branch.",
            CiStatusArgs,
        ),
        _read(
            "github_branch_head",
            "get_commit",
            "The head commit of a branch (or one commit by SHA) of a GitHub "
            "repository registered in this workspace: SHA, message, author. "
            "Arguments: owner, repo, sha (a branch name or commit SHA).",
            BranchHeadArgs,
        ),
        ToolPolicy(
            name="github_open_pr",
            server_tool="create_pull_request",
            kind=TOOL_WRITE,
            description="Open a pull request for a pushed harness branch.",
            argument_serializer=OpenPullRequestArgs,
            schema_sha256=schema_hash(SERVER_SCHEMAS["create_pull_request"]),
            scope_check=github.scope_check,
            user_invocable=False,
            renderer=render_open_pr,
            gate_name=GATE_OPEN_PR,
            context_serializer=OpenPullRequestContext,
            precondition=head_unmoved,
        ),
    ),
)
