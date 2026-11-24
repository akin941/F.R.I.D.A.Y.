"""Phase 12 D11/D18 (branch 4): the `chat.followup` purpose is seeded in the
places the brief names -- tier, prompt (seed + data migration + prompt
pack, kept equal) and the compression bypass. No budget row of its own."""

import re
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

PURPOSE = "chat.followup"
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = import_module("ai_core.migrations.0011_chat_followup_prompt")


def test_the_purpose_has_a_tier_and_bypasses_compression():
    assert settings.AI_PURPOSE_TIERS[PURPOSE] == "mid"
    assert PURPOSE in compression.COMPRESSION_BYPASS
    assert PURPOSE not in compression.COMPRESSION_ELIGIBLE


def test_seed_migration_and_prompt_pack_carry_the_same_text():
    doc = (REPO_ROOT / "docs" / "prompts" / "phase-12-reach" / "followup.md").read_text("utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert seed_ai_defaults.FRIDAY_CHAT_FOLLOWUP_V1 == MIGRATION.PROMPT_V1
    assert seed_ai_defaults.FRIDAY_CHAT_FOLLOWUP_V1 in blocks
    assert MIGRATION.PROMPT_NAME == "friday_chat_followup"


@pytest.mark.django_db
def test_the_migration_creates_the_prompt_once_and_never_overwrites():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    MIGRATION.create_prompt(django_apps, None)
    template = PromptTemplate.objects.get(name=MIGRATION.PROMPT_NAME)
    assert template.purpose == PURPOSE
    assert template.active_version.content == MIGRATION.PROMPT_V1
    version = template.active_version
    version.content = "owner edit"
    version.save(update_fields=["content"])
    MIGRATION.create_prompt(django_apps, None)
    template.refresh_from_db()
    assert template.active_version.content == "owner edit"
    assert PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).count() == 1


@pytest.mark.django_db
def test_seed_ai_defaults_seeds_the_prompt_and_no_budget():
    PromptTemplate.objects.filter(name=MIGRATION.PROMPT_NAME).delete()
    call_command("seed_ai_defaults", stdout=StringIO())
    template = PromptTemplate.objects.get(name="friday_chat_followup")
    assert template.active_version.content == seed_ai_defaults.FRIDAY_CHAT_FOLLOWUP_V1
    assert not [b for b in AIBudget.objects.all() if PURPOSE in (b.purposes or [])]
