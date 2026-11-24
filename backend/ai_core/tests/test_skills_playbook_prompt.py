"""Phase 12 D17/D18 (branch 7): the `skills.playbook_distill` purpose is
seeded in all four places -- tier, prompt (seed + data migration + prompt
pack, kept equal), the budgets (the shared `skills` cap gains the purpose;
`skills_playbook_daily`, 0.5 USD/day) and the compression bypass."""

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

PURPOSE = "skills.playbook_distill"
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = import_module("ai_core.migrations.0012_skills_playbook_distill_prompt_and_budget")


def test_the_purpose_has_a_tier_and_bypasses_compression():
    assert settings.AI_PURPOSE_TIERS[PURPOSE] == "mid"
    assert PURPOSE in compression.COMPRESSION_BYPASS
    assert PURPOSE in settings.AI_SKILLS_PURPOSES


def test_seed_migration_and_prompt_pack_carry_the_same_text():
    doc = (REPO_ROOT / "docs" / "prompts" / "phase-12-reach" / "playbook-distill.md").read_text(
        "utf-8"
    )
    blocks = re.findall(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert seed_ai_defaults.FRIDAY_SKILLS_PLAYBOOK_DISTILL_V1 == MIGRATION.PROMPT_V1
    assert seed_ai_defaults.FRIDAY_SKILLS_PLAYBOOK_DISTILL_V1 in blocks
    assert MIGRATION.PROMPT_NAME == "friday_skills_playbook_distill"


@pytest.mark.django_db
def test_the_migration_adds_the_purpose_and_never_overwrites_limits():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name__in=["skills", "skills_playbook_daily"]).delete()
    AIBudget.objects.create(
        name="skills", purposes=["skills.match"], monthly_limit_usd=Decimal("9")
    )
    MIGRATION.create_prompt_and_budgets(django_apps, None)
    template = PromptTemplate.objects.get(name=MIGRATION.PROMPT_NAME)
    assert template.purpose == PURPOSE
    assert template.active_version.content == MIGRATION.PROMPT_V1
    shared = AIBudget.objects.get(name="skills")
    assert shared.purposes == ["skills.match", PURPOSE]
    assert shared.monthly_limit_usd == Decimal("9")
    daily = AIBudget.objects.get(name="skills_playbook_daily")
    assert daily.purposes == [PURPOSE] and daily.daily_limit_usd == Decimal("0.5")

    AIBudget.objects.filter(pk=daily.pk).update(daily_limit_usd=Decimal("2"))
    MIGRATION.create_prompt_and_budgets(django_apps, None)
    assert AIBudget.objects.get(name="skills_playbook_daily").daily_limit_usd == Decimal("2")
    assert AIBudget.objects.get(name="skills").purposes == ["skills.match", PURPOSE]


@pytest.mark.django_db
def test_seed_ai_defaults_seeds_the_prompt_and_budgets():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name__in=["skills", "skills_playbook_daily"]).delete()
    AIBudget.objects.create(name="skills", purposes=["skills.match"])
    call_command("seed_ai_defaults", stdout=StringIO())
    template = PromptTemplate.objects.get(name=MIGRATION.PROMPT_NAME)
    assert template.active_version.content == seed_ai_defaults.FRIDAY_SKILLS_PLAYBOOK_DISTILL_V1
    assert PURPOSE in AIBudget.objects.get(name="skills").purposes
    assert AIBudget.objects.get(name="skills_playbook_daily").purposes == [PURPOSE]
