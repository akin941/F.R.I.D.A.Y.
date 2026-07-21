from unittest import mock

import pytest

from accounts.services import register_user
from audit.models import AuditLog
from harness import services as harness_services
from harness.models import HarnessJob, HarnessRepo
from harness.services import (
    HarnessInvalidTransition,
    HarnessPermissionDenied,
    cancel_harness_job,
    create_harness_job,
    create_repo,
    delete_repo,
    dispatch_harness_job,
    edit_repo,
    get_job,
    get_repo,
    list_jobs,
    list_repos,
)
from memory.models import Memory
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def make_repo(workspace, **overrides):
    fields = {"workspace": workspace, "name": "jarvis-os", "git_url": "git@example.com:x/y.git"}
    fields.update(overrides)
    return HarnessRepo.objects.create(**fields)


class TestCreateRepo:
    def test_owner_can_create(self, workspace, owner):
        repo = create_repo(
            workspace=workspace, actor=owner, name="jarvis-os", git_url="git@example.com:x/y.git"
        )
        assert repo.workspace_id == workspace.id
        assert repo.enabled is False
        assert repo.sensitive is False
        assert repo.default_branch == "main"
        assert repo.runner == HarnessRepo.Runner.MAC_LOCAL
        assert repo.test_command == ""

    def test_can_set_test_command_at_creation(self, workspace, owner):
        """Codex code-review P2, branch 2 round 1: an earlier version added
        `HarnessRepo.test_command` and `create_harness_implementation_job`'s
        own hard requirement for it, without ever exposing it through this
        service (the only sanctioned creation path) or its serializer --
        every repo registered through the real API could never run an
        implementation job at all."""
        repo = create_repo(
            workspace=workspace,
            actor=owner,
            name="jarvis-os",
            git_url="git@example.com:x/y.git",
            test_command="pytest -q",
        )
        assert repo.test_command == "pytest -q"

    def test_create_writes_audit_row(self, workspace, owner):
        repo = create_repo(
            workspace=workspace, actor=owner, name="jarvis-os", git_url="git@example.com:x/y.git"
        )
        entry = AuditLog.objects.get(action="harness.repo_registered")
        assert entry.resource_id == str(repo.id)
        assert entry.workspace_id == workspace.id

    @pytest.mark.parametrize(
        "role",
        [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER],
    )
    def test_non_curator_cannot_create(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        with pytest.raises(HarnessPermissionDenied):
            create_repo(
                workspace=workspace,
                actor=user,
                name="jarvis-os",
                git_url="git@example.com:x/y.git",
            )
        assert not HarnessRepo.objects.filter(workspace=workspace).exists()

    def test_admin_can_create(self, workspace):
        admin = user_with_role(workspace, WorkspaceMember.Role.ADMIN, "admin@test.dev")
        repo = create_repo(
            workspace=workspace, actor=admin, name="jarvis-os", git_url="git@example.com:x/y.git"
        )
        assert repo.pk is not None


class TestListGetRepo:
    def test_curator_can_list(self, workspace, owner):
        make_repo(workspace)
        repos = list_repos(workspace=workspace, actor=owner)
        assert repos.count() == 1

    @pytest.mark.parametrize(
        "role",
        [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER],
    )
    def test_non_curator_cannot_list(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_repo(workspace)
        with pytest.raises(HarnessPermissionDenied):
            list_repos(workspace=workspace, actor=user)

    def test_curator_can_get(self, workspace, owner):
        repo = make_repo(workspace)
        fetched = get_repo(workspace=workspace, actor=owner, repo_id=repo.id)
        assert fetched.id == repo.id

    def test_non_curator_get_raises(self, workspace):
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        repo = make_repo(workspace)
        with pytest.raises(HarnessPermissionDenied):
            get_repo(workspace=workspace, actor=member, repo_id=repo.id)

    def test_get_out_of_scope_returns_none_not_error(self, workspace, owner):
        other_workspace = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(
            workspace=other_workspace, user=owner, role=WorkspaceMember.Role.OWNER
        )
        repo = make_repo(other_workspace)
        # owner is a curator of `workspace` too, but this repo belongs to a
        # different workspace than the one passed -- for_actor's workspace
        # filter must exclude it.
        assert get_repo(workspace=workspace, actor=owner, repo_id=repo.id) is None


class TestEditRepo:
    def test_curator_can_edit(self, workspace, owner):
        repo = make_repo(workspace)
        updated = edit_repo(repo=repo, actor=owner, enabled=True, name="renamed")
        assert updated.enabled is True
        assert updated.name == "renamed"

    def test_edit_writes_audit_row_with_changed_fields(self, workspace, owner):
        repo = make_repo(workspace)
        edit_repo(repo=repo, actor=owner, enabled=True)
        entry = AuditLog.objects.get(action="harness.repo_updated")
        assert entry.metadata["fields_changed"] == ["enabled"]

    def test_no_op_edit_writes_no_audit_row(self, workspace, owner):
        repo = make_repo(workspace, name="same")
        edit_repo(repo=repo, actor=owner, name="same")
        assert not AuditLog.objects.filter(action="harness.repo_updated").exists()

    def test_can_edit_test_command(self, workspace, owner):
        repo = make_repo(workspace)
        updated = edit_repo(repo=repo, actor=owner, test_command="pytest -q")
        assert updated.test_command == "pytest -q"

    @pytest.mark.parametrize(
        "role",
        [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER],
    )
    def test_non_curator_cannot_edit(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace)
        with pytest.raises(HarnessPermissionDenied):
            edit_repo(repo=repo, actor=user, enabled=True)
        repo.refresh_from_db()
        assert repo.enabled is False


class TestDeleteRepo:
    def test_curator_can_delete(self, workspace, owner):
        repo = make_repo(workspace)
        delete_repo(repo=repo, actor=owner)
        assert not HarnessRepo.objects.filter(pk=repo.pk).exists()

    def test_delete_writes_audit_row(self, workspace, owner):
        repo = make_repo(workspace)
        repo_id = repo.id
        delete_repo(repo=repo, actor=owner)
        entry = AuditLog.objects.get(action="harness.repo_deleted")
        assert entry.resource_id == str(repo_id)

    @pytest.mark.parametrize(
        "role",
        [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER],
    )
    def test_non_curator_cannot_delete(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace)
        with pytest.raises(HarnessPermissionDenied):
            delete_repo(repo=repo, actor=user)
        assert HarnessRepo.objects.filter(pk=repo.pk).exists()

    def test_deleting_a_repo_with_job_history_is_a_clean_400_not_a_500(self, workspace, owner):
        """Codex code-review P2, branch 2 round 5: HarnessJob.repo is
        on_delete=PROTECT (by design -- job/audit history must never
        silently disappear), but that surfaced as an unhandled
        ProtectedError/500 instead of a clean service-level error."""
        repo = make_repo(workspace, enabled=True)
        HarnessJob.objects.create(workspace=workspace, repo=repo, goal="find bug")

        with pytest.raises(HarnessInvalidTransition):
            delete_repo(repo=repo, actor=owner)

        assert HarnessRepo.objects.filter(pk=repo.pk).exists()
        # The rejected attempt must not have left a stray audit row behind
        # (the whole function is @transaction.atomic).
        assert not AuditLog.objects.filter(action="harness.repo_deleted").exists()


class TestCrossWorkspaceInvariant:
    def test_repo_from_a_different_workspace_is_out_of_scope_for_edit(self, owner):
        workspace_a = Workspace.objects.create(name="A", owner=owner)
        WorkspaceMember.objects.create(
            workspace=workspace_a, user=owner, role=WorkspaceMember.Role.OWNER
        )
        repo_a = make_repo(workspace_a)
        # A curator of a *different* workspace must not be able to reach
        # workspace_a's repo through the service layer's locked-fetch path.
        outsider = register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")
        # register_user already creates outsider's own personal workspace +
        # owner membership (workspaces.services.ensure_personal_workspace) --
        # outsider is a curator there, but never a member of workspace_a.
        with pytest.raises(HarnessPermissionDenied):
            edit_repo(repo=repo_a, actor=outsider, enabled=True)

    def test_forged_workspace_attribute_cannot_bypass_curator_check(self, owner):
        """Codex code-review P2 (branch 1, round 1): a stale/forged `repo`
        instance whose in-memory `.workspace` attribute disagrees with the
        row's real, current workspace must not let the pre-lock authorization
        check run against the wrong (attacker-favorable) workspace. The
        actor here is only a MEMBER of `workspace_a` (real owner of the row)
        but a curator (OWNER) of their own `workspace_b` -- if
        `edit_repo`/`delete_repo` checked curator rights against
        `repo.workspace` before locking, this would have incorrectly passed
        against `workspace_b`."""
        workspace_a = Workspace.objects.create(name="A", owner=owner)
        WorkspaceMember.objects.create(
            workspace=workspace_a, user=owner, role=WorkspaceMember.Role.OWNER
        )
        repo_a = make_repo(workspace_a)

        attacker = register_user(email="attacker@test.dev", password=PASSWORD, name="Attacker")
        workspace_b = Workspace.objects.get(owner=attacker)  # attacker is curator here
        WorkspaceMember.objects.create(
            workspace=workspace_a, user=attacker, role=WorkspaceMember.Role.MEMBER
        )

        forged = HarnessRepo.objects.get(pk=repo_a.pk)
        forged.workspace = workspace_b  # in-memory only, never saved

        with pytest.raises(HarnessPermissionDenied):
            edit_repo(repo=forged, actor=attacker, enabled=True)
        repo_a.refresh_from_db()
        assert repo_a.enabled is False

        with pytest.raises(HarnessPermissionDenied):
            delete_repo(repo=forged, actor=attacker)
        assert HarnessRepo.objects.filter(pk=repo_a.pk).exists()


class TestCreateHarnessJob:
    def test_owner_can_create_against_an_enabled_repo(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        assert job.status == HarnessJob.Status.PENDING
        assert job.agent == HarnessJob.Agent.CLAUDE_CODE
        assert job.sensitivity == Memory.Sensitivity.NORMAL
        assert job.created_by == owner

    def test_create_dispatches_the_job_on_commit(
        self, workspace, owner, monkeypatch, django_capture_on_commit_callbacks
    ):
        """Codex code-review P1, branch 2 round 1: an earlier draft left
        the row `pending` with nothing ever enqueuing
        `harness.tasks.run_harness_job` -- the only sanctioned creation
        path must actually schedule the job, not just create the row."""
        dispatch_mock = mock.Mock()
        monkeypatch.setattr(harness_services, "dispatch_harness_job", dispatch_mock)
        repo = make_repo(workspace, enabled=True)
        with django_capture_on_commit_callbacks(execute=True):
            job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        dispatch_mock.assert_called_once_with(job.id)

    def test_sensitivity_snapshotted_from_sensitive_repo(self, workspace, owner):
        repo = make_repo(workspace, enabled=True, sensitive=True)
        job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        assert job.sensitivity == Memory.Sensitivity.PRIVATE

    def test_sensitivity_is_not_re_derived_after_repo_flag_changes(self, workspace, owner):
        repo = make_repo(workspace, enabled=True, sensitive=False)
        job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        repo.sensitive = True
        repo.save(update_fields=["sensitive"])
        job.refresh_from_db()
        assert job.sensitivity == Memory.Sensitivity.NORMAL

    def test_disabled_repo_is_rejected(self, workspace, owner):
        repo = make_repo(workspace, enabled=False)
        with pytest.raises(HarnessInvalidTransition):
            create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        assert not HarnessJob.objects.filter(workspace=workspace).exists()

    def test_unselectable_agent_is_rejected(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        with pytest.raises(HarnessInvalidTransition):
            create_harness_job(
                workspace=workspace,
                actor=owner,
                repo=repo,
                goal="find bug",
                agent=HarnessJob.Agent.CODEX_CLI,
            )

    def test_create_writes_audit_row(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")
        entry = AuditLog.objects.get(action="harness.job_created")
        assert entry.resource_id == str(job.id)

    @pytest.mark.parametrize("role", [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER])
    def test_non_curator_cannot_create(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        with pytest.raises(HarnessPermissionDenied):
            create_harness_job(workspace=workspace, actor=user, repo=repo, goal="find bug")
        assert not HarnessJob.objects.filter(workspace=workspace).exists()

    def test_repo_from_a_different_workspace_is_rejected(self, workspace, owner):
        other = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(workspace=other, user=owner, role=WorkspaceMember.Role.OWNER)
        repo = make_repo(other, enabled=True)
        with pytest.raises(HarnessPermissionDenied):
            create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="find bug")


def make_job(workspace, repo, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "find the bug",
        "status": HarnessJob.Status.PENDING,
    }
    fields.update(overrides)
    return HarnessJob.objects.create(**fields)


class TestListGetHarnessJob:
    def test_curator_can_list(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        make_job(workspace, repo)
        jobs = list_jobs(workspace=workspace, actor=owner)
        assert jobs.count() == 1

    @pytest.mark.parametrize(
        "role",
        [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER],
    )
    def test_non_curator_cannot_list(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        make_job(workspace, repo)
        with pytest.raises(HarnessPermissionDenied):
            list_jobs(workspace=workspace, actor=user)

    def test_curator_can_get(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        fetched = get_job(workspace=workspace, actor=owner, job_id=job.id)
        assert fetched.id == job.id

    def test_non_curator_get_raises(self, workspace):
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        with pytest.raises(HarnessPermissionDenied):
            get_job(workspace=workspace, actor=member, job_id=job.id)

    def test_get_out_of_scope_returns_none_not_error(self, workspace, owner):
        other_workspace = Workspace.objects.create(name="Other", owner=owner)
        WorkspaceMember.objects.create(
            workspace=other_workspace, user=owner, role=WorkspaceMember.Role.OWNER
        )
        other_repo = make_repo(other_workspace, enabled=True)
        job = make_job(other_workspace, other_repo)
        # owner is a curator of `workspace` too, but this job belongs to a
        # different workspace than the one passed -- for_actor's workspace
        # filter must exclude it.
        assert get_job(workspace=workspace, actor=owner, job_id=job.id) is None


class TestCancelHarnessJob:
    def test_curator_cancels_pending_job_directly(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        cancelled = cancel_harness_job(job=job, actor=owner)
        assert cancelled.status == HarnessJob.Status.CANCELLED
        assert cancelled.failure_reason == HarnessJob.FailureReason.KILLED
        assert cancelled.cancel_requested is False

    def test_curator_requests_cancel_on_running_job_without_touching_docker(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo, status=HarnessJob.Status.RUNNING)
        result = cancel_harness_job(job=job, actor=owner)
        assert result.status == HarnessJob.Status.RUNNING
        assert result.cancel_requested is True

    @pytest.mark.parametrize(
        "status",
        [HarnessJob.Status.SUCCEEDED, HarnessJob.Status.FAILED, HarnessJob.Status.CANCELLED],
    )
    def test_cancelling_a_terminal_job_is_a_no_op(self, workspace, owner, status):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo, status=status)
        result = cancel_harness_job(job=job, actor=owner)
        assert result.status == status
        assert result.cancel_requested is False

    def test_cancel_writes_audit_row(self, workspace, owner):
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        cancel_harness_job(job=job, actor=owner)
        assert AuditLog.objects.filter(action="harness.job_cancelled").exists()

    @pytest.mark.parametrize("role", [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER])
    def test_non_curator_cannot_cancel(self, workspace, role):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        repo = make_repo(workspace, enabled=True)
        job = make_job(workspace, repo)
        with pytest.raises(HarnessPermissionDenied):
            cancel_harness_job(job=job, actor=user)
        job.refresh_from_db()
        assert job.status == HarnessJob.Status.PENDING

    def test_forged_workspace_attribute_cannot_bypass_curator_check(self, owner):
        """Same bug class as `edit_repo`/`delete_repo` (Codex code-review
        P2, branch 1 round 1) -- authorize after locking, not before."""
        workspace_a = Workspace.objects.create(name="A", owner=owner)
        WorkspaceMember.objects.create(
            workspace=workspace_a, user=owner, role=WorkspaceMember.Role.OWNER
        )
        repo_a = make_repo(workspace_a, enabled=True)
        job_a = make_job(workspace_a, repo_a)

        attacker = register_user(email="attacker2@test.dev", password=PASSWORD, name="Attacker")
        workspace_b = Workspace.objects.get(owner=attacker)
        WorkspaceMember.objects.create(
            workspace=workspace_a, user=attacker, role=WorkspaceMember.Role.MEMBER
        )

        forged = HarnessJob.objects.get(pk=job_a.pk)
        forged.workspace = workspace_b

        with pytest.raises(HarnessPermissionDenied):
            cancel_harness_job(job=forged, actor=attacker)
        job_a.refresh_from_db()
        assert job_a.status == HarnessJob.Status.PENDING


class TestDispatchHarnessJob:
    def test_eager_mode_lets_task_exceptions_propagate(self, monkeypatch, settings):
        """Must NOT swallow the task's own exceptions under
        CELERY_TASK_ALWAYS_EAGER (mirrors
        memory.jobs.dispatch_extraction_job's identical reasoning) -- eager
        mode never touches a real broker, so there is nothing broker-shaped
        to catch."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        import uuid

        with mock.patch("harness.tasks.run_harness_job") as task_mock:
            task_mock.delay.side_effect = RuntimeError("task body bug")
            with pytest.raises(RuntimeError):
                dispatch_harness_job(uuid.uuid4())

    def test_non_eager_broker_failure_is_swallowed(self, monkeypatch, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        import uuid

        with mock.patch("harness.tasks.run_harness_job") as task_mock:
            task_mock.delay.side_effect = ConnectionError("redis down")
            dispatch_harness_job(uuid.uuid4())  # must not raise
        task_mock.delay.assert_called_once()
