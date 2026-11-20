import pytest

from accounts.services import register_user
from harness.models import HarnessJob, HarnessRepo
from memory.models import Memory
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


class TestHarnessRepoDefaults:
    def test_defaults_are_inert(self, workspace):
        repo = HarnessRepo.objects.create(
            workspace=workspace, name="friday", git_url="git@example.com:x/y.git"
        )
        assert repo.enabled is False
        assert repo.sensitive is False
        assert repo.default_branch == "main"
        assert repo.runner == HarnessRepo.Runner.MAC_LOCAL

    def test_str_includes_workspace(self, workspace):
        repo = HarnessRepo.objects.create(
            workspace=workspace, name="friday", git_url="git@example.com:x/y.git"
        )
        assert str(workspace.id) in str(repo)


class TestHarnessJobDefaults:
    def test_defaults(self, workspace):
        repo = HarnessRepo.objects.create(
            workspace=workspace, name="friday", git_url="git@example.com:x/y.git"
        )
        job = HarnessJob.objects.create(workspace=workspace, repo=repo, goal="find the bug")
        assert job.agent == HarnessJob.Agent.CLAUDE_CODE
        assert job.sensitivity == Memory.Sensitivity.NORMAL
        assert job.status == HarnessJob.Status.PENDING
        assert job.cancel_requested is False
        assert job.container_id == ""
        assert job.elapsed_seconds == 0

    def test_str_includes_status(self, workspace):
        repo = HarnessRepo.objects.create(
            workspace=workspace, name="friday", git_url="git@example.com:x/y.git"
        )
        job = HarnessJob.objects.create(workspace=workspace, repo=repo, goal="find the bug")
        assert "pending" in str(job)


class TestHarnessJobCrossWorkspaceInvariant:
    def test_save_rejects_a_repo_from_a_different_workspace(self, owner):
        """The exact `Memory.save()` shape (`memory/models.py:159`, Codex
        code-review HIGH, branch 2 rounds 1-2): a job may only reference a
        repo in its own workspace."""
        workspace_a = Workspace.objects.create(name="A", owner=owner)
        repo_a = HarnessRepo.objects.create(
            workspace=workspace_a, name="repo-a", git_url="git@example.com:x/a.git"
        )
        workspace_b = Workspace.objects.create(name="B", owner=owner)

        with pytest.raises(ValueError):
            HarnessJob.objects.create(workspace=workspace_b, repo=repo_a, goal="find the bug")

        assert not HarnessJob.objects.filter(workspace=workspace_b).exists()

    def test_save_allows_a_repo_from_the_same_workspace(self, workspace):
        repo = HarnessRepo.objects.create(
            workspace=workspace, name="friday", git_url="git@example.com:x/y.git"
        )
        job = HarnessJob.objects.create(workspace=workspace, repo=repo, goal="find the bug")
        assert job.pk is not None
