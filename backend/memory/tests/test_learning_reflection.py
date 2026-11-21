"""Phase 8B branch 2 (brief D5, D6): the weekly reflection -- at most 5
quote-verified proposals, `self`/`developer` dropped, the nightly 10 and
weekly core-block 3 shared, skipped under 5 approved items, once per ISO
week (a budget refusal is counted and never retried), and no private or
personal-class text in the payload. FakeAdapter with captured payloads."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from ai_core.models import AIBudget, ModelUsage
from audit.models import AuditLog
from memory.ingestion import reflection
from memory.ingestion.tasks import run_weekly_reflection_task
from memory.models import (
    CoreBlockProposal,
    CoreMemoryBlock,
    IngestionSource,
    Memory,
    MemoryFact,
    compute_fingerprint,
)
from memory.services import create_core_block_proposal
from memory.tests.graph_helpers import entity, fact
from memory.tests.ingestion_helpers import add_rule
from memory.tests.learning_helpers import (
    Rule,
    capture_llm,
    enable,
    episode,
    fake_provider,
    map_repo,
    payload_text,
    seed_prompts,
)

pytestmark = pytest.mark.django_db

Kind = IngestionSource.Kind
LINES = [f"Week note {i}: keep the billing api on Hetzner, move cron jobs to it" for i in range(7)]


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
def git(sources, personal, firm):
    enable(sources, Kind.GIT, Kind.CALENDAR)
    map_repo(sources, "r1", personal)
    map_repo(sources, "f1", firm)
    add_rule(sources[Kind.CALENDAR], Rule.CALENDAR_BINDING, "b1", personal)
    return sources[Kind.GIT]


def recently():
    return timezone.now() - timedelta(seconds=30)


def approved(workspace, count, *, sensitivity="normal", prefix="Approved"):
    rows = []
    for i in range(count):
        content = f"{prefix} memory {i} content."
        rows.append(
            Memory.objects.create(
                workspace=workspace,
                memory_type="semantic",
                status=Memory.Status.APPROVED,
                source=Memory.Source.AI_SUGGESTED,
                title=f"{prefix} {i}",
                content=content,
                fingerprint=compute_fingerprint(content),
                sensitivity=sensitivity,
            )
        )
    return rows


def week(git, count=7):
    return [episode(git, owner_text=LINES[i], occurred_at=recently()) for i in range(count)]


def candidate(episode_id, quote, target="memory", fact_obj=None, title="Pattern"):
    return {
        "type": "semantic",
        "title": title,
        "content": f"{title} content.",
        "quote": quote,
        "episode_id": str(episode_id),
        "target": target,
        "fact": fact_obj,
    }


def reply(*candidates):
    return json.dumps({"version": 2, "candidates": list(candidates)})


def learned_memories():
    return Memory.objects.filter(ingestion_source__isnull=False)


def test_skipped_under_five_approved_items(git, personal, llm):
    approved(personal, 4)
    week(git)
    result = reflection.reflect_workspace(personal)
    assert result["outcome"] == "too_few_items"
    assert not llm.of("memory.reflection")


def test_at_most_five_quoted_proposals_and_one_model_usage(git, personal, llm):
    approved(personal, 5)
    week(git)
    result = reflection.reflect_workspace(personal)
    assert result == {"workspace": str(personal.id), "outcome": "ok", "proposals_created": 5}
    rows = list(learned_memories())
    assert len(rows) == 5 and all(r.status == "pending_approval" for r in rows)
    assert all(r.provenance_quote in LINES for r in rows)
    assert ModelUsage.objects.filter(purpose="memory.reflection").count() == 1
    usage = ModelUsage.objects.get(purpose="memory.reflection")
    assert usage.prompt_version.template.name == "friday_memory_reflection"


def test_more_than_five_candidates_is_a_schema_error(git, personal, llm):
    approved(personal, 5)
    ids = week(git)
    llm.overrides["memory.reflection"] = reply(*[candidate(e, LINES[i]) for i, e in enumerate(ids)])
    assert reflection.reflect_workspace(personal)["outcome"] == "schema_invalid"
    assert not learned_memories().exists()


def test_quotes_are_verified_and_self_developer_targets_dropped(git, personal, llm):
    approved(personal, 5, prefix="Context")
    ids = week(git, 3)
    llm.overrides["memory.reflection"] = reply(
        candidate(ids[0], LINES[0], title="Kept"),
        candidate(ids[1], "keep the billing api on AWS, honestly", title="Paraphrase"),
        candidate(ids[1], "Context memory 0 content.", title="From context"),
        candidate(ids[2], LINES[2], target="self", title="Self"),
        candidate(ids[2], LINES[2], target="developer", title="Developer"),
    )
    result = reflection.reflect_workspace(personal)
    assert result["proposals_created"] == 1
    assert [m.title for m in learned_memories()] == ["Kept"]
    finished = AuditLog.objects.get(action=reflection.FINISHED)
    assert finished.metadata["drops"] == {"no_provenance": 2, "reflection_target": 2}


def test_a_reflection_fact_is_a_normal_pending_proposal(git, personal, llm):
    approved(personal, 5)
    [eid] = week(git, 1)
    llm.overrides["memory.reflection"] = reply(
        candidate(
            eid,
            LINES[0],
            target="fact",
            fact_obj={
                "subject": {"kind": "service", "name": "billing api"},
                "predicate": "deployed_to",
                "object": {"kind": "service", "name": "Hetzner"},
                "valid_from": timezone.localdate().isoformat(),
            },
        )
    )
    reflection.reflect_workspace(personal)
    row = MemoryFact.objects.get()
    assert row.status == "pending_approval" and row.sensitivity == "normal"
    assert row.provenance_quote == LINES[0]


def test_reflection_counts_toward_the_nightly_ten(git, personal, llm):
    approved(personal, 5)
    week(git)
    for i in range(8):
        content = f"Earlier tonight {i}."
        Memory.objects.create(
            workspace=personal,
            memory_type="semantic",
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
            title=f"Tonight {i}",
            content=content,
            fingerprint=compute_fingerprint(content),
            ingestion_source=git,
            provenance_quote="earlier tonight quote",
        )
    assert reflection.reflect_workspace(personal)["proposals_created"] == 2


def test_reflection_counts_toward_the_weekly_core_block_three(git, personal, llm):
    approved(personal, 5)
    ids = week(git, 1)
    for label in ("current_focus", "active_projects", "preferences"):
        create_core_block_proposal(
            workspace=personal,
            label=label,
            proposed_content=f"Earlier {label}",
            provenance_quote="an earlier owner quote",
            ingestion_source=git,
        )
    llm.overrides["memory.reflection"] = reply(candidate(ids[0], LINES[0], target="current_focus"))
    reflection.reflect_workspace(personal)
    assert CoreBlockProposal.objects.count() == 3
    finished = AuditLog.objects.get(action=reflection.FINISHED)
    assert finished.metadata["drops"] == {"weekly_cap": 1}


def test_no_private_or_personal_class_text_reaches_the_payload(git, sources, personal, llm):
    approved(personal, 5)
    approved(personal, 2, sensitivity="private", prefix="SECRET-MEMORY")
    subject = entity(personal, "billing api")
    secret_fact = fact(personal, subject, text="x", sensitivity="private")
    secret_fact.statement = "SECRET-FACT-STATEMENT"
    secret_fact.decided_at = recently()
    secret_fact.save()
    normal_fact = fact(personal, subject, text="hetzner")
    normal_fact.decided_at = recently()
    normal_fact.save()
    week(git, 2)
    episode(git, owner_text="SECRET-EPISODE owner text here", sensitivity="private")
    episode(
        sources[Kind.CALENDAR],
        owner_text="SECRET-CALENDAR therapy session title",
        kind="calendar.event",
        mapping_key="calendar:b1",
        occurred_at=recently(),
    )
    CoreMemoryBlock.objects.create(
        workspace=personal, label="current_focus", content="Shipping Phase 8B."
    )
    reflection.reflect_workspace(personal)
    [messages] = llm.of("memory.reflection")
    body = payload_text(messages)
    for marker in ("SECRET-MEMORY", "SECRET-FACT", "SECRET-EPISODE", "SECRET-CALENDAR"):
        assert marker not in body
    assert "Shipping Phase 8B." in body and normal_fact.statement in body
    assert body.count("<reflection_context>") == 1 and body.count("</reflection_context>") == 1
    # Sent without personal_data (the gateway marks only personal-data calls).
    report = ModelUsage.objects.get(purpose="memory.reflection").context_report
    assert "personal_data" not in report


def test_once_per_week_and_a_budget_refusal_is_never_retried(git, personal, llm):
    provider = fake_provider(priced=True, name="fake-priced")
    AIBudget.objects.create(
        name="learning_reflection_weekly", purposes=["memory.reflection"], daily_limit_usd=1
    )
    ModelUsage.objects.create(
        purpose="memory.reflection", provider=provider, success=True, estimated_cost=2
    )
    approved(personal, 5)
    week(git)
    assert reflection.reflect_workspace(personal)["outcome"] == "budget_exceeded"
    assert not llm.of("memory.reflection")
    assert (
        ModelUsage.objects.filter(purpose="memory.reflection", error_type="budget_exceeded").count()
        == 1
    )
    AIBudget.objects.all().delete()
    assert reflection.reflect_workspace(personal)["outcome"] == "already_ran"
    assert not llm.of("memory.reflection") and not learned_memories().exists()


def test_the_task_runs_every_eligible_workspace_once(git, personal, firm, llm):
    approved(personal, 5)
    approved(firm, 1)
    week(git)
    results = run_weekly_reflection_task()
    outcomes = {r["workspace"]: r["outcome"] for r in results}
    assert outcomes == {str(personal.id): "ok", str(firm.id): "too_few_items"}
    assert run_weekly_reflection_task()[0]["outcome"] == "already_ran"
