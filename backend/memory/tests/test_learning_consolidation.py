"""Phase 8A branch 5 (brief D10, D13): consolidation, the nightly and weekly
caps, dedupe, schema errors and budgets. FakeAdapter with captured payloads;
no real model."""

import json
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from ai_core.models import AIBudget, ModelUsage
from memory.ingestion import consolidation
from memory.ingestion.consolidation import (
    MAX_PROPOSALS_PER_NIGHT,
    ConsolidationParseError,
    consolidate_workspace,
    parse_consolidation,
)
from memory.models import (
    CoreBlockProposal,
    CoreMemoryBlock,
    EpisodeExtractionJob,
    IngestionCounter,
    IngestionSource,
    Memory,
    compute_fingerprint,
)
from memory.tests.learning_helpers import capture_llm, fake_provider, payload_text, seed_prompts

pytestmark = pytest.mark.django_db

Kind = IngestionSource.Kind


@pytest.fixture(autouse=True)
def ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    seed_prompts()
    fake_provider()


@pytest.fixture
def llm(monkeypatch):
    return capture_llm(monkeypatch)


@pytest.fixture
def git(sources):
    source = sources[Kind.GIT]
    source.enabled = True
    source.save()
    return source


def candidate(source, index, *, target="memory", content=None, quote=None, sensitivity="normal"):
    return {
        "type": "semantic",
        "title": f"Learned {index}",
        "content": content or f"The owner prefers approach number {index}.",
        "quote": quote or f"owner words number {index} verbatim",
        "episode_id": str(uuid.uuid4()),
        "source_id": str(source.id),
        "target": target,
        "sensitivity": sensitivity,
    }


def waiting_job(workspace, candidates, *, personal_data=False):
    return EpisodeExtractionJob.objects.create(
        workspace=workspace,
        night=timezone.localdate(),
        personal_data=personal_data,
        status=EpisodeExtractionJob.Status.SUCCEEDED,
        candidates=candidates,
    )


def learned_rows():
    return Memory.objects.filter(ingestion_source__isnull=False).count() + (
        CoreBlockProposal.objects.filter(ingestion_source__isnull=False).count()
    )


# --- caps (d6) ------------------------------------------------------------------


def test_fifteen_candidates_become_at_most_ten_proposals(git, personal, llm):
    waiting_job(personal, [candidate(git, i) for i in range(15)])
    assert consolidate_workspace(personal.id) == MAX_PROPOSALS_PER_NIGHT == 10
    assert Memory.objects.filter(status=Memory.Status.PENDING_APPROVAL).count() == 10
    assert not Memory.objects.filter(status=Memory.Status.APPROVED).exists()
    counter = IngestionCounter.objects.get(source=git)
    assert counter.proposals_created == 10
    assert counter.learning_drops["nightly_cap"] == 5
    job = EpisodeExtractionJob.objects.get()
    assert job.consolidated_at is not None and job.candidates == []  # discarded, not queued
    assert len(llm.of("memory.consolidation")) == 1


def test_the_nightly_cap_is_shared_across_the_owners_workspaces(git, personal, firm, llm):
    waiting_job(personal, [candidate(git, i) for i in range(7)])
    waiting_job(firm, [candidate(git, 100 + i) for i in range(7)])
    created = consolidate_workspace(personal.id) + consolidate_workspace(firm.id)
    assert created == 10 == learned_rows()
    assert Memory.objects.filter(workspace=personal).count() == 7
    assert Memory.objects.filter(workspace=firm).count() == 3


def test_a_new_night_has_a_fresh_cap(git, personal, llm):
    waiting_job(personal, [candidate(git, i) for i in range(10)])
    consolidate_workspace(personal.id)
    Memory.objects.update(created_at=timezone.now() - timedelta(days=1))
    waiting_job(personal, [candidate(git, 50 + i) for i in range(3)])
    assert consolidate_workspace(personal.id) == 3


