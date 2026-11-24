"""Shared fixtures for the Phase 12 branch 7 playbook tests: sources recorded
through the port, the distill prompt, a fake provider and a helper that sets
the fake distiller's answer."""

import json
import uuid

import pytest

from ai_core.adapters import fake
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from skills import ports
from skills.playbooks import PROMPT_NAME, PURPOSE
from skills.tests.helpers import make_owner, personal_of

GOALS = (
    "acme-api: migration ekle ve testleri make test ile çalıştır",
    "acme-api: yeni bir migration ekle, make test ile doğrula",
    "acme-api: alan için migration ekle; make test geçmeli",
)


def record_source(workspace, *, repo_key="repo-1", goal=None, job_id=None, **overrides):
    fields = {
        "workspace": workspace,
        "job_id": job_id or uuid.uuid4(),
        "repo_key": repo_key,
        "repo_label": "acme-api",
        "goal": goal or GOALS[0],
        "test_command": "make test",
        "changed_paths": ["backend/app/migrations/0002_x.py", "backend/app/models.py"],
        "test_passed": True,
        "exit_code": 0,
        "context_text": "Change summary: added a migration. Review summary: looks fine.",
        "sensitive": False,
    }
    fields.update(overrides)
    return ports.record_playbook_source(**fields)


def record_sources(workspace, count=3, *, repo_key="repo-1"):
    from skills.models import PlaybookSource

    ids = [
        record_source(workspace, repo_key=repo_key, goal=GOALS[index % len(GOALS)])
        for index in range(count)
    ]
    return list(PlaybookSource.objects.filter(pk__in=ids).order_by("created_at"))


def step(source, text, quote, paths=None):
    row = {"text": text, "source": str(source.job_id), "quote": quote}
    if paths is not None:
        row["paths"] = paths
    return row


def draft(steps, *, name="acme-api-migration", description="acme-api'ye migration eklerken."):
    return json.dumps({"name": name, "description": description, "steps": steps})


def good_draft(sources):
    return draft(
        [
            step(
                sources[0],
                "Migration ekle.",
                "migration ekle",
                ["backend/app/migrations/0002_x.py"],
            ),
            step(sources[1], "Testleri make test ile çalıştır.", "make test"),
        ]
    )


@pytest.fixture
def owner(db):
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


@pytest.fixture
def distill_prompt(db):
    template, _ = PromptTemplate.objects.get_or_create(
        name=PROMPT_NAME, defaults={"purpose": PURPOSE}
    )
    if template.active_version_id is None:
        version = PromptVersion.objects.create(
            template=template, version=1, content="Draft a playbook as JSON."
        )
        template.active_version = version
        template.save(update_fields=["active_version"])
    return template.active_version


@pytest.fixture
def fake_provider(db, settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


@pytest.fixture
def distiller_answer(monkeypatch):
    def set_answer(text):
        monkeypatch.setattr(fake, "FAKE_SKILLS_PLAYBOOK_DISTILL_RESPONSE", text)

    return set_answer
