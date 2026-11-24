"""Phase 12 D16/D18 (branch 6): the `skills.curate` purpose is seeded in
all four places -- tier, prompt (seed + data migration + prompt pack, kept
equal), the `skills` and `skills_curate_weekly` budgets, and the
compression bypass."""

import re
from decimal import Decimal
from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.core.management import call_command

from ai_core import compression
from ai_core.management.commands import seed_ai_defaults
from ai_core.models import AIBudget, PromptTemplate

PURPOSE = "skills.curate"
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = import_module("ai_core.migrations.0011_skills_curate_prompt_and_budget")


def test_the_purpose_has_a_tier_and_bypasses_compression():
    assert settings.AI_PURPOSE_TIERS[PURPOSE] == "mid"
    assert PURPOSE in compression.COMPRESSION_BYPASS
    assert PURPOSE not in compression.COMPRESSION_ELIGIBLE
    assert PURPOSE in settings.AI_SKILLS_PURPOSES


def test_seed_migration_and_prompt_pack_carry_the_same_text():
    doc = (REPO_ROOT / "docs" / "prompts" / "phase-12-reach" / "skills-curate.md").read_text(
        "utf-8"
    )
    blocks = re.findall(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert seed_ai_defaults.FRIDAY_SKILLS_CURATE_V1 == MIGRATION.PROMPT_V1
    assert seed_ai_defaults.FRIDAY_SKILLS_CURATE_V1 in blocks
    assert MIGRATION.PROMPT_NAME == "friday_skills_curate"


@pytest.mark.django_db
def test_the_migration_creates_prompt_and_budgets_once_and_never_overwrites():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name__in=["skills", "skills_curate_weekly"]).delete()
    # An upgraded installation: the branch-5 row exists with only the matcher.
    AIBudget.objects.create(
        name="skills", purposes=["skills.match"], monthly_limit_usd=Decimal("7")
    )
    MIGRATION.create_prompt_and_budget(django_apps, None)
    template = PromptTemplate.objects.get(name=MIGRATION.PROMPT_NAME)
    assert template.purpose == PURPOSE
    assert template.active_version.content == MIGRATION.PROMPT_V1
    shared = AIBudget.objects.get(name="skills")
    assert shared.purposes == ["skills.match", PURPOSE]
    assert shared.monthly_limit_usd == Decimal("7")
    weekly = AIBudget.objects.get(name="skills_curate_weekly")
    assert weekly.purposes == [PURPOSE]
    assert weekly.daily_limit_usd == Decimal("0.5")
    assert weekly.monthly_limit_usd is None

    AIBudget.objects.filter(pk=weekly.pk).update(daily_limit_usd=Decimal("2"))
    MIGRATION.create_prompt_and_budget(django_apps, None)
    assert AIBudget.objects.get(name="skills_curate_weekly").daily_limit_usd == Decimal("2")
    assert AIBudget.objects.get(name="skills").purposes == ["skills.match", PURPOSE]
    assert PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).count() == 1


@pytest.mark.django_db
def test_the_migration_on_a_fresh_installation_covers_both_purposes():
    AIBudget.objects.filter(name__in=["skills", "skills_curate_weekly"]).delete()
    MIGRATION.create_prompt_and_budget(django_apps, None)
    assert set(AIBudget.objects.get(name="skills").purposes) == {"skills.match", PURPOSE}


@pytest.mark.django_db
def test_seed_ai_defaults_seeds_the_prompt_and_both_budgets():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name__in=["skills", "skills_curate_weekly"]).delete()
    AIBudget.objects.create(name="skills", purposes=["skills.match"])
    call_command("seed_ai_defaults", stdout=StringIO())
    template = PromptTemplate.objects.get(name="friday_skills_curate")
    assert template.active_version.content == seed_ai_defaults.FRIDAY_SKILLS_CURATE_V1
    assert AIBudget.objects.get(name="skills").purposes == ["skills.match", PURPOSE]
    weekly = AIBudget.objects.get(name="skills_curate_weekly")
    assert weekly.purposes == [PURPOSE] and weekly.daily_limit_usd == Decimal("0.5")
