"""Shared fixtures for the Phase 12 branch 6 curator tests: the seeded
prompt and budgets, a fake personal-data provider, and a scripted
`skills.curate` answer that also records the messages it was sent."""

import json
from importlib import import_module

import pytest
from django.apps import apps as django_apps

from ai_core.adapters import fake as fake_adapter
from ai_core.models import ModelProvider
from skills.curator import CURATE_PURPOSE
from skills.tests.helpers import make_owner, personal_of

MIGRATION = import_module("ai_core.migrations.0012_skills_curate_prompt_and_budget")


@pytest.fixture
def seeded(db):
    """What the ai_core 0012 data migration creates (a transactional test
    elsewhere may have flushed it from a reused test database)."""
    MIGRATION.create_prompt_and_budget(django_apps, None)


@pytest.fixture
def owner(db):
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


@pytest.fixture
def provider(settings, db):
    settings.AI_FAKE_PROVIDER = True
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


class Script:
    def __init__(self):
        self.text = fake_adapter.FAKE_SKILLS_CURATE_RESPONSE
        self.messages = []
        self.side_effect = None

    def answer(self, proposals):
        self.text = json.dumps({"proposals": proposals}, ensure_ascii=False)


@pytest.fixture
def curate_answer(monkeypatch):
    """`curate_answer.answer([...])` scripts the fake curator's JSON;
    `.text` sets raw text; `.side_effect` runs inside the model call (inside
    the unattended run); `.messages` holds what the model was sent."""
    script = Script()
    original = fake_adapter.FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        if purpose == CURATE_PURPOSE:
            script.messages.append(messages)
            if script.side_effect is not None:
                script.side_effect()
            return script.text
        return original(self, purpose, messages)

    monkeypatch.setattr(fake_adapter.FakeAdapter, "_text_for", text_for)
    return script


def skill_md(name, body="Önce özet yaz. Sonra değişiklikleri listele.", extra=""):
    return f'---\nname: {name}\ndescription: "{name} için kullan."\n{extra}---\n{body}\n'
