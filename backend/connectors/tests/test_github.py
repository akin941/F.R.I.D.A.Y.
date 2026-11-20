"""GitHub connector (Phase 5 branch 5, brief D7) against the fake
`github-mcp-server`: manifest shape, pins, the workspace repo scope check
(refused before any call), per-call `personal` class for a sensitive repo,
the system-only `github_open_pr` gate (unmintable via `POST /approvals`),
the `head_moved` precondition, and unlisted merge/file tools unreachable.

The harness registers the real repo resolver at startup; these tests
substitute a table-driven one (`connectors` tests may not import harness).
The harness-side resolver and endpoints are covered in
`harness/tests/test_pull_requests.py`."""

import json
import re
from pathlib import Path

import pytest

from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.models import AuditLog
from connectors import github
from connectors.manifest import get_manifest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_SYSTEM, CHANNEL_WEB
from tools.registry import ReadToolError, get_tool, list_read_tools

pytestmark = pytest.mark.django_db

HEAD = "harness/p3-1234"
READS = {"github_pr_status", "github_ci_status", "github_branch_head"}


@pytest.fixture
def gh(monkeypatch):
    return fakes.install_github(monkeypatch, fakes.GithubServerState())


@pytest.fixture
def repos(monkeypatch):
    """`{(workspace_id, canonical): RepoScope}` behind the scope check."""
    table = {}

    def resolver(workspace, canonical):
        return table.get((workspace.id, canonical))

    monkeypatch.setattr(github, "_repo_resolver", resolver)

    def add(workspace, owner, repo, *, sensitive=False):
        table[(workspace.id, github.canonical_remote(owner, repo))] = github.RepoScope(
            sensitive=sensitive, default_branch="main"
        )

    return add


@pytest.fixture
def manifest():
    return get_manifest("github")


def _read(workspace, owner, name, args, channel=CHANNEL_WEB):
    spec = next(s for s in list_read_tools(workspace=workspace, channel=channel) if s.name == name)
    return spec.execute(workspace=workspace, actor=owner, args=args, channel=channel)


def test_manifest_shape(manifest):
    assert manifest.status == "active" and manifest.kind == "mcp"
    assert manifest.data_class == "firm"
    assert manifest.url_env == "GITHUB_MCP_URL"
    assert manifest.credential_envs == ()  # the PAT never reaches Friday
    assert manifest.sidecar_image.startswith("ghcr.io/github/github-mcp-server:v1.12.2@sha256:")
    assert {t.name for t in manifest.reads} == READS
    assert [t.server_tool for t in manifest.writes] == ["create_pull_request"]
    gate = get_tool("github_open_pr")
    assert gate.gated is True and gate.user_invocable is False
    assert all(t.scope_check is github.scope_check for t in manifest.tools)


def test_enable_pins_every_tool_and_ignores_unlisted_ones(workspace, enable, gh, manifest):
    binding = enable(manifest, workspace)
    tools = binding.last_health["tools"]
    assert tools == {name: "ok" for name in [*READS, "github_open_pr"]}
    assert "merge_pull_request" not in json.dumps(binding.last_health)
    assert binding.last_health["server_name"] == "github-mcp-server"


def test_merge_and_file_tools_are_unreachable_even_when_listed(workspace, enable, gh, manifest):
    enable(manifest, workspace)
    assert {"merge_pull_request", "create_or_update_file"} <= set(gh.schemas)
    names = {s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_WEB)}
    assert names >= READS
    assert not any("merge" in n or "file" in n for n in names)
    assert get_tool("mcp_github_merge_pull_request") is None
    assert get_tool("mcp_github_create_or_update_file") is None
    assert all(
        t.server_tool not in {"merge_pull_request", "create_or_update_file"} for t in manifest.tools
    )


def test_cross_workspace_repo_is_refused_before_any_call(
    workspace, firm, owner, enable, gh, manifest, repos
):
    enable(manifest, workspace)
    repos(firm, "acme", "app")  # firm B's repo, not this workspace's
    gh.calls.clear()
    with pytest.raises(ReadToolError) as exc:
        _read(
            workspace, owner, "github_branch_head", {"owner": "acme", "repo": "app", "sha": "main"}
        )
    assert exc.value.code == "out_of_scope"
    assert gh.calls == []
    log = AuditLog.objects.filter(action="connector.read").latest("created_at")
    assert log.metadata["outcome"] == "out_of_scope"


def test_unregistered_repo_and_no_resolver_fail_closed(
    workspace, owner, enable, gh, manifest, monkeypatch
):
    enable(manifest, workspace)
    monkeypatch.setattr(github, "_repo_resolver", None)
    gh.calls.clear()
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, "github_ci_status", {"owner": "acme", "repo": "app"})
    assert exc.value.code == "out_of_scope"
    assert gh.calls == []


def test_scope_matches_any_spelling_of_the_remote(workspace, repos):
    repos(workspace, "Acme", "App")
    assert github.resolve_scope(workspace, "acme", "app") is not None
    assert github.split_github_remote("git@github.com:Acme/App.git") == ("acme", "app")
    assert github.split_github_remote("ssh://git@ssh.github.com:443/acme/app") == ("acme", "app")
    assert github.split_github_remote("https://gitlab.com/acme/app") is None


