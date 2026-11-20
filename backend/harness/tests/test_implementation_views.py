"""Implementation-job API (Phase 3 branch 6): role x endpoint permissions,
workspace isolation, response shape (gates projected without payload, no
runner-host internals), plus the approvals-side `display_summary` and the
`user_invocable` gate refusal for system-only tools."""

import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from approvals.models import ApprovalRequest
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo, HarnessReview
from harness.services import create_harness_implementation_job, request_harness_push
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/harness/implementation-jobs"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]
CURATOR = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="friday",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


def member(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.update_or_create(
        workspace=workspace, user=user, defaults={"role": role}
    )
    return user


def actor_for(workspace, owner, role):
    if role == WorkspaceMember.Role.OWNER:
        return owner
    return member(workspace, role, f"{role}@test.dev")


def make_job(workspace, repo, owner):
    return create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="add a health endpoint"
    )


class TestPermissions:
    def test_requires_auth(self, workspace):
        assert APIClient().get(BASE, {"workspace": str(workspace.id)}).status_code == 401

    @pytest.mark.parametrize("role", ROLES)
    def test_list(self, workspace, owner, repo, role, web_client):
        make_job(workspace, repo, owner)
        response = web_client(actor_for(workspace, owner, role)).get(
            BASE, {"workspace": str(workspace.id)}
        )
        assert response.status_code == (200 if role in CURATOR else 403)

    @pytest.mark.parametrize("role", ROLES)
    def test_detail(self, workspace, owner, repo, role, web_client):
        job = make_job(workspace, repo, owner)
        response = web_client(actor_for(workspace, owner, role)).get(f"{BASE}/{job.id}")
        assert response.status_code == (200 if role in CURATOR else 403)

    @pytest.mark.parametrize("role", ROLES)
    def test_create(self, workspace, owner, repo, role, web_client):
        response = web_client(actor_for(workspace, owner, role)).post(
            BASE,
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "do it"},
            format="json",
        )
        assert response.status_code == (201 if role in CURATOR else 403)

    @pytest.mark.parametrize("role", ROLES)
    def test_cancel(self, workspace, owner, repo, role, web_client):
        job = make_job(workspace, repo, owner)
        response = web_client(actor_for(workspace, owner, role)).post(f"{BASE}/{job.id}/cancel")
        assert response.status_code == (200 if role in CURATOR else 403)

    def test_outsider_gets_404_everywhere(self, workspace, owner, repo, outsider, web_client):
        job = make_job(workspace, repo, owner)
        client = web_client(outsider)
        assert client.get(BASE, {"workspace": str(workspace.id)}).status_code == 404
        assert client.get(f"{BASE}/{job.id}").status_code == 404
        assert client.post(f"{BASE}/{job.id}/cancel").status_code == 404
        response = client.post(
            BASE,
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "x"},
            format="json",
        )
        assert response.status_code == 404


class TestIsolation:
    def test_repo_from_another_workspace_is_404(self, workspace, owner, outsider, web_client):
        other_ws = Workspace.objects.get(owner=outsider)
        other_repo = HarnessRepo.objects.create(
            workspace=other_ws, name="other", git_url="git@example.com:o/p.git", enabled=True
        )
        response = web_client(owner).post(
            BASE,
            {"workspace": str(workspace.id), "repo": str(other_repo.id), "goal": "x"},
            format="json",
        )
        assert response.status_code == 404

    def test_discovery_job_from_another_workspace_is_404(
        self, workspace, owner, repo, outsider, web_client
    ):
        other_ws = Workspace.objects.get(owner=outsider)
        other_repo = HarnessRepo.objects.create(
            workspace=other_ws, name="other", git_url="git@example.com:o/p.git", enabled=True
        )
        foreign = HarnessJob.objects.create(
            workspace=other_ws, repo=other_repo, goal="g", created_by=outsider
        )
        response = web_client(owner).post(
            BASE,
            {
                "workspace": str(workspace.id),
                "repo": str(repo.id),
                "goal": "x",
                "discovery_job": str(foreign.id),
            },
            format="json",
        )
        assert response.status_code == 404

    def test_list_only_returns_this_workspace(self, workspace, owner, repo, outsider, web_client):
        make_job(workspace, repo, owner)
        other_ws = Workspace.objects.get(owner=outsider)
        WorkspaceMember.objects.create(
            workspace=other_ws, user=owner, role=WorkspaceMember.Role.ADMIN
        )
        other_repo = HarnessRepo.objects.create(
            workspace=other_ws,
            name="other",
            git_url="git@example.com:o/p.git",
            enabled=True,
            test_command="pytest -q",
        )
        create_harness_implementation_job(
            workspace=other_ws, actor=owner, repo=other_repo, goal="other ws"
        )
        response = web_client(owner).get(BASE, {"workspace": str(workspace.id)})
        assert [row["goal"] for row in response.json()] == ["add a health endpoint"]