def test_a_fourth_core_block_proposal_in_a_week_is_discarded(git, personal, llm):
    labels = ["current_focus", "active_projects", "preferences", "developer"]
    waiting_job(
        personal,
        [candidate(git, i, target=label) for i, label in enumerate(labels)],
    )
    assert consolidate_workspace(personal.id) == 3
    assert CoreBlockProposal.objects.count() == 3
    assert all(p.ingestion_source == git for p in CoreBlockProposal.objects.all())
    assert IngestionCounter.objects.get(source=git).learning_drops["weekly_cap"] == 1
    # Nothing is applied: every block is still empty.
    assert not CoreMemoryBlock.objects.filter(version__gt=0).exists()


def test_core_block_proposal_appends_to_current_content_and_keeps_the_quote(git, personal, llm):
    CoreMemoryBlock.objects.create(
        workspace=personal,
        label="current_focus",
        content="Phase 8A learning loop.",
        version=1,
        updated_by_kind="owner",
    )
    waiting_job(
        personal,
        [candidate(git, 1, target="current_focus", content="Hook relay runbook.")],
    )
    consolidate_workspace(personal.id)
    proposal = CoreBlockProposal.objects.get()
    assert proposal.proposed_content == "Phase 8A learning loop.\nHook relay runbook."
    assert proposal.base_version == 1 and proposal.inferred is True
    assert proposal.provenance_quote == "owner words number 1 verbatim"
    assert proposal.status == CoreBlockProposal.Status.PENDING


# --- dedupe -------------------------------------------------------------------------


def test_fingerprint_duplicates_are_dropped_before_the_model(git, personal, llm):
    Memory.objects.create(
        workspace=personal,
        memory_type="semantic",
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        title="Known",
        content="Already known fact.",
        fingerprint=compute_fingerprint("Already known fact."),
    )
    waiting_job(
        personal,
        [
            candidate(git, 1, content="Already known fact."),
            candidate(git, 2, content="Brand new fact."),
            candidate(git, 3, content="Brand  new fact."),
        ],
    )
    assert consolidate_workspace(personal.id) == 1
    text = payload_text(llm.of("memory.consolidation")[0])
    assert text.count("<candidate index=") == 1
    assert IngestionCounter.objects.get(source=git).learning_drops["duplicate"] == 2


def test_the_model_selects_merges_and_ranks_but_quotes_stay_code_held(git, personal, llm):
    waiting_job(personal, [candidate(git, i) for i in range(3)])
    llm.overrides["memory.consolidation"] = json.dumps(
        {
            "version": 1,
            "proposals": [
                {"index": 2, "title": "Merged", "content": "Merged statement of 2 and 0."},
                {"index": 1, "title": None, "content": None},
            ],
        }
    )
    assert consolidate_workspace(personal.id) == 2
    rows = {m.title: m for m in Memory.objects.all()}
    assert rows["Merged"].content == "Merged statement of 2 and 0."
    assert rows["Merged"].provenance_quote == "owner words number 2 verbatim"
    assert rows["Learned 1"].provenance_quote == "owner words number 1 verbatim"
    assert IngestionCounter.objects.get(source=git).learning_drops["merged"] == 1


def test_private_candidates_are_consolidated_with_personal_data_and_stay_private(
    git, personal, llm, monkeypatch
):
    waiting_job(personal, [candidate(git, 1, sensitivity="private")], personal_data=True)
    waiting_job(personal, [candidate(git, 2)])
    flags = []
    from ai_core.gateway import InferenceGateway

    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        flags.append((kwargs.get("personal_data"), payload_text(messages)))
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    assert consolidate_workspace(personal.id) == 2
    assert len(flags) == 2  # never batched together
    private_call = next(text for flag, text in flags if flag)
    assert "number 1" in private_call and "number 2" not in private_call
    assert Memory.objects.get(title="Learned 1").sensitivity == Memory.Sensitivity.PRIVATE


