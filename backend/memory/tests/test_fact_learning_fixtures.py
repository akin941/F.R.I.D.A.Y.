"""Drift guard (Phase 8B branch 2): the FakeAdapter's hand-written learning
fixtures (ai_core may not import memory) must parse with the real schema-v2
episode parser and the reconciliation parser."""

from ai_core.adapters.fake import (
    FAKE_RECONCILIATION_RESPONSE,
    fake_episode_extraction_response,
    fake_reflection_response,
)
from memory.ingestion.extraction import parse_episode_candidates
from memory.ingestion.reconciliation import parse_reconciliation
from memory.ingestion.reflection import MAX_REFLECTION_PROPOSALS


def _messages(count):
    body = "\n".join(
        f'<owner_item episode_id="e{i}" source="git" kind="git.commit" occurred_at="x">\n'
        f"Owner line number {i} with enough characters\n</owner_item>"
        for i in range(count)
    )
    return [{"role": "system", "content": "p"}, {"role": "user", "content": body}]


def test_fake_episode_extraction_is_schema_v2():
    parsed = parse_episode_candidates(fake_episode_extraction_response(_messages(3)))
    assert len(parsed) == 3 and all(c["fact"] is None for c in parsed)


def test_fake_reflection_respects_the_cap():
    raw = fake_reflection_response(_messages(8))
    parsed = parse_episode_candidates(raw, max_candidates=MAX_REFLECTION_PROPOSALS)
    assert len(parsed) == MAX_REFLECTION_PROPOSALS


def test_fake_reconciliation_parses():
    assert parse_reconciliation(FAKE_RECONCILIATION_RESPONSE, 3) == {}