class TestShapes:
    def test_create_links_discovery_job_and_requests_gate_a(
        self, workspace, owner, repo, web_client
    ):
        discovery = HarnessJob.objects.create(
            workspace=workspace, repo=repo, goal="analyze", created_by=owner
        )
        response = web_client(owner).post(
            BASE,
            {
                "workspace": str(workspace.id),
                "repo": str(repo.id),
                "goal": "implement it",
                "discovery_job": str(discovery.id),
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["discovery_job"] == str(discovery.id)
        assert body["status"] == "pending_gate_a"
        assert body["agent"] == "claude_code"
        assert body["reviewer_agent"] == "codex_cli"
        assert body["repo_name"] == "friday"
        assert body["gate_a"]["status"] == "pending"
        assert set(body["gate_a"]) == {"id", "status", "decided_at"}
        assert body["gate_b"] is None

    def test_invalid_transition_is_400_with_detail(self, workspace, owner, repo, web_client):
        repo.enabled = False
        repo.save(update_fields=["enabled"])
        response = web_client(owner).post(
            BASE,
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "x"},
            format="json",
        )
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_blank_goal_is_400(self, workspace, owner, repo, web_client):
        response = web_client(owner).post(
            BASE,
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "   "},
            format="json",
        )
        assert response.status_code == 400

    def test_detail_exposes_reviews_and_transcripts_but_no_host_internals(
        self, workspace, owner, repo, web_client
    ):
        job = make_job(workspace, repo, owner)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(
            worktree_path="/tmp/secret-host-path",
            container_id="harness-impl-x",
            raw_transcript="cli output",
            diff_summary="summary",
            changed_files=["a.py"],
        )
        HarnessReview.objects.create(
            workspace=workspace,
            implementation_job=job,
            agent=HarnessReview.Agent.CODEX_CLI,
            status=HarnessReview.Status.SUCCEEDED,
            verdict=HarnessReview.Verdict.APPROVED,
            summary="looks fine",
            findings=["f1"],
            raw_transcript="review output",
        )
        body = web_client(owner).get(f"{BASE}/{job.id}").json()
        assert body["raw_transcript"] == "cli output"
        assert body["reviews"][0]["raw_transcript"] == "review output"
        assert body["reviews"][0]["verdict"] == "approved"
        assert "worktree_path" not in body
        assert "container_id" not in body
        assert "container_id" not in body["reviews"][0]
        assert "payload" not in body["gate_a"]

    def test_cancel_pending_gate_a_returns_cancelled(self, workspace, owner, repo, web_client):
        job = make_job(workspace, repo, owner)
        body = web_client(owner).post(f"{BASE}/{job.id}/cancel").json()
        assert body["status"] == "cancelled"
        assert body["gate_a"]["status"] == "expired"


class TestApprovalsDisplaySummary:
    def test_gate_a_summary_is_snapshotted_and_payload_stays_hidden(
        self, workspace, owner, repo, web_client
    ):
        job = make_job(workspace, repo, owner)
        repo.name = "renamed-later"
        repo.save(update_fields=["name"])
        response = web_client(owner).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
        )
        row = next(r for r in response.json() if r["id"] == str(job.gate_a_request_id))
        assert "payload" not in row
        summary = row["display_summary"]
        assert summary["kind"] == "harness_implement"
        assert summary["goal"] == "add a health endpoint"
        assert summary["repo_name"] == "friday"
        assert summary["branch_name"] == job.branch_name
        assert summary["reviewer_agent"] == "codex_cli"

    def test_gate_b_summary_comes_from_the_request_time_snapshot(
        self, workspace, owner, repo, web_client
    ):
        job = make_job(workspace, repo, owner)
        HarnessImplementationJob.objects.filter(pk=job.pk).update(
            status=HarnessImplementationJob.Status.PENDING_REVIEW,
            diff_summary="adds /health",
            changed_files=["app.py", "tests/test_app.py"],
            produced_commit_sha="abc123",
            test_result={"ran": True, "exit_code": 0, "passed": True, "command": "pytest -q"},
        )
        job.refresh_from_db()
        review = HarnessReview.objects.create(
            workspace=workspace,
            implementation_job=job,
            agent=HarnessReview.Agent.CODEX_CLI,
            status=HarnessReview.Status.SUCCEEDED,
            verdict=HarnessReview.Verdict.APPROVED,
            summary="solid",
        )
        job = request_harness_push(job=job, review=review)
        # Later edits must not change what Gate B showed.
        HarnessImplementationJob.objects.filter(pk=job.pk).update(diff_summary="edited later")
        HarnessReview.objects.filter(pk=review.pk).update(summary="edited later")
        response = web_client(owner).get(
            "/api/v1/approvals/requests", {"workspace": str(workspace.id)}
        )
        row = next(r for r in response.json() if r["id"] == str(job.gate_b_request_id))
        summary = row["display_summary"]
        assert summary["kind"] == "harness_push"
        assert summary["diff_summary"] == "adds /health"
        assert summary["changed_files"] == ["app.py", "tests/test_app.py"]
        assert summary["changed_files_total"] == 2
        assert summary["test_result"] == {"passed": True, "command": "pytest -q", "exit_code": 0}
        assert summary["review"] == {
            "agent": "codex_cli",
            "verdict": "approved",
            "summary": "solid",
        }
        assert summary["produced_commit_sha"] == "abc123"
        assert "payload" not in row