def test_a_private_candidate_targeting_a_block_never_becomes_block_content(git, personal, llm):
    """Defence in depth for candidates already waiting: a private
    candidate aimed at a core block yields no proposal, so its content never
    lands in a cloud-eligible (normal) block."""
    waiting_job(
        personal,
        [candidate(git, 1, target="preferences", sensitivity="private")],
        personal_data=True,
    )
    assert consolidate_workspace(personal.id) == 0
    assert not CoreBlockProposal.objects.exists()
    assert not CoreMemoryBlock.objects.filter(
        workspace=personal,
        sensitivity=Memory.Sensitivity.NORMAL,
        content__contains="approach number 1",
    ).exists()
    drops = IngestionCounter.objects.get(source=git).learning_drops
    assert drops["private_block_target"] == 1


@pytest.mark.parametrize("state", ["forgotten", "paused", "disabled"])
def test_candidates_of_an_inactive_source_become_no_proposals(git, personal, llm, state):
    waiting_job(personal, [candidate(git, 1), candidate(git, 2, target="current_focus")])
    now = timezone.now()
    if state == "forgotten":
        git.forgotten_at, git.paused_at = now, now
    elif state == "paused":
        git.paused_at = now
    else:
        git.enabled = False
    git.save()
    assert consolidate_workspace(personal.id) == 0
    assert learned_rows() == 0
    reason = "source_forgotten" if state == "forgotten" else "source_paused"
    assert IngestionCounter.objects.get(source=git).learning_drops[reason] == 2
    assert EpisodeExtractionJob.objects.get().candidates == []
    assert not llm.of("memory.consolidation")  # no spend on an inactive source


def test_a_source_forgotten_after_the_claim_yields_no_proposals(git, personal, llm, monkeypatch):
    waiting_job(personal, [candidate(git, 1)])
    original = consolidation.dedupe

    def forget_then_dedupe(*args, **kwargs):
        IngestionSource.objects.filter(pk=git.pk).update(
            forgotten_at=timezone.now(), paused_at=timezone.now()
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(consolidation, "dedupe", forget_then_dedupe)
    assert consolidate_workspace(personal.id) == 0
    assert learned_rows() == 0
    assert IngestionCounter.objects.get(source=git).learning_drops["source_forgotten"] == 1


def test_existing_private_memories_never_reach_the_payload(git, personal, llm):
    Memory.objects.create(
        workspace=personal,
        memory_type="semantic",
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        sensitivity=Memory.Sensitivity.PRIVATE,
        title="Private note",
        content="Very private content.",
        fingerprint=compute_fingerprint("Very private content."),
    )
    waiting_job(personal, [candidate(git, 1)])
    consolidate_workspace(personal.id)
    assert "Very private content" not in payload_text(llm.of("memory.consolidation")[0])


# --- waiting, failures and budgets ------------------------------------------------


def test_consolidation_waits_while_an_extraction_job_is_open(git, personal, llm):
    waiting_job(personal, [candidate(git, 1)])
    open_job = EpisodeExtractionJob.objects.create(
        workspace=personal, night=timezone.localdate(), episode_ids=[]
    )
    assert consolidate_workspace(personal.id) == 0
    assert not llm.calls
    open_job.status = EpisodeExtractionJob.Status.SUCCEEDED
    open_job.save()
    assert consolidate_workspace(personal.id) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "nope",
        json.dumps({"version": 2, "proposals": []}),
        json.dumps({"version": 1, "proposals": [{"index": 5, "title": None, "content": None}]}),
        json.dumps({"version": 1, "proposals": [{"index": True, "title": None, "content": None}]}),
        json.dumps(
            {
                "version": 1,
                "proposals": [
                    {"index": 0, "title": None, "content": None},
                    {"index": 0, "title": None, "content": None},
                ],
            }
        ),
        json.dumps({"version": 1, "proposals": [{"index": 0, "title": "", "content": None}]}),
        json.dumps({"version": 1, "proposals": [{"index": 0, "quote": "x"}]}),
    ],
)
def test_consolidation_schema_violations(raw):
    with pytest.raises(ConsolidationParseError):
        parse_consolidation(raw, 2)