def test_reads_are_audited_and_run_over_cli(workspace, owner, enable, gh, manifest, repos):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    gh.workflow_runs = [fakes.workflow_run(fakes.HEAD_SHA)]
    result = _read(
        workspace, owner, "github_ci_status", {"owner": "acme", "repo": "app"}, CHANNEL_CLI
    )
    assert "workflow_runs" in result.text
    assert result.data_class == "firm"
    name, sent = gh.calls[-1]
    assert name == "actions_list"
    assert sent == {"owner": "acme", "repo": "app", "method": "list_workflow_runs", "per_page": 30}
    log = AuditLog.objects.filter(action="connector.read").latest("created_at")
    assert log.metadata["outcome"] == "ok" and log.metadata["tool"] == "github_ci_status"
    assert log.metadata["channel"] == CHANNEL_CLI
    assert "acme" not in json.dumps(log.metadata)  # args hashed, never stored


def test_sensitive_repo_read_is_personal_and_web_only(
    workspace, owner, enable, gh, manifest, repos
):
    enable(manifest, workspace)
    repos(workspace, "acme", "secret", sensitive=True)
    args = {"owner": "acme", "repo": "secret", "sha": "main"}
    gh.calls.clear()
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, "github_branch_head", args, CHANNEL_CLI)
    assert exc.value.code == "channel_forbidden"
    assert gh.calls == []
    result = _read(workspace, owner, "github_branch_head", args, CHANNEL_WEB)
    assert result.data_class == "personal"
    assert gh.calls == [
        ("get_commit", {"owner": "acme", "repo": "secret", "sha": "main", "include_diff": False})
    ]


@pytest.mark.parametrize(
    "tool, args",
    [
        (
            "github_pr_status",
            {"owner": "acme", "repo": "app", "pullNumber": 1, "method": "get_diff"},
        ),
        (
            "github_branch_head",
            {"owner": "acme", "repo": "app", "sha": "main", "include_diff": True},
        ),
        ("github_branch_head", {"owner": "acme", "repo": "..", "sha": "main"}),
        ("github_ci_status", {"owner": "-acme", "repo": "app"}),
    ],
)
def test_restricted_arguments_are_refused_before_any_call(
    workspace, owner, enable, gh, manifest, repos, tool, args
):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    gh.calls.clear()
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, tool, args)
    assert exc.value.code == "invalid_arguments"
    assert gh.calls == []


# --- github_open_pr ---


def _pr_payload(**overrides):
    arguments = {
        "owner": "acme",
        "repo": "app",
        "title": "Add the header",
        "body": "## Goal\n\nAdd it.",
        "head": HEAD,
        "base": "main",
        "draft": False,
        "maintainer_can_modify": False,
        "reviewers": [],
    }
    arguments.update(overrides)
    return {
        "arguments": arguments,
        "context": {
            "head_sha": fakes.HEAD_SHA,
            "origin_ref": "harness_implementation_job:00000000-0000-0000-0000-000000000001",
        },
    }


def _mint(workspace, owner, payload):
    return create_request(
        workspace=workspace,
        actor=owner,
        tool_name="github_open_pr",
        payload=payload,
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
        system_created=True,
    )


@pytest.fixture
def approve(web_client, owner, committed):
    def _approve(request_id):
        with committed():
            return web_client(owner).post(f"/api/v1/approvals/requests/{request_id}/approve")

    return _approve


def test_open_pr_is_unmintable_via_post_approvals(
    workspace, owner, enable, gh, manifest, repos, web_client
):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    response = web_client(owner).post(
        "/api/v1/approvals/requests",
        {"workspace": str(workspace.id), "tool_name": "github_open_pr", "payload": _pr_payload()},
        format="json",
    )
    assert response.status_code == 400
    with pytest.raises(InvalidTransition):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name="github_open_pr",
            payload=_pr_payload(),
            origin=ApprovalRequest.Origin.HUMAN,
            channel=CHANNEL_SYSTEM,
        )
    assert not ApprovalRequest.objects.exists()


@pytest.mark.parametrize(
    "override",
    [
        {"reviewers": ["mallory"]},
        {"maintainer_can_modify": True},
        {"head": "main"},
        {"owner": "other"},
    ],
)
def test_open_pr_fixed_fields_and_scope_are_enforced(
    workspace, owner, enable, gh, manifest, repos, override
):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    with pytest.raises(InvalidTransition):
        _mint(workspace, owner, _pr_payload(**override))
    assert not ApprovalRequest.objects.exists()


