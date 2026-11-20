"""Data migration 0006 (Jarvis -> Friday, 2026-11-20): pre-rename prompt
template rows are renamed, and an active version that names the assistant
"Jarvis" is superseded -- never edited in place."""

from importlib import import_module

import pytest
from django.apps import apps
from django.core.management import call_command

from ai_core.models import PromptTemplate, PromptVersion

pytestmark = pytest.mark.django_db

migration = import_module("ai_core.migrations.0006_rename_jarvis_prompt_templates")


def _legacy_template(name, content):
    template = PromptTemplate.objects.create(name=name, purpose="chat")
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])
    return template, version


def test_renames_templates_and_supersedes_the_jarvis_persona():
    template, v1 = _legacy_template("jarvis_system", "You are Jarvis OS, a concise assistant.")
    plain, plain_v1 = _legacy_template("jarvis_router_intent", "Classify the intent.")

    migration.rename_templates(apps, None)

    template.refresh_from_db()
    assert template.name == "friday_system"
    assert template.active_version.version == 2
    assert template.active_version.content == "You are Friday OS, a concise assistant."
    v1.refresh_from_db()
    assert v1.content == "You are Jarvis OS, a concise assistant."  # history untouched

    plain.refresh_from_db()
    assert plain.name == "friday_router_intent"
    assert plain.active_version == plain_v1  # nothing to rename in the text


def test_is_idempotent_and_seed_does_not_duplicate():
    _legacy_template("jarvis_system", "You are Jarvis OS.")
    migration.rename_templates(apps, None)
    migration.rename_templates(apps, None)
    call_command("seed_ai_defaults")
    call_command("seed_ai_defaults")

    assert not PromptTemplate.objects.filter(name__startswith="jarvis_").exists()
    template = PromptTemplate.objects.get(name="friday_system")
    assert template.versions.count() == 2
    assert "Jarvis" not in template.active_version.content


def test_skips_a_name_that_already_exists():
    _legacy_template("jarvis_system", "You are Jarvis OS.")
    existing, _ = _legacy_template("friday_system", "You are Friday OS.")

    migration.rename_templates(apps, None)

    assert PromptTemplate.objects.filter(name="jarvis_system").exists()
    existing.refresh_from_db()
    assert existing.active_version.content == "You are Friday OS."


def test_reverse_restores_the_names():
    _legacy_template("jarvis_agent_plan", "Plan.")
    migration.rename_templates(apps, None)
    migration.restore_names(apps, None)
    assert PromptTemplate.objects.filter(name="jarvis_agent_plan").exists()
