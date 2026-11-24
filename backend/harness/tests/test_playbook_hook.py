"""Phase 12 D17: the harness terminal hook records a playbook source only for
a `succeeded` job whose review approved it, whose tests passed and whose
goal the owner typed -- never for failed/cancelled jobs, agent- or
member-typed goals, or sensitive repos; `.claude/skills/` paths never reach
the port; a failing port never touches the harness state machine. Ends with
the branch's "done when": three fake jobs -> one provenance-checked proposal
-> approved -> attached to a new implementation request."""

import json
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.adapters import fake
from ai_core.models import ModelProvider
from core.channels import CHANNEL_WEB
from harness import ingestion
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from skills import playbooks, ports, services
from skills.models import PlaybookDistillRun, PlaybookSource
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
GOALS = (
    "acme-api: migration ekle ve testleri make test ile çalıştır",
    "acme-api: yeni bir migration ekle, make test ile doğrula",
    "acme-api: alan için migration ekle; make test geçmeli",
)


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="acme-api",
        git_url="git@example.com:acme/api.git",
        enabled=True,
        test_command="make test",
    )


def make_done_job(workspace, repo, owner, *, goal=GOALS[0], verdict="approved", **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": goal,
        "status": HarnessImplementationJob.Status.SUCCEEDED,
        "created_by": owner,
        "changed_files": ["backend/app/migrations/0002_x.py", ".claude/skills/pr-yaz/SKILL.md"],
        "test_result": {"ran": True, "exit_code": 0, "passed": True, "command": "make test"},
        "diff_summary": "Added a migration.",
    }
    fields.update(overrides)
    job = HarnessImplementationJob.objects.create(**fields)
    if verdict is not None:
        HarnessReview.objects.create(
            workspace=workspace,
            implementation_job=job,
            agent=HarnessReview.Agent.CODEX_CLI,
            status=HarnessReview.Status.SUCCEEDED,
            verdict=verdict,
            summary="Looks fine.",
        )
    return job


def test_a_succeeded_approved_tested_owner_job_is_recorded(workspace, repo, owner):
    job = make_done_job(workspace, repo, owner)
    ingestion.record_implementation_outcome(job)
    source = PlaybookSource.objects.get()
    assert source.workspace == workspace and source.job_id == job.id
    assert source.repo_key == str(repo.id) and source.repo_label == "acme-api"
    assert source.goal == GOALS[0]
    assert source.test_command == "make test"
    assert source.changed_paths == ["backend/app/migrations/0002_x.py"]
    assert "Added a migration." in source.context_text and "Looks fine." in source.context_text
    # Idempotent: the terminal hooks can run more than once.
    ingestion.record_implementation_outcome(job)
    assert PlaybookSource.objects.count() == 1


@pytest.mark.parametrize(
    ("overrides", "verdict"),
    [
        ({"status": HarnessImplementationJob.Status.FAILED}, "approved"),
        ({"status": HarnessImplementationJob.Status.CANCELLED}, "approved"),
        ({}, "changes_requested"),
        ({}, None),
        ({"test_result": {"ran": True, "exit_code": 1, "passed": False}}, "approved"),
        ({"test_result": {}}, "approved"),
        ({"created_by": None}, "approved"),
    ],
)
def test_nothing_is_recorded_otherwise(workspace, repo, owner, overrides, verdict):
    job = make_done_job(workspace, repo, owner, verdict=verdict, **overrides)
    ingestion.record_implementation_outcome(job)
    assert not PlaybookSource.objects.exists()


def test_a_member_typed_goal_is_not_recorded(workspace, repo, owner):
    member = register_user(email="m@test.dev", password=PASSWORD, name="M")
    WorkspaceMember.objects.create(
        workspace=workspace, user=member, role=WorkspaceMember.Role.ADMIN
    )
    job = make_done_job(workspace, repo, owner, created_by=member)
    ingestion.record_implementation_outcome(job)
    assert not PlaybookSource.objects.exists()


def test_an_agent_typed_goal_is_not_recorded(workspace, repo, owner):
    owner.is_agent = True
    owner.save(update_fields=["is_agent"])
    job = make_done_job(workspace, repo, owner)
    ingestion.record_implementation_outcome(job)
    assert not PlaybookSource.objects.exists()


def test_sensitive_repos_stay_out(workspace, repo, owner):
    repo.sensitive = True
    repo.save(update_fields=["sensitive"])
    job = make_done_job(workspace, repo, owner)
    ingestion.record_implementation_outcome(job)
    assert not PlaybookSource.objects.exists()


def test_the_port_never_receives_a_skill_path(workspace, repo, owner, monkeypatch):
    spy = mock.Mock(wraps=ports.record_playbook_source)
    monkeypatch.setattr(ingestion.skills_ports, "record_playbook_source", spy)
    job = make_done_job(workspace, repo, owner)
    ingestion.record_implementation_outcome(job)
    paths = spy.call_args.kwargs["changed_paths"]
    assert paths == ["backend/app/migrations/0002_x.py"]
    assert not any(".claude/skills" in path for path in paths)


def test_a_failing_port_never_reaches_the_state_machine(workspace, repo, owner, monkeypatch):
    monkeypatch.setattr(
        ingestion.skills_ports,
        "record_playbook_source",
        mock.Mock(side_effect=RuntimeError("boom")),
    )
    job = make_done_job(workspace, repo, owner)
    ingestion.record_implementation_outcome(job)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.SUCCEEDED
    assert not PlaybookSource.objects.exists()


def test_three_fake_jobs_make_one_proposal_that_can_be_attached(
    workspace, repo, owner, settings, monkeypatch, web_client
):
    """The branch's "done when": three successful jobs on one repo -> one
    provenance-checked playbook proposal; approved, it can be attached to a
    new implementation request (Gate A lists it by name and hash)."""
    settings.AI_FAKE_PROVIDER = True
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    jobs = [make_done_job(workspace, repo, owner, goal=goal) for goal in GOALS]
    for job in jobs:
        ingestion.record_implementation_outcome(job)
    monkeypatch.setattr(
        fake,
        "FAKE_SKILLS_PLAYBOOK_DISTILL_RESPONSE",
        json.dumps(
            {
                "name": "acme-api-migration",
                "description": "acme-api'ye migration eklerken kullan.",
                "steps": [
                    {
                        "text": "Migration ekle.",
                        "source": str(jobs[0].id),
                        "quote": "migration ekle",
                        "paths": ["backend/app/migrations/0002_x.py"],
                    },
                    {
                        "text": "make test çalıştır.",
                        "source": str(jobs[1].id),
                        "quote": "make test",
                    },
                    {"text": "Özet.", "source": str(jobs[2].id), "quote": "Added a migration."},
                ],
            }
        ),
    )
    assert playbooks.distill_playbooks() == {PlaybookDistillRun.Outcome.PROPOSED: 1}
    version = PlaybookDistillRun.objects.get().version
    assert [step["source"] for step in version.provenance["steps"]] == [
        str(jobs[0].id),
        str(jobs[1].id),
    ]
    services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256=version.content_sha256
    )
    response = web_client(owner).post(
        "/api/v1/harness/implementation-jobs",
        {
            "workspace": str(workspace.id),
            "repo": str(repo.id),
            "goal": "acme-api: başka bir migration ekle",
            "skill_versions": [str(version.id)],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["skills"] == [
        {
            "id": str(version.id),
            "name": "acme-api-migration",
            "version": 1,
            "sha256": version.content_sha256,
        }
    ]
