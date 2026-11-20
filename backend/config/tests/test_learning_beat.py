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
    assert set(learning.purposes) == {"memory.episode_extraction", "memory.consolidation"}
    assert learning.monthly_limit_usd == Decimal("20")
    assert AIBudget.objects.get(name="learning_extraction_daily").daily_limit_usd == Decimal("1")
    AIBudget.objects.filter(name="learning").update(monthly_limit_usd=Decimal("5"))
    call_command("seed_ai_defaults")  # never overwrites the owner's edit
    assert AIBudget.objects.get(name="learning").monthly_limit_usd == Decimal("5")
