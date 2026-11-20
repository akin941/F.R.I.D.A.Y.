"""Phase 8A branch 4 (brief D5): the harness-outcome producer. Every terminal
discovery and implementation job becomes one `harness` episode through the
memory port: the owner-typed goal is `owner_text`, the model-produced
summaries are bounded `context_text`, failed and cancelled jobs carry their
status, and an ingestion failure never reaches the harness state machine.
All runner calls are mocked (no git, no Docker)."""

import time
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from approvals.models import ApprovalRequest
from harness import execution, implementation_execution, ingestion, services
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo, HarnessReview
from memory.ingestion.services import ensure_default_sources
from memory.models import Episode, IngestionCounter, IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
REPO_RULE = WorkspaceMappingRule.RuleType.REPO


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture(autouse=True)
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def briefing_prompt():
    template = PromptTemplate.objects.create(
        name="friday_harness_briefing", purpose="harness.briefing"
    )
    version = PromptVersion.objects.create(
        template=template, version=1, content="Summarize the transcript."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def harness_source(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    source = IngestionSource.objects.get(workspace=personal, kind=IngestionSource.Kind.HARNESS)
    source.enabled = True
    source.save()
    return source


@pytest.fixture
def repo(personal, harness_source):
    repo = HarnessRepo.objects.create(
        workspace=personal, name="friday", git_url="git@example.com:x/y.git", enabled=True
    )
    add_rule(harness_source, REPO_RULE, str(repo.id), personal)
    return repo


def make_job(workspace, repo, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "Neden scrubber yavaş, bul ve açıkla",
        "agent": HarnessJob.Agent.CLAUDE_CODE,
        "status": HarnessJob.Status.PENDING,
    }
    fields.update(overrides)
    return HarnessJob.objects.create(**fields)


def _claude_json():
    import json

    return json.dumps({"total_cost_usd": "0.02", "usage": {"input_tokens": 10, "output_tokens": 5}})


def _run(job, monkeypatch, *, exit_code=0, logs=None):
    monkeypatch.setattr(execution.runner, "clone_repo", mock.Mock(return_value="/tmp/clone"))
    monkeypatch.setattr(execution.runner, "cleanup_directory", mock.Mock())
    monkeypatch.setattr(execution.runner, "image_for_agent", mock.Mock(return_value="img:1"))
    monkeypatch.setattr(execution.runner, "launch_container", mock.Mock())
    monkeypatch.setattr(execution.runner, "remove_container", mock.Mock())
    monkeypatch.setattr(execution.runner, "container_is_running", mock.Mock(side_effect=[False]))
    monkeypatch.setattr(execution.runner, "container_exit_code", mock.Mock(return_value=exit_code))
    monkeypatch.setattr(
        execution.runner,
        "capture_logs",
        mock.Mock(return_value=_claude_json() if logs is None else logs),
    )
    execution.run_harness_job(job.pk)
    job.refresh_from_db()
    return job


def _episode(job_id, kind="harness.discovery"):
    return Episode.objects.get(kind=kind, metadata__job_id=str(job_id))


# --- discovery jobs --------------------------------------------------------------


def test_succeeded_job_goal_is_owner_text_and_briefing_is_context(
    owner, personal, repo, monkeypatch, briefing_prompt, harness_source
):
    job = _run(make_job(personal, repo, created_by=owner), monkeypatch)
    assert job.status == HarnessJob.Status.SUCCEEDED
    episode = _episode(job.id)
    assert episode.owner_text == job.goal
    assert job.briefing["summary"][:100] in episode.context_text
    assert episode.workspace_id == personal.id
    assert episode.source_id == harness_source.id
    assert episode.external_id == f"harness:{job.id}"
    assert episode.metadata["status"] == "succeeded"
    assert episode.metadata["agent"] == "claude_code"
    assert episode.metadata["repo_id"] == str(repo.id)
    assert episode.metadata["cost_usd"] == "0.02"
    assert episode.metadata["owner_authored_goal"] is True
    # The CLI transcript itself never becomes episode text.
    assert job.briefing["raw_transcript"] not in episode.context_text


def test_failed_job_produces_an_episode_with_its_status(
    owner, personal, repo, monkeypatch, briefing_prompt
):
    job = _run(make_job(personal, repo, created_by=owner), monkeypatch, exit_code=1, logs="boom")
    assert job.status == HarnessJob.Status.FAILED
    episode = _episode(job.id)
    assert episode.metadata["status"] == "failed"
    assert episode.metadata["failure_reason"] == job.failure_reason
    assert episode.owner_text == job.goal


def test_cancelled_pending_job_produces_an_episode(owner, personal, repo):
    job = make_job(personal, repo, created_by=owner)
    services.cancel_harness_job(job=job, actor=owner)
    episode = _episode(job.id)
    assert episode.metadata["status"] == "cancelled"
    assert episode.metadata["failure_reason"] == HarnessJob.FailureReason.KILLED


def test_recovered_and_unexpected_failures_produce_episodes(owner, personal, repo, monkeypatch):
    monkeypatch.setattr(execution.runner, "remove_container", mock.Mock())
    monkeypatch.setattr(execution.runner, "stop_container", mock.Mock())
    monkeypatch.setattr(execution.runner, "kill_container", mock.Mock())
    job = make_job(personal, repo, created_by=owner, status=HarnessJob.Status.RUNNING)
    execution.resolve_running_job_after_unexpected_error(job.pk)
    assert _episode(job.id).metadata["status"] == "failed"

    from datetime import timedelta

    from django.utils import timezone

    stale = make_job(
        personal,
        repo,
        created_by=owner,
        status=HarnessJob.Status.RUNNING,
        started_at=timezone.now() - timedelta(days=2),
    )
    execution.recover_stale_harness_jobs()
    assert _episode(stale.id).metadata["status"] == "failed"


def test_goal_of_a_non_owner_is_context_only(owner, personal, repo, monkeypatch, briefing_prompt):
    colleague = register_user(email="colleague@test.dev", password=PASSWORD, name="C")
    job = _run(make_job(personal, repo, created_by=colleague), monkeypatch)
    episode = _episode(job.id)
    assert episode.owner_text == ""
    assert "not owner-authored" in episode.context_text
    assert episode.metadata["owner_authored_goal"] is False


def test_agent_created_job_goal_is_context_only(owner, personal, repo):
    agent = register_user(email="agent@test.dev", password=PASSWORD, name="Agent")
    agent.is_agent = True
    agent.save()
    job = make_job(personal, repo, created_by=agent, status=HarnessJob.Status.FAILED)
    ingestion.record_discovery_outcome(job)
    assert _episode(job.id).owner_text == ""


def test_briefing_summary_is_truncated(owner, personal, repo):
    job = make_job(
        personal,
        repo,
        created_by=owner,
        status=HarnessJob.Status.SUCCEEDED,
        briefing={"summary": "x" * 5000, "findings": [], "raw_transcript": "raw"},
    )
    ingestion.record_discovery_outcome(job)
    episode = _episode(job.id)
    assert episode.context_text.count("x") == ingestion.CONTEXT_SUMMARY_MAX_CHARS
    assert "raw" not in episode.context_text


def test_sensitive_job_episode_is_private(owner, personal, repo):
    job = make_job(
        personal, repo, created_by=owner, status=HarnessJob.Status.FAILED, sensitivity="private"
    )
    ingestion.record_discovery_outcome(job)
    assert _episode(job.id).sensitivity == "private"


def test_non_terminal_job_records_nothing(owner, personal, repo):
    job = make_job(personal, repo, created_by=owner, status=HarnessJob.Status.RUNNING)
    assert ingestion.record_discovery_outcome(job) is None
    assert not Episode.all_objects.exists()


def test_recording_twice_is_a_duplicate(owner, personal, repo, harness_source):
    job = make_job(personal, repo, created_by=owner, status=HarnessJob.Status.FAILED)
    assert ingestion.record_discovery_outcome(job).status == "stored"
    assert ingestion.record_discovery_outcome(job).status == "duplicate"
    assert Episode.objects.count() == 1


def test_ingestion_failure_never_reaches_the_state_machine(
    owner, personal, repo, monkeypatch, briefing_prompt
):
    def explode(**kwargs):
        raise RuntimeError("memory is down")

    monkeypatch.setattr(ingestion.ports, "record_episode", explode)
    job = _run(make_job(personal, repo, created_by=owner), monkeypatch)
    assert job.status == HarnessJob.Status.SUCCEEDED
    # Also on the cancel path inside the service's own transaction.
    pending = make_job(personal, repo, created_by=owner)
    services.cancel_harness_job(job=pending, actor=owner)
    pending.refresh_from_db()
    assert pending.status == HarnessJob.Status.CANCELLED
    assert not Episode.all_objects.exists()


def test_paused_source_drops_and_counts(owner, personal, repo, harness_source):
    harness_source.enabled = False
    harness_source.save()
    job = make_job(personal, repo, created_by=owner, status=HarnessJob.Status.FAILED)
    assert ingestion.record_discovery_outcome(job).status == "dropped_paused"
    assert IngestionCounter.objects.get(source=harness_source).dropped_paused == 1


def test_unmapped_repo_is_dropped(owner, personal, harness_source):
    other = HarnessRepo.objects.create(
        workspace=personal, name="other", git_url="git@example.com:o/o.git", enabled=True
    )
    job = make_job(personal, other, created_by=owner, status=HarnessJob.Status.FAILED)
    assert ingestion.record_discovery_outcome(job).status == "dropped_unmapped"
    assert not Episode.all_objects.exists()


def test_firm_repo_outcome_lands_in_the_firm_workspace(owner, personal, harness_source):
    from core.channels import CHANNEL_WEB
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)
    firm_repo = HarnessRepo.objects.create(
        workspace=firm, name="acme", git_url="git@example.com:a/a.git", enabled=True
    )
    add_rule(harness_source, REPO_RULE, str(firm_repo.id), firm)
    job = make_job(firm, firm_repo, created_by=owner, status=HarnessJob.Status.SUCCEEDED)
    ingestion.record_discovery_outcome(job)
    assert _episode(job.id).workspace_id == firm.id
    assert not Episode.objects.filter(workspace=personal).exists()


# --- implementation jobs ---------------------------------------------------------


def make_impl(workspace, repo, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "Scrubber'a zaman bütçesi ekle",
        "agent": HarnessImplementationJob.Agent.CLAUDE_CODE,
    }
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


def test_implementation_outcome_records_goal_and_summaries(owner, personal, repo):
    job = make_impl(
        personal,
        repo,
        created_by=owner,
        status=HarnessImplementationJob.Status.SUCCEEDED,
        diff_summary="d" * 3000,
        test_result={"ran": True, "passed": True, "exit_code": 0},
    )
    HarnessReview.objects.create(
        workspace=personal,
        implementation_job=job,
        agent=HarnessReview.Agent.CODEX_CLI,
        status=HarnessReview.Status.SUCCEEDED,
        verdict=HarnessReview.Verdict.APPROVED,
        summary="Looks right.",
    )
    implementation_execution.record_implementation_work_log(job)
    episode = _episode(job.id, kind="harness.implementation")
    assert episode.owner_text == job.goal
    assert episode.context_text.count("d") >= ingestion.CONTEXT_SUMMARY_MAX_CHARS
    assert "d" * (ingestion.CONTEXT_SUMMARY_MAX_CHARS + 1) not in episode.context_text
    assert "Review summary: Looks right." in episode.context_text
    assert episode.metadata["status"] == "succeeded"
    assert episode.metadata["branch"] == job.branch_name
    assert episode.metadata["tests_passed"] is True
    assert episode.metadata["review_verdict"] == "approved"
    assert episode.external_id == f"harness-impl:{job.id}"


def test_failed_implementation_goes_through_the_work_log_hook(owner, personal, repo):
    job = make_impl(
        personal,
        repo,
        created_by=owner,
        status=HarnessImplementationJob.Status.FAILED,
        failure_reason=HarnessImplementationJob.FailureReason.TESTS_FAILED,
    )
    implementation_execution.record_implementation_work_log(job)
    episode = _episode(job.id, kind="harness.implementation")
    assert episode.metadata["status"] == "failed"
    assert episode.metadata["failure_reason"] == "tests_failed"
    assert episode.metadata["branch"] == ""


def test_gate_a_rejection_and_pre_gate_cancel_produce_episodes(owner, personal, repo):
    rejected = make_impl(personal, repo, created_by=owner)
    services.fail_implementation_job_for_gate_a_decision(
        rejected.pk, decision_status=ApprovalRequest.Status.REJECTED
    )
    assert _episode(rejected.id, kind="harness.implementation").metadata["failure_reason"] == (
        "gate_a_rejected"
    )

    cancelled = make_impl(personal, repo, created_by=owner)
    services.cancel_harness_implementation_job(job=cancelled, actor=owner)
    assert _episode(cancelled.id, kind="harness.implementation").metadata["status"] == "cancelled"


def test_implementation_ingestion_failure_never_raises(owner, personal, repo, monkeypatch):
    monkeypatch.setattr(
        ingestion.ports, "record_episode", mock.Mock(side_effect=RuntimeError("down"))
    )
    job = make_impl(personal, repo, created_by=owner, status=HarnessImplementationJob.Status.FAILED)
    implementation_execution.record_implementation_work_log(job)  # no exception
    assert not Episode.all_objects.exists()


def test_finalize_is_not_slowed_by_a_missing_source(owner, personal, monkeypatch, briefing_prompt):
    """No ingestion source registered at all (fresh install): the port drops
    the item and the job finalizes normally."""
    repo = HarnessRepo.objects.create(
        workspace=personal, name="r", git_url="git@example.com:r/r.git", enabled=True
    )
    started = time.monotonic()
    job = _run(make_job(personal, repo, created_by=owner), monkeypatch)
    assert job.status == HarnessJob.Status.SUCCEEDED
    assert not Episode.all_objects.exists()
    assert time.monotonic() - started < 30
