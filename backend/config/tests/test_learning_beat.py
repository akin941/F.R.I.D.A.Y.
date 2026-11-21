"""Phase 8A branch 5 (brief D9/D10/D13): the nightly learning run and the
extraction recovery scan are on the compose beat; the learning purposes are
tiered; the learning budgets are seeded."""

from decimal import Decimal

import pytest
from django.core.management import call_command

from ai_core.models import AIBudget
from config.celery import app, build_beat_schedule

LEARNING_TASKS = {
    "memory.ingestion.tasks.run_learning_night_task",
    "memory.ingestion.tasks.recover_stale_episode_extraction_jobs",
}


def test_learning_tasks_run_on_the_default_beat_and_are_registered():
    default = {e["task"] for e in build_beat_schedule(runner_mode=False).values()}
    runner = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert LEARNING_TASKS <= default
    assert not LEARNING_TASKS & runner
    app.loader.import_default_modules()
    assert LEARNING_TASKS <= set(app.tasks)


def test_learning_purposes_are_tiered(settings):
    assert settings.AI_PURPOSE_TIERS["memory.episode_extraction"] == "cheap"
    assert settings.AI_PURPOSE_TIERS["memory.consolidation"] == "mid"


@pytest.mark.django_db
def test_seed_creates_the_learning_budgets_once(settings):
    call_command("seed_ai_defaults")
    learning = AIBudget.objects.get(name="learning")
    assert set(learning.purposes) == {
        "memory.episode_extraction",
        "memory.consolidation",
        "memory.fact_reconciliation",
        "memory.reflection",
    }
    assert learning.monthly_limit_usd == Decimal("20")
    assert AIBudget.objects.get(name="learning_extraction_daily").daily_limit_usd == Decimal("1")
    AIBudget.objects.filter(name="learning").update(monthly_limit_usd=Decimal("5"))
    call_command("seed_ai_defaults")  # never overwrites the owner's edit
    assert AIBudget.objects.get(name="learning").monthly_limit_usd == Decimal("5")


# --- Phase 8B branch 2 (brief D5, D15) ---------------------------------------


def test_the_weekly_reflection_runs_sunday_0330_on_the_default_beat():
    default = build_beat_schedule(runner_mode=False)
    entry = default["memory-learning-weekly-reflection"]
    assert entry["task"] == "memory.ingestion.tasks.run_weekly_reflection_task"
    schedule = entry["schedule"]
    assert schedule.day_of_week == {0} and schedule.hour == {3} and schedule.minute == {30}
    runner = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert entry["task"] not in runner
    app.loader.import_default_modules()
    assert entry["task"] in app.tasks
    assert str(app.conf.timezone) == "Europe/Istanbul"


def test_the_fact_learning_purposes_are_mid_tier_and_learning_budgeted(settings):
    for purpose in ("memory.fact_reconciliation", "memory.reflection"):
        assert settings.AI_PURPOSE_TIERS[purpose] == "mid"
        assert purpose in settings.AI_LEARNING_PURPOSES


@pytest.mark.django_db
def test_seed_creates_the_fact_learning_budgets_and_prompts(settings):
    from ai_core.models import PromptTemplate

    call_command("seed_ai_defaults")
    reconcile = AIBudget.objects.get(name="learning_reconciliation_daily")
    assert reconcile.purposes == ["memory.fact_reconciliation"]
    assert reconcile.daily_limit_usd == Decimal("0.5")
    reflect = AIBudget.objects.get(name="learning_reflection_weekly")
    assert reflect.purposes == ["memory.reflection"] and reflect.daily_limit_usd == Decimal("1")
    episode = PromptTemplate.objects.get(name="friday_episode_extraction")
    assert episode.active_version.version == 2
    assert '"version": 2' in episode.active_version.content
    for name in ("friday_fact_reconciliation", "friday_memory_reflection"):
        assert PromptTemplate.objects.get(name=name).active_version.version == 1


@pytest.mark.django_db
def test_an_upgraded_learning_budget_gains_the_new_purposes_only(settings):
    AIBudget.objects.create(
        name="learning",
        purposes=["memory.episode_extraction", "memory.consolidation"],
        monthly_limit_usd=Decimal("7"),
    )
    call_command("seed_ai_defaults")
    learning = AIBudget.objects.get(name="learning")
    assert learning.purposes == [
        "memory.episode_extraction",
        "memory.consolidation",
        "memory.fact_reconciliation",
        "memory.reflection",
    ]
    assert learning.monthly_limit_usd == Decimal("7")


@pytest.mark.django_db
def test_an_owner_episode_prompt_version_is_kept(settings):
    from ai_core.models import PromptTemplate, PromptVersion

    call_command("seed_ai_defaults")
    template = PromptTemplate.objects.get(name="friday_episode_extraction")
    own = PromptVersion.objects.create(template=template, version=3, content="Owner's own.")
    template.active_version = own
    template.save()
    call_command("seed_ai_defaults")
    template.refresh_from_db()
    assert template.active_version_id == own.id
