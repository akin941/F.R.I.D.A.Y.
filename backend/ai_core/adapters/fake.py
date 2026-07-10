"""Deterministic adapter for tests and local development. No network access."""

import hashlib
import math
import random
import re

from ai_core.adapters.base import AdapterResult, BaseAdapter, EmbedAdapterResult

FAKE_RESPONSE = "Merhaba, ben Jarvis. MVP 1 gateway üzerinden çalışıyorum."

# Fixed, small dimension for the fake embedding provider (MVP 3 §1) — chosen
# purely for fast test fixtures. It does not need to match any real
# provider's dimension: retrieval always filters chunks to one
# `embedding_provider` before comparing vectors, so each provider's vectors
# only ever compare against their own kind.
FAKE_EMBEDDING_DIMENSION = 32

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text):
    return _TOKEN_RE.findall(text.lower())


def _token_vector(token, dimension):
    # sha256, not the built-in hash(): Python's hash() is salted per-process
    # by default and unstable across runs/processes, which would make this
    # "deterministic" embedding not actually deterministic.
    seed = int.from_bytes(hashlib.sha256(token.encode()).digest(), "big")
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dimension)]


def embed_text(text, dimension=FAKE_EMBEDDING_DIMENSION):
    """Deterministic bag-of-words embedding: sum each distinct token's
    SHA256-seeded unit vector, then L2-normalize. Texts sharing more
    vocabulary land closer together under cosine similarity by construction —
    a crude but genuine semantic-similarity signal, not an arbitrary hash."""
    tokens = _tokenize(text)
    vector = [0.0] * dimension
    for token in tokens:
        token_vector = _token_vector(token, dimension)
        for i in range(dimension):
            vector[i] += token_vector[i]
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


# Purpose-aware fixture for `memory.extraction` (MVP 2 §3): a hand-written
# literal, not imported from `memory` — ai_core must never import a business
# app (ADR-003 / import-linter contract), so this intentionally duplicates
# the shape of `memory.extraction.SCHEMA_VERSION`/candidate schema rather
# than referencing it. If that schema changes, this fixture must be updated
# by hand; a schema-parser test against this exact string guards the drift.
FAKE_MEMORY_EXTRACTION_RESPONSE = (
    '{"version": 1, "candidates": [{"type": "preference", '
    '"title": "Fake extracted preference", '
    '"content": "Deterministic FakeAdapter candidate for memory.extraction."}]}'
)

# Purpose-aware fixture for `router.intent` (MVP 6 §1): a hand-written
# literal mirroring assistant.intent's schema — ai_core must never import a
# business app (ADR-003 / import-linter contract), so this intentionally
# duplicates the shape rather than referencing it. Default classifies as
# `chat` with no tool_name; a schema-parser test against this exact string
# guards the drift. Per-intent unit-test behavior is exercised by feeding
# `parse_intent` its own literal fixtures directly (monkeypatching
# `parse_intent`, which never reads this string at all) — the CONTENT-based
# triggers below exist only so the Playwright e2e suite (which drives a real
# Docker backend process it cannot monkeypatch) can still exercise every
# intent, using the exact same trigger phrases the unit tests already send.
FAKE_ROUTER_INTENT_RESPONSE = '{"intent": "chat", "tool_name": null}'
FAKE_ROUTER_TASK_DRAFT_INTENT_RESPONSE = '{"intent": "task_draft", "tool_name": null}'
FAKE_ROUTER_TOOL_REQUEST_INTENT_RESPONSE = '{"intent": "tool_request", "tool_name": "vault_export"}'
FAKE_ROUTER_MEMORY_QUERY_INTENT_RESPONSE = '{"intent": "memory_query", "tool_name": null}'
FAKE_ROUTER_KNOWLEDGE_QUERY_INTENT_RESPONSE = '{"intent": "knowledge_query", "tool_name": null}'

# Substring triggers checked against the latest user message, lowercased.
# Deliberately specific (not "remind"/"export" alone) so no unrelated message
# in any existing test or real usage accidentally matches one.
_TASK_DRAFT_TRIGGER = "remind me"
_TOOL_REQUEST_TRIGGER = "export my vault"
_MEMORY_QUERY_TRIGGER = "what do i like"
_KNOWLEDGE_QUERY_TRIGGER = "summarize my knowledge base"

# Purpose-aware fixture for `router.task_draft` (MVP 6 §3) — mirrors
# assistant.task_draft's schema for the same ai_core-must-not-import-business-
# apps reason as the fixtures above. Always a fixed title with no due date;
# per-case behavior (title/due_date variations, malformed output) is
# exercised by feeding `parse_task_draft` its own literal fixtures directly.
FAKE_TASK_DRAFT_RESPONSE = '{"title": "Fake extracted task", "due_date": null}'

# Purpose-aware fixture for `agent.plan` (MVP 7 §2) -- mirrors
# agents.planner's schema for the same ai_core-must-not-import-business-
# apps reason as the fixtures above. A single `read`-kind step; per-case
# behavior (multi-step plans, tool_request/task_draft steps, malformed
# output) is exercised by feeding `parse_plan` its own literal fixtures
# directly.
FAKE_AGENT_PLAN_RESPONSE = (
    '{"steps": [{"kind": "read", "description": "Fake plan read step", "payload": {}}]}'
)


def _latest_user_content(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content", "")).lower()
    return ""


class FakeAdapter(BaseAdapter):
    def _text_for(self, purpose, messages=None):
        if purpose == "memory.extraction":
            return FAKE_MEMORY_EXTRACTION_RESPONSE
        if purpose == "router.intent":
            latest = _latest_user_content(messages)
            if _TASK_DRAFT_TRIGGER in latest:
                return FAKE_ROUTER_TASK_DRAFT_INTENT_RESPONSE
            if _TOOL_REQUEST_TRIGGER in latest:
                return FAKE_ROUTER_TOOL_REQUEST_INTENT_RESPONSE
            if _MEMORY_QUERY_TRIGGER in latest:
                return FAKE_ROUTER_MEMORY_QUERY_INTENT_RESPONSE
            if _KNOWLEDGE_QUERY_TRIGGER in latest:
                return FAKE_ROUTER_KNOWLEDGE_QUERY_INTENT_RESPONSE
            return FAKE_ROUTER_INTENT_RESPONSE
        if purpose == "router.task_draft":
            return FAKE_TASK_DRAFT_RESPONSE
        if purpose == "agent.plan":
            return FAKE_AGENT_PLAN_RESPONSE
        return FAKE_RESPONSE

    def _result(self, messages, purpose):
        text = self._text_for(purpose, messages)
        input_tokens = sum(self.estimate_tokens(str(m.get("content", ""))) for m in messages)
        return AdapterResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=self.estimate_tokens(text),
            finish_reason="stop",
            raw_usage={"fake": True},
        )

    def complete(self, messages, params, *, purpose=""):
        return self._result(messages, purpose)

    def stream(self, messages, params, *, purpose=""):
        text = self._text_for(purpose, messages)
        words = text.split(" ")
        for word in words[:-1]:
            yield word + " "
        yield words[-1]
        return self._result(messages, purpose)

    def embed(self, texts, *, purpose=""):
        vectors = [embed_text(t) for t in texts]
        input_tokens = sum(self.estimate_tokens(t) for t in texts)
        return EmbedAdapterResult(
            vectors=vectors, input_tokens=input_tokens, raw_usage={"fake": True}
        )
