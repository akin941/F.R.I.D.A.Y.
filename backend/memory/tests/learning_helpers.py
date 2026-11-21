"""Helpers for the Phase 8A branch 5 learning-loop tests: a capturing,
overridable FakeAdapter, prompt seeding and episode factories."""

import json
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from memory.ingestion.ports import record_episode
from memory.models import IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule

Rule = WorkspaceMappingRule.RuleType

PROMPTS = {
    "friday_episode_extraction": "memory.episode_extraction",
    "friday_memory_consolidation": "memory.consolidation",
    "friday_memory_extraction": "memory.extraction",
    "friday_fact_reconciliation": "memory.fact_reconciliation",
    "friday_memory_reflection": "memory.reflection",
}


def seed_prompts():
    for name, purpose in PROMPTS.items():
        template, _ = PromptTemplate.objects.get_or_create(name=name, defaults={"purpose": purpose})
        version, _ = PromptVersion.objects.get_or_create(
            template=template, version=1, defaults={"content": f"{name} prompt."}
        )
        template.active_version = version
        template.save(update_fields=["active_version"])


def fake_provider(*, personal_data_allowed=True, name="fake-local", priced=False):
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=personal_data_allowed,
        input_price_per_mtok=1 if priced else 0,
    )


def capture_llm(monkeypatch):
    """Record every `(purpose, messages)` the FakeAdapter is asked for; an
    entry in `overrides[purpose]` (a string or `messages -> str`) replaces
    the purpose's fixture."""
    calls = []
    overrides = {}
    original = FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        calls.append((purpose, messages))
        if purpose in overrides:
            override = overrides[purpose]
            return override(messages) if callable(override) else override
        return original(self, purpose, messages)

    monkeypatch.setattr(FakeAdapter, "_text_for", text_for)
    return SimpleNamespace(
        calls=calls,
        overrides=overrides,
        of=lambda purpose: [m for p, m in calls if p == purpose],
    )


def payload_text(messages):
    return "\n".join(str(m.get("content", "")) for m in messages)


def enable(sources, *kinds):
    for kind in kinds:
        source = sources[kind]
        source.enabled = True
        source.save()


def map_repo(sources, repo_id, workspace, kinds=(IngestionSource.Kind.GIT,)):
    for kind in kinds:
        add_rule(sources[kind], Rule.REPO, repo_id, workspace)


_counter = {"n": 0}


def episode(
    source,
    *,
    owner_text="",
    context_text="",
    repo="r1",
    kind="git.commit",
    sensitivity="normal",
    metadata=None,
    mapping_key=None,
    occurred_at=None,
):
    _counter["n"] += 1
    result = record_episode(
        source=source,
        kind=kind,
        external_id=f"ext-{_counter['n']}",
        occurred_at=occurred_at or timezone.now() - timedelta(hours=1),
        mapping_key=mapping_key or f"repo:{repo}",
        owner_text=owner_text,
        context_text=context_text,
        metadata=metadata or {},
        sensitivity=sensitivity,
    )
    assert result.stored, result
    return result.episode_id


def candidates_reply(items):
    """A schema-v2 episode-extraction reply from `(episode_id, quote, extra)`
    tuples; `extra` overrides candidate keys."""
    out = []
    for index, (episode_id, quote, extra) in enumerate(items):
        candidate = {
            "type": "semantic",
            "title": f"Learned {index}",
            "content": f"Learned statement number {index}.",
            "quote": quote,
            "episode_id": str(episode_id),
            "target": "memory",
            "fact": None,
        }
        candidate.update(extra or {})
        out.append(candidate)
    return json.dumps({"version": 2, "candidates": out})