def test_open_pr_sends_once_after_the_head_check(
    workspace, owner, enable, gh, manifest, repos, web_client, approve
):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    gh.heads[HEAD] = fakes.HEAD_SHA
    request = _mint(workspace, owner, _pr_payload())
    summary = (
        web_client(owner).get(f"/api/v1/approvals/requests/{request.id}").json()["display_summary"]
    )
    rows = {row["label"]: row["value"] for row in summary["rows"]}
    assert rows["İnceleyiciler (sabit)"] == "(yok)"
    assert rows["maintainer_can_modify (sabit)"] == "false"
    assert {r["label"]: r["value"] for r in summary["context_rows"]}["head_sha"] == fakes.HEAD_SHA
    gh.calls.clear()
    assert approve(request.id).status_code == 200
    assert gh.names() == ["get_commit", "create_pull_request"]
    sent = gh.calls[1][1]
    assert summary["arguments_json"] == json.dumps(
        sent, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert sent["reviewers"] == [] and sent["maintainer_can_modify"] is False
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded"
    ref = github.pull_request_ref(json.loads(execution.result["text"]))
    assert ref == {"number": 42, "url": "https://github.com/acme/app/pull/42"}


def test_head_moved_sends_nothing(workspace, owner, enable, gh, manifest, repos, approve):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    gh.heads[HEAD] = fakes.MOVED_SHA
    request = _mint(workspace, owner, _pr_payload())
    gh.calls.clear()
    approve(request.id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "failed"
    assert execution.error_type == "head_moved"
    assert "create_pull_request" not in gh.names()
    assert AuditLog.objects.get(action="connector.write.failed").metadata["error_type"] == (
        "head_moved"
    )


def test_head_check_tool_disabled_sends_nothing(
    workspace, owner, enable, gh, manifest, repos, approve
):
    enable(manifest, workspace)
    repos(workspace, "acme", "app")
    request = _mint(workspace, owner, _pr_payload())
    binding = ConnectorBinding.objects.get(connector="github")
    binding.last_health["tools"]["github_branch_head"] = "mismatch"
    binding.save(update_fields=["last_health"])
    gh.calls.clear()
    approve(request.id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "failed" and execution.error_type == "pin_mismatch"
    assert gh.calls == []


def test_ci_state_collapses_runs():
    sha = fakes.HEAD_SHA
    runs = lambda *r: {"workflow_runs": list(r)}  # noqa: E731
    assert github.ci_state(runs(), head_sha=sha)["state"] == "unknown"
    assert github.ci_state(runs(fakes.workflow_run(sha)), head_sha=sha)["state"] == "success"
    assert (
        github.ci_state(runs(fakes.workflow_run(fakes.MOVED_SHA)), head_sha=sha)["state"]
        == "unknown"
    )
    assert (
        github.ci_state(
            runs(fakes.workflow_run(sha), fakes.workflow_run(sha, conclusion="failure")),
            head_sha=sha,
        )["state"]
        == "failure"
    )
    assert (
        github.ci_state(
            runs(fakes.workflow_run(sha, status="in_progress", conclusion="")), head_sha=sha
        )["state"]
        == "pending"
    )
    assert github.ci_state("garbage")["state"] == "unknown"


# --- the sidecar (compose + Dockerfile, plain-text checks) ---

ROOT = Path(__file__).resolve().parents[3]


def _service_block(text, name):
    lines = text.splitlines()
    start = lines.index(f"  {name}:")
    block = []
    for line in lines[start + 1 :]:
        if re.match(r"^  [a-z][a-z0-9-]*:\s*$", line) or re.match(r"^[a-z]", line):
            break
        block.append(line)
    return "\n".join(block)


def test_sidecar_is_hardened_digest_pinned_and_alone_holds_the_pat():
    from connectors.manifests.github import SERVER_TOOLS, SIDECAR_IMAGE

    compose = (ROOT / "deploy" / "docker" / "compose.yml").read_text()
    block = _service_block(compose, "github-mcp")
    assert "<<: *connector-sidecar" in block
    assert "ports:" not in block
    assert '["connectors", "connectors-egress"]' in block
    tools = ",".join(SERVER_TOOLS)
    assert f'"--tools={tools}"' in block
    assert f'BRIDGE_ALLOWED_TOOLS: "{tools}"' in block
    assert 'BRIDGE_ENV_PASSTHROUGH: "GITHUB_PERSONAL_ACCESS_TOKEN"' in block
    # Stdio mode behind the bridge, never the server's HTTP mode.
    assert '"stdio"' in block and '"http"' not in block
    # The PAT is referenced by this service only.
    outside = compose.replace(block, "")
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in "\n".join(
        line for line in outside.splitlines() if not line.lstrip().startswith("#")
    )
    dockerfile = (ROOT / "backend" / "connectors" / "docker" / "Dockerfile.github").read_text()
    assert f"FROM {SIDECAR_IMAGE} AS github" in dockerfile
    assert "USER bridge" in dockerfile


def test_context_and_preconditions_are_write_only():
    import dataclasses

    from connectors.manifest import validate_manifest

    manifest = get_manifest("github")
    read = manifest.get_tool("github_ci_status")
    bad = dataclasses.replace(read, precondition=lambda *a: None)
    with pytest.raises(ValueError, match="writes only"):
        validate_manifest(dataclasses.replace(manifest, name="gh_bad", tools=(bad,)))
