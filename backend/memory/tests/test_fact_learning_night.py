"""Phase 8B branch 2 "done when": a fixture night of owner commits and
hook-free episodes yields at most 10 proposals including verified fact
proposals; an approved supersede closes the old fact's window; a fixture
Sunday yields at most 5 quoted reflection proposals. Eager Celery, the
nightly task with on-commit dispatch executed, FakeAdapter only."""

import json
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from memory.ingestion.tasks import run_learning_night_task, run_weekly_reflection_task
from memory.models import Episode, IngestionSource, Memory, MemoryFact, compute_fingerprint
from memory.provenance import normalise
from memory.tests.graph_helpers import entity, fact
from memory.tests.learning_helpers import (
    capture_llm,
    enable,
    episode,
    fake_provider,
    map_repo,
    payload_text,
    seed_prompts,
)

pytestmark = pytest.mark.django_db

Kind = IngestionSource.Kind
BASE = "/api/v1/memory/learning"
OWNER_ITEM = re.compile(r'<owner_item episode_id="([^"]+)"[^>]*>\n(.*?)\n</owner_item>', re.DOTALL)


@pytest.fixture(autouse=True)
def ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    seed_prompts()
    fake_provider(personal_data_allowed=True)


@pytest.fixture
def llm(monkeypatch):
    return capture_llm(monkeypatch)


def facts_for_service_lines(messages):
    """A model that turns each "<svc> runs on <host>" owner line into a fact
    and every other line into a memory."""
    candidates = []
    for episode_id, text in OWNER_ITEM.findall(payload_text(messages)):
        text = text.strip()
        match = re.search(r"(\w+) runs on (\w+)", text)
        candidates.append(
            {
                "type": "semantic",
                "title": "Learned",
                "content": f"Learned: {text[:60]}",
                "quote": text,
                "episode_id": episode_id,
                "target": "fact" if match else "memory",
                "fact": (
                    {
                        "subject": {"kind": "service", "name": match.group(1)},
                        "predicate": "deployed_to",
                        "object": {"kind": "service", "name": match.group(2)},
                        "valid_from": timezone.localdate().isoformat(),
                    }
                    if match
                    else None
                ),
            }
        )
    return json.dumps({"version": 2, "candidates": candidates})


def test_a_fixture_night_yields_verified_fact_proposals_and_an_approved_supersede(
    sources, owner, personal, llm, web_client, django_capture_on_commit_callbacks
):
    enable(sources, Kind.GIT, Kind.CLAUDE_CODE_HOOKS)
    map_repo(sources, "r1", personal)
    git = sources[Kind.GIT]
    billing = entity(personal, "billing")
    old = fact(
        personal,
        billing,
        obj=entity(personal, "aws"),
        valid_from=timezone.now() - timedelta(days=9),
    )
    for i in range(3):
        episode(git, owner_text=f"Commit {i}: tighten the scrubber entropy rule again")
    episode(git, owner_text="Commit: billing runs on hetzner from today on")
    for i in range(10):
        episode(git, owner_text=f"Deploy note {i}: worker{i} runs on box{i} in prod")

    def reconcile(messages):
        body = payload_text(messages)
        index = re.search(r'candidate_index="(\d+)"', body).group(1)
        return json.dumps(
            {
                "version": 1,
                "decisions": [
                    {
                        "candidate_index": int(index),
                        "action": "supersede",
                        "supersedes_fact_id": str(old.id),
                    }
                ],
            }
        )

    llm.overrides["memory.episode_extraction"] = facts_for_service_lines
    llm.overrides["memory.fact_reconciliation"] = reconcile
    with django_capture_on_commit_callbacks(execute=True):
        run_learning_night_task()

    memories = list(Memory.objects.filter(ingestion_source__isnull=False))
    facts = list(MemoryFact.objects.filter(status="pending_approval"))
    assert len(memories) + len(facts) <= 10
    assert len(memories) == 3 and len(facts) == 6  # the fact cap
    episodes = {str(e.id): e for e in Episode.all_objects.all()}
    for row in facts:
        source_episode = episodes[str(row.source_episode_id)]
        assert normalise(row.provenance_quote) in normalise(source_episode.owner_text)
        assert row.subject.normalised_name in row.provenance_quote.lower()
    [proposal] = [f for f in facts if f.supersedes_id]
    assert proposal.supersedes_id == old.id and proposal.subject_id == billing.id

    response = web_client(owner).post(f"{BASE}/proposals/fact/{proposal.id}/approve", {})
    assert response.status_code == 200
    old.refresh_from_db()
    proposal.refresh_from_db()
    assert old.valid_to == proposal.valid_from and old.superseded_by_id == proposal.id
    assert MemoryFact.objects.filter(subject=billing).current().get() == proposal


def test_a_fixture_sunday_yields_at_most_five_quoted_reflection_proposals(
    sources, personal, llm, django_capture_on_commit_callbacks
):
    enable(sources, Kind.GIT)
    map_repo(sources, "r1", personal)
    git = sources[Kind.GIT]
    for i in range(5):
        content = f"Approved {i}."

        Memory.objects.create(
            workspace=personal,
            memory_type="semantic",
            status=Memory.Status.APPROVED,
            source=Memory.Source.AI_SUGGESTED,
            title=f"Approved {i}",
            content=content,
            fingerprint=compute_fingerprint(content),
        )
    lines = [f"Weekly line {i}: keep shipping the graph branch first" for i in range(9)]
    for line in lines:
        episode(git, owner_text=line, occurred_at=timezone.now() - timedelta(seconds=30))
    [result] = run_weekly_reflection_task()
    assert result["outcome"] == "ok"
    rows = list(Memory.objects.filter(ingestion_source__isnull=False))
    assert 0 < len(rows) <= 5
    assert all(r.provenance_quote in lines for r in rows)