class TestSystemOnlyGates:
    @pytest.mark.parametrize("tool_name", ["harness_implement", "harness_push"])
    def test_direct_post_of_a_system_only_gate_is_refused(
        self, workspace, owner, repo, tool_name, web_client
    ):
        job = make_job(workspace, repo, owner)
        response = web_client(owner).post(
            "/api/v1/approvals/requests",
            {
                "workspace": str(workspace.id),
                "tool_name": tool_name,
                "payload": {"job_id": str(job.id)},
            },
            format="json",
        )
        assert response.status_code == 400
        assert ApprovalRequest.objects.filter(workspace=workspace, tool_name=tool_name).count() == (
            1 if tool_name == "harness_implement" else 0
        )

    def test_router_and_planner_vocabulary_excludes_system_only_gates(self):
        from tools.registry import list_tool_names

        names = list_tool_names()
        assert "harness_implement" not in names
        assert "harness_push" not in names
        assert "vault_export" in names


class TestDisplaySummaryAudience:
    """Phase 3 branch 6 review P2: the summary goes only to curators over
    the web channel -- the same audience that can decide the gate."""

    def _row(self, client, workspace, request_id):
        response = client.get("/api/v1/approvals/requests", {"workspace": str(workspace.id)})
        assert response.status_code == 200
        return next(r for r in response.json() if r["id"] == str(request_id))

    @pytest.mark.parametrize("role", [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER])
    def test_non_curators_get_no_summary(self, workspace, owner, repo, role, web_client):
        job = make_job(workspace, repo, owner)
        client = web_client(member(workspace, role, f"{role}-aud@test.dev"))
        assert self._row(client, workspace, job.gate_a_request_id)["display_summary"] is None

    def test_low_trust_channel_gets_no_summary(self, workspace, owner, repo):
        from accounts.authentication import hash_token
        from accounts.models import PersonalAccessToken

        job = make_job(workspace, repo, owner)
        raw = "jpat_test-raw-token-0000000000000000"
        PersonalAccessToken.objects.create(
            user=owner, workspace=workspace, name="cli", token_hash=hash_token(raw)
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert self._row(client, workspace, job.gate_a_request_id)["display_summary"] is None

    def test_admin_on_web_gets_the_summary(self, workspace, owner, repo, web_client):
        job = make_job(workspace, repo, owner)
        admin = member(workspace, WorkspaceMember.Role.ADMIN, "admin-aud@test.dev")
        row = self._row(web_client(admin), workspace, job.gate_a_request_id)
        assert row["display_summary"]["kind"] == "harness_implement"
        assert row["display_summary"]["reviewer_agent"] == "codex_cli"

    def test_unlinked_legacy_gate_row_gets_no_summary(self, workspace, owner, repo, web_client):
        job = make_job(workspace, repo, owner)
        forged = ApprovalRequest.objects.create(
            workspace=workspace,
            tool_name="harness_push",
            payload={"job_id": str(job.id), "review_verdict": "approved", "review_summary": "x"},
            requested_by=owner,
            origin=ApprovalRequest.Origin.HUMAN,
        )
        assert self._row(web_client(owner), workspace, forged.id)["display_summary"] is None


def test_discovery_job_from_another_repo_is_refused(workspace, owner, repo, web_client):
    other_repo = HarnessRepo.objects.create(
        workspace=workspace,
        name="other",
        git_url="git@example.com:o/p.git",
        enabled=True,
        test_command="pytest -q",
    )
    discovery = HarnessJob.objects.create(
        workspace=workspace, repo=other_repo, goal="analyze", created_by=owner
    )
    response = web_client(owner).post(
        BASE,
        {
            "workspace": str(workspace.id),
            "repo": str(repo.id),
            "goal": "x",
            "discovery_job": str(discovery.id),
        },
        format="json",
    )
    assert response.status_code == 400