def test_a_consolidation_schema_error_discards_terminally(git, personal, llm):
    waiting_job(personal, [candidate(git, 1), candidate(git, 2)])
    llm.overrides["memory.consolidation"] = '{"version": 1, "proposals": "all"}'
    assert consolidate_workspace(personal.id) == 0
    assert not Memory.objects.exists()
    job = EpisodeExtractionJob.objects.get()
    assert job.consolidated_at is not None and job.candidates == []
    drops = IngestionCounter.objects.get(source=git).learning_drops
    assert drops["consolidation_schema_invalid"] == 2
    assert consolidate_workspace(personal.id) == 0  # nothing retried
    assert len(llm.of("memory.consolidation")) == 1


def test_a_budget_refusal_keeps_candidates_for_the_next_night(git, personal, llm):
    provider = fake_provider(priced=True, name="fake-priced")
    AIBudget.objects.create(name="learning", purposes=["memory.consolidation"], monthly_limit_usd=1)
    ModelUsage.objects.create(
        purpose="memory.consolidation", provider=provider, success=True, estimated_cost=5
    )
    waiting_job(personal, [candidate(git, 1)])
    assert consolidate_workspace(personal.id) == 0
    assert not llm.calls
    assert ModelUsage.objects.filter(error_type="budget_exceeded").count() == 1
    job = EpisodeExtractionJob.objects.get()
    assert job.consolidated_at is None and len(job.candidates) == 1
    assert job.consolidation_token is None  # released
    AIBudget.objects.all().delete()
    assert consolidation.consolidate_waiting() == 1


def test_one_model_usage_per_consolidation_call_without_text(git, personal, llm):
    waiting_job(personal, [candidate(git, 1, content="Distinctive candidate sentence.")])
    consolidate_workspace(personal.id)
    usage = ModelUsage.objects.get(purpose="memory.consolidation")
    assert usage.context_report["candidate_count"] == 1
    assert usage.context_report["source_kinds"] == ["git"]
    assert "Distinctive" not in json.dumps(usage.context_report)


def test_extraction_jobs_do_not_outlive_the_episode_ttl(git, personal, firm, settings):
    from memory.ingestion.services import purge_stale_extraction_jobs

    old_personal = waiting_job(personal, [candidate(git, 1)])
    old_firm = waiting_job(firm, [candidate(git, 2)])
    fresh = waiting_job(personal, [candidate(git, 3)])
    EpisodeExtractionJob.objects.filter(pk=old_personal.pk).update(
        created_at=timezone.now() - timedelta(days=settings.MEMORY_EPISODE_TTL_DAYS_PERSONAL + 1)
    )
    EpisodeExtractionJob.objects.filter(pk=old_firm.pk).update(
        created_at=timezone.now() - timedelta(days=settings.MEMORY_EPISODE_TTL_DAYS_FIRM + 1)
    )
    assert purge_stale_extraction_jobs() == 2
    assert list(EpisodeExtractionJob.objects.values_list("pk", flat=True)) == [fresh.pk]


def test_dispatch_runs_inline_in_eager_mode_and_swallows_broker_errors(settings, personal):
    with mock.patch("memory.ingestion.tasks.consolidate_workspace_task.delay") as delay:
        consolidation.dispatch_consolidation(personal.id)
    delay.assert_called_once()
    settings.CELERY_TASK_ALWAYS_EAGER = False
    with mock.patch(
        "memory.ingestion.tasks.consolidate_workspace_task.delay", side_effect=OSError("down")
    ):
        consolidation.dispatch_consolidation(personal.id)  # must not raise
