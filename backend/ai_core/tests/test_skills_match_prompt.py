"""Phase 12 D15/D18 (branch 5): the `skills.match` purpose is seeded in
all four places -- tier, prompt (seed + data migration + prompt pack, kept
equal), the `skills` budget, and the compression bypass."""

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

PURPOSE = "skills.match"
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = import_module("ai_core.migrations.0010_skills_match_prompt_and_budget")


def test_the_purpose_has_a_tier_and_bypasses_compression():
    assert settings.AI_PURPOSE_TIERS[PURPOSE] == "cheap"
    assert PURPOSE in compression.COMPRESSION_BYPASS
    assert PURPOSE in settings.AI_SKILLS_PURPOSES


def test_seed_migration_and_prompt_pack_carry_the_same_text():
    doc = (REPO_ROOT / "docs" / "prompts" / "phase-12-reach" / "skills-match.md").read_text("utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert seed_ai_defaults.FRIDAY_SKILLS_MATCH_V1 == MIGRATION.PROMPT_V1
    assert seed_ai_defaults.FRIDAY_SKILLS_MATCH_V1 in blocks
    assert MIGRATION.PROMPT_NAME == "friday_skills_match"


@pytest.mark.django_db
def test_the_migration_creates_prompt_and_budget_once_and_never_overwrites():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name="skills").delete()
    MIGRATION.create_prompt_and_budget(django_apps, None)
    template = PromptTemplate.objects.get(name=MIGRATION.PROMPT_NAME)
    assert template.purpose == PURPOSE
    assert template.active_version.content == MIGRATION.PROMPT_V1
    budget = AIBudget.objects.get(name="skills")
    assert budget.purposes == [PURPOSE]
    assert budget.monthly_limit_usd == Decimal("5")

    AIBudget.objects.filter(pk=budget.pk).update(monthly_limit_usd=Decimal("9"))
    MIGRATION.create_prompt_and_budget(django_apps, None)
    assert AIBudget.objects.get(name="skills").monthly_limit_usd == Decimal("9")
    assert PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).count() == 1


@pytest.mark.django_db
def test_seed_ai_defaults_seeds_the_prompt_and_the_budget():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    AIBudget.objects.filter(name="skills").delete()
    call_command("seed_ai_defaults", stdout=StringIO())
    template = PromptTemplate.objects.get(name="friday_skills_match")
    assert template.active_version.content == seed_ai_defaults.FRIDAY_SKILLS_MATCH_V1
    budget = AIBudget.objects.get(name="skills")
    # Branch 6 adds the curator's purpose to the same row.
    assert budget.purposes[0] == PURPOSE and "skills.curate" in budget.purposes
