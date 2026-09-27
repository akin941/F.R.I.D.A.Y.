import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from harness.models import HarnessJob, HarnessRepo
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]

CURATOR_STATUS = {
    WorkspaceMember.Role.OWNER: 200,
    WorkspaceMember.Role.ADMIN: 200,
    WorkspaceMember.Role.MEMBER: 403,
    WorkspaceMember.Role.VIEWER: 403,
}


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def make_repo(workspace, **overrides):
    fields = {"workspace": workspace, "name": "jarvis-os", "git_url": "git@example.com:x/y.git"}
    fields.update(overrides)
    return HarnessRepo.objects.create(**fields)


def create_payload(workspace, **overrides):
    payload = {
        "workspace": str(workspace.id),
        "name": "jarvis-os",
        "git_url": "git@example.com:x/y.git",
    }
    payload.update(overrides)
    return payload


class TestListCreate:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/harness/repos", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner, web_client):
        assert web_client(owner).get("/api/v1/harness/repos").status_code == 400

    def test_invalid_workspace_param_is_400(self, owner, web_client):
        response = web_client(owner).get("/api/v1/harness/repos", {"workspace": "not-a-uuid"})
        assert response.status_code == 400

    def test_outsider_gets_404_not_403(self, workspace, outsider, web_client):
        response = web_client(outsider).get(
            "/api/v1/harness/repos", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404

    def test_owner_lists_repos(self, workspace, owner, web_client):
        make_repo(workspace)
        response = web_client(owner).get("/api/v1/harness/repos", {"workspace": str(workspace.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_create_returns_serialized_repo(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/repos", create_payload(workspace), format="json"
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "jarvis-os"
        assert body["enabled"] is False
        assert body["sensitive"] is False
        assert body["runner"] == "mac_local"

    def test_create_empty_name_is_400(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/repos", create_payload(workspace, name="  "), format="json"
        )
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "git_url",
        [
            "https://ghp_faketoken123@github.com/org/repo.git",
            "https://user:pass@github.com/org/repo.git",
            "ssh://evilcorp@github.com/org/repo.git",
            "https://github.com/org/repo.git?token=abc123",
            "https://github.com/org/repo.git#access_token=abc123",
            "git@github.com:org/repo.git?token=abc123",
            "git@github.com:org/repo.git#access_token=abc123",
        ],
    )
    def test_create_rejects_credentials_embedded_in_git_url(
        self, workspace, owner, git_url, web_client
    ):
        """Codex code-review P2 (branch 1 rounds 2-4): git_url must never
        smuggle a credential -- via userinfo or via a query/fragment
        carrier, for both schemed and scp-like URL shapes -- HarnessRepo
        deliberately has no credentials field, clone auth resolves from env
        at run time."""
        response = web_client(owner).post(
            "/api/v1/harness/repos", create_payload(workspace, git_url=git_url), format="json"
        )
        assert response.status_code == 400

    def test_create_malformed_git_url_is_400_not_500(self, workspace, owner, web_client):
        """Codex code-review P2 (branch 1 round 3): a URL urlsplit() itself
        can't parse must be a clean validation error, not an unhandled
        500."""
        response = web_client(owner).post(
            "/api/v1/harness/repos",
            create_payload(workspace, git_url="https://[::1/repo.git"),
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "git_url",
        [
            "https://github.com/org/repo.git",
            "git@github.com:org/repo.git",
            "ssh://git@github.com/org/repo.git",
        ],
    )
    def test_create_allows_credential_free_git_url(self, workspace, owner, git_url, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/repos", create_payload(workspace, git_url=git_url), format="json"
        )
        assert response.status_code == 201

    def test_create_out_of_scope_workspace_is_404(self, outsider, web_client):
        workspace = Workspace.objects.create(name="Not yours", owner=outsider)
        WorkspaceMember.objects.create(
            workspace=workspace, user=outsider, role=WorkspaceMember.Role.OWNER
        )
        non_member = register_user(email="nonmember@test.dev", password=PASSWORD, name="X")
        response = web_client(non_member).post(
            "/api/v1/harness/repos", create_payload(workspace), format="json"
        )
        assert response.status_code == 404


class TestPermissionMatrix:
    """Role x endpoint matrix (CLAUDE.md rule 7) -- curator-gated CRUD:
    every verb, not just writes (brief §1)."""

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_list(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_repo(workspace)
        response = web_client(user).get("/api/v1/harness/repos", {"workspace": str(workspace.id)})
        assert response.status_code == expected

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_create(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).post(
            "/api/v1/harness/repos", create_payload(workspace), format="json"
        )
        assert response.status_code == (201 if expected == 200 else expected)

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_retrieve(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace)
        response = web_client(user).get(f"/api/v1/harness/repos/{repo.id}")
        assert response.status_code == expected

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_edit(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace)
        response = web_client(user).patch(
            f"/api/v1/harness/repos/{repo.id}", {"enabled": True}, format="json"
        )
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 204),
            (WorkspaceMember.Role.ADMIN, 204),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_delete(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace)
        response = web_client(user).delete(f"/api/v1/harness/repos/{repo.id}")
        assert response.status_code == expected


class TestDetail:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, outsider, web_client):
        repo = make_repo(workspace)
        response = web_client(outsider).get(f"/api/v1/harness/repos/{repo.id}")
        assert response.status_code == 404

    def test_nonexistent_repo_is_404(self, owner, web_client):
        response = web_client(owner).get(
            "/api/v1/harness/repos/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    def test_edit_updates_fields(self, workspace, owner, web_client):
        repo = make_repo(workspace)
        response = web_client(owner).patch(
            f"/api/v1/harness/repos/{repo.id}",
            {"enabled": True, "sensitive": True},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert response.json()["sensitive"] is True

    def test_delete_removes_row(self, workspace, owner, web_client):
        repo = make_repo(workspace)
        response = web_client(owner).delete(f"/api/v1/harness/repos/{repo.id}")
        assert response.status_code == 204
        assert not HarnessRepo.objects.filter(pk=repo.pk).exists()

    def test_delete_repo_with_job_history_is_400_not_500(self, workspace, owner, web_client):
        """Codex code-review P2, branch 2 round 7: `delete_repo()` translates
        the `ProtectedError` from a repo's job history into
        `HarnessInvalidTransition`, but the view only ever caught
        `HarnessPermissionDenied` -- so it still escaped as an unhandled 500
        until `_service_error_response` learned this exception too."""
        repo = make_repo(workspace)
        HarnessJob.objects.create(workspace=workspace, repo=repo, goal="find bug")
        response = web_client(owner).delete(f"/api/v1/harness/repos/{repo.id}")
        assert response.status_code == 400
        assert "job history" in response.json()["detail"]
        assert HarnessRepo.objects.filter(pk=repo.pk).exists()


def make_job(workspace, repo, **overrides):
    fields = {"workspace": workspace, "repo": repo, "goal": "find the bug"}
    fields.update(overrides)
    return HarnessJob.objects.create(**fields)


def job_create_payload(workspace, repo, **overrides):
    payload = {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "find the bug"}
    payload.update(overrides)
    return payload


class TestJobListCreate:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/harness/jobs", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner, web_client):
        assert web_client(owner).get("/api/v1/harness/jobs").status_code == 400

    def test_outsider_gets_404_not_403(self, workspace, outsider, web_client):
        response = web_client(outsider).get(
            "/api/v1/harness/jobs", {"workspace": str(workspace.id)}
        )
        assert response.status_code == 404

    def test_owner_lists_jobs(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=True)
        make_job(workspace, repo)
        response = web_client(owner).get("/api/v1/harness/jobs", {"workspace": str(workspace.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_list_response_has_no_briefing_field(self, workspace, owner, web_client):
        """`HarnessJobSerializer` (list shape) omits `briefing` -- only
        `HarnessJobDetailSerializer` includes it (brief §4: kept light for
        the list view)."""
        repo = make_repo(workspace, enabled=True)
        make_job(workspace, repo)
        response = web_client(owner).get("/api/v1/harness/jobs", {"workspace": str(workspace.id)})
        assert "briefing" not in response.json()[0]

    def test_create_returns_serialized_job(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=True)
        response = web_client(owner).post(
            "/api/v1/harness/jobs", job_create_payload(workspace, repo), format="json"
        )
        assert response.status_code == 201
        body = response.json()
        assert body["goal"] == "find the bug"
        assert body["status"] == "pending"
        assert body["agent"] == "claude_code"

    def test_create_empty_goal_is_400(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=True)
        response = web_client(owner).post(
            "/api/v1/harness/jobs",
            job_create_payload(workspace, repo, goal="  "),
            format="json",
        )
        assert response.status_code == 400

    def test_create_against_disabled_repo_is_400(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=False)
        response = web_client(owner).post(
            "/api/v1/harness/jobs", job_create_payload(workspace, repo), format="json"
        )
        assert response.status_code == 400

    def test_create_against_repo_in_a_different_workspace_is_403(
        self, workspace, owner, web_client
    ):
        """`owner` can see `other_repo` (they're a member of `other` too),
        so the repo lookup itself succeeds -- `create_harness_job`'s own
        `repo.workspace_id != workspace.id` check is what rejects this,
        the same `HarnessPermissionDenied` `test_services.py`'s
        `test_repo_from_a_different_workspace_is_rejected` asserts."""
        other = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(workspace=other, user=owner, role=WorkspaceMember.Role.OWNER)
        other_repo = make_repo(other, enabled=True)
        response = web_client(owner).post(
            "/api/v1/harness/jobs",
            job_create_payload(workspace, other_repo),
            format="json",
        )
        assert response.status_code == 403

    def test_create_against_a_repo_the_actor_cannot_see_at_all_is_404(
        self, workspace, owner, web_client
    ):
        """Existence must not leak (mirrors `HarnessRepoDetailView`'s own
        convention): a repo id the requester has no membership-based
        visibility into at all -- not even in another workspace -- must
        404 before the service layer's own workspace-mismatch check is
        ever reached."""
        stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
        other = Workspace.objects.create(name="Stranger's", owner=stranger)
        WorkspaceMember.objects.create(
            workspace=other, user=stranger, role=WorkspaceMember.Role.OWNER
        )
        invisible_repo = make_repo(other, enabled=True)
        response = web_client(owner).post(
            "/api/v1/harness/jobs",
            job_create_payload(workspace, invisible_repo),
            format="json",
        )
        assert response.status_code == 404

    def test_create_out_of_scope_workspace_is_404(self, outsider, web_client):
        workspace = Workspace.objects.create(name="Not yours", owner=outsider)
        WorkspaceMember.objects.create(
            workspace=workspace, user=outsider, role=WorkspaceMember.Role.OWNER
        )
        repo = make_repo(workspace, enabled=True)
        non_member = register_user(email="nonmember2@test.dev", password=PASSWORD, name="X")
        response = web_client(non_member).post(
            "/api/v1/harness/jobs", job_create_payload(workspace, repo), format="json"
        )
        assert response.status_code == 404


class TestJobPermissionMatrix:
    """Role x endpoint matrix (CLAUDE.md rule 7) -- curator-gated like the
    repo registry (brief §2: launching/reviewing a run is not a
    lighter-weight action than registering the repo)."""

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_list(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        make_job(workspace, repo)
        response = web_client(user).get("/api/v1/harness/jobs", {"workspace": str(workspace.id)})
        assert response.status_code == expected

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_create(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        response = web_client(user).post(
            "/api/v1/harness/jobs", job_create_payload(workspace, repo), format="json"
        )
        assert response.status_code == (201 if expected == 200 else expected)

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_retrieve(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        response = web_client(user).get(f"/api/v1/harness/jobs/{job.id}")
        assert response.status_code == expected

    @pytest.mark.parametrize("role,expected", list(CURATOR_STATUS.items()))
    def test_cancel(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        response = web_client(user).post(f"/api/v1/harness/jobs/{job.id}/cancel")
        assert response.status_code == expected


class TestJobDetail:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, outsider, web_client):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        response = web_client(outsider).get(f"/api/v1/harness/jobs/{job.id}")
        assert response.status_code == 404

    def test_nonexistent_job_is_404(self, owner, web_client):
        response = web_client(owner).get(
            "/api/v1/harness/jobs/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    def test_detail_includes_briefing(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=True)
        job = make_job(
            workspace,
            repo,
            briefing={"summary": "All clear.", "findings": [], "raw_transcript": "..."},
        )
        response = web_client(owner).get(f"/api/v1/harness/jobs/{job.id}")
        assert response.status_code == 200
        assert response.json()["briefing"]["summary"] == "All clear."

    def test_cancel_pending_job(self, workspace, owner, web_client):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        response = web_client(owner).post(f"/api/v1/harness/jobs/{job.id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_cancel_out_of_scope_job_is_404(self, workspace, outsider, web_client):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        response = web_client(outsider).post(f"/api/v1/harness/jobs/{job.id}/cancel")
        assert response.status_code == 404


class TestJobWorkspaceIsolation:
    """Release-gate discipline (brief §4, mirrors Phase 1 branch 4's
    isolation test): a HarnessJob from workspace A must never appear in
    workspace B's list or be reachable through workspace B's detail/cancel
    endpoints, even for a curator who owns both workspaces."""

    def test_job_from_another_workspace_never_appears_in_list(self, workspace, owner, web_client):
        """`owner` is a curator of BOTH workspaces here -- role alone must
        not be what scopes the list; `list_jobs`'s `for_actor(workspace=...)`
        filter is what has to exclude the other workspace's job."""
        other = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(workspace=other, user=owner, role=WorkspaceMember.Role.OWNER)
        other_repo = make_repo(other, enabled=True)
        make_job(other, other_repo)
        make_job(workspace, make_repo(workspace, enabled=True))
        response = web_client(owner).get("/api/v1/harness/jobs", {"workspace": str(workspace.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["workspace"] == str(workspace.id)
