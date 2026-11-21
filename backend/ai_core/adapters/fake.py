"""Deterministic adapter for tests and local development. No network access."""

import hashlib
import json
import math
import random
import re

from ai_core.adapters.base import (
    AdapterResult,
    BaseAdapter,
    EmbedAdapterResult,
    SynthesizeAdapterResult,
    TranscribeAdapterResult,
)
from ai_core.audio import silent_wav, wav_seconds_or_zero

FAKE_RESPONSE = "Merhaba, ben Friday. MVP 1 gateway üzerinden çalışıyorum."

# Deterministic STT/TTS fixtures (MVP 12). No audio decoding/encoding —
# `transcribe` returns a fixed transcript regardless of the input bytes, and
# `synthesize` returns fixed placeholder bytes. Real audio round-trips are
# exercised on the user's own machine against a local Whisper/Piper backend,
# never in tests (rule 5: LLM/model-calling code is mockable; tests never
# hit a real model). `audio_seconds` is a fixed fake duration so the
# gateway's cost/observability recording has a non-zero value to store.
FAKE_TRANSCRIPT = "Bu, sahte adaptörün ürettiği örnek bir çevrim yazısıdır."
FAKE_AUDIO_SECONDS = 1.5
# Phase 6 D3: the fake TTS output is a real (silent) 16 kHz mono PCM16 WAV,
# so callers that validate TTS output (the voice endpoints, branch 2) accept
# it exactly as they would a real speech server's.
FAKE_AUDIO_BYTES = silent_wav(FAKE_AUDIO_SECONDS)
FAKE_AUDIO_CONTENT_TYPE = "audio/wav"

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
#
# Schema v2 (Phase 8A brief D9): every candidate carries a `quote` that must
# match a `user`-role message verbatim, so the fixture quotes the latest
# plain (unfenced) user message -- a short one ("hi") is still quoted and
# then dropped by memory's provenance check, exactly like a real model's.
FAKE_MEMORY_EXTRACTION_TITLE = "Fake extracted preference"
FAKE_MEMORY_EXTRACTION_CONTENT = "Deterministic FakeAdapter candidate for memory.extraction."
_FAKE_QUOTE_CHARS = 200


def _latest_plain_user_text(messages):
    for message in reversed(messages or []):
        content = str(message.get("content", ""))
        if message.get("role") == "user" and not content.startswith("<untrusted_context"):
            return content.strip()
    return ""


def fake_memory_extraction_response(messages):
    quote = _latest_plain_user_text(messages)[:_FAKE_QUOTE_CHARS]
    candidates = []
    if quote:
        candidates.append(
            {
                "type": "preference",
                "title": FAKE_MEMORY_EXTRACTION_TITLE,
                "content": FAKE_MEMORY_EXTRACTION_CONTENT,
                "quote": quote,
            }
        )
    return json.dumps({"version": 2, "candidates": candidates})


FAKE_MEMORY_EXTRACTION_RESPONSE = fake_memory_extraction_response(
    [{"role": "user", "content": "I always prefer short answers."}]
)

# Purpose-aware fixtures for the Phase 8A learning loop (brief D9, D10),
# hand-written mirrors of `memory.ingestion.extraction` / `.consolidation`'s
# v1 schemas (ai_core must not import memory). Episode extraction: one
# `memory` candidate per `<owner_item>` of the payload, quoting the item's
# first characters verbatim. Consolidation: keep every candidate, in order,
# with its own title/content.
_OWNER_ITEM = re.compile(r'<owner_item episode_id="([^"]+)"[^>]*>\n(.*?)\n</owner_item>', re.DOTALL)
_CANDIDATE_INDEX = re.compile(r'<candidate index="(\d+)"')


def _all_content(messages):
    return "\n".join(str(m.get("content", "")) for m in messages or [])


def fake_episode_extraction_response(messages, limit=None):
    """Schema v2 (Phase 8B D4): every candidate carries `fact: null`."""
    candidates = []
    for episode_id, text in _OWNER_ITEM.findall(_all_content(messages))[:limit]:
        text = text.strip()
        candidates.append(
            {
                "type": "semantic",
                "title": "Fake learned fact",
                "content": f"Fake learned: {text[:80]}",
                "quote": text[:_FAKE_QUOTE_CHARS],
                "episode_id": episode_id,
                "target": "memory",
                "fact": None,
            }
        )
    return json.dumps({"version": 2, "candidates": candidates})


# Phase 8B (brief D4/D5), hand-written mirrors as above: reconciliation
# decides nothing (every candidate stays `new`); the weekly reflection
# proposes one memory per owner item, at most 5.
FAKE_RECONCILIATION_RESPONSE = '{"version": 1, "decisions": []}'


def fake_reflection_response(messages):
    return fake_episode_extraction_response(messages, limit=5)


def fake_consolidation_response(messages):
    indexes = [int(i) for i in _CANDIDATE_INDEX.findall(_all_content(messages))]
    return json.dumps(
        {
            "version": 1,
            "proposals": [{"index": i, "title": None, "content": None} for i in indexes],
        }
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
# apps reason as the fixtures above. Default is a single `read`-kind step;
# per-case behavior (multi-step plans, tool_request/task_draft steps,
# malformed output) is exercised by feeding `parse_plan` its own literal
# fixtures directly in unit tests. The CONTENT-triggered multi-step variant
# below exists only so the Playwright e2e suite (branch 5, brief §6 -- a
# real Docker backend process it cannot monkeypatch) can drive a goal
# through all three step kinds in one plan, mirroring `router.intent`'s
# identical trigger-phrase convention above.
FAKE_AGENT_PLAN_RESPONSE = (
    '{"steps": [{"kind": "read", "description": "Fake plan read step", "payload": {}}]}'
)
_AGENT_MULTI_STEP_PLAN_TRIGGER = "full plan"
FAKE_AGENT_MULTI_STEP_PLAN_RESPONSE = (
    '{"steps": ['
    '{"kind": "read", "description": "Summarize relevant notes", "payload": {}}, '
    '{"kind": "task_draft", "description": "Draft a follow-up task", '
    '"payload": {"title": "Follow up on the summary", "due_date": null}}, '
    '{"kind": "tool_request", "description": "Export the vault", '
    '"payload": {"tool_name": "vault_export"}}'
    "]}"
)

# Purpose-aware fixture for `agent.read_step` (MVP 7 §3) -- free text, not
# JSON (this purpose has no schema/parser, unlike the others above).
FAKE_AGENT_READ_STEP_RESPONSE = "Fake read-step summary: nothing notable was retrieved."

# Purpose-aware fixture for `agent.skill_match` (MVP 8 §3) -- mirrors
# agents.skill_match's schema for the same ai_core-must-not-import-business-
# apps reason as the fixtures above. Default is an explicit no-match; the
# CONTENT-triggered matched variant below exists only so the Playwright e2e
# suite (a real Docker backend process it cannot monkeypatch) can drive a
# goal through the skill-selection path, mirroring `router.intent`'s/
# `agent.plan`'s identical trigger-phrase convention. Per-case unit-test
# behavior (hallucinated names, parse failures) is exercised by feeding
# `parse_skill_match` its own literal fixtures directly, or by monkeypatching
# the gateway call/parser -- never by relying on this string.
FAKE_AGENT_SKILL_MATCH_RESPONSE = '{"skill_name": null}'
_AGENT_SKILL_MATCH_TRIGGER = "export my weekly notes"
FAKE_AGENT_SKILL_MATCH_MATCHED_RESPONSE = '{"skill_name": "weekly_export"}'

# Purpose-aware fixture for `harness.briefing` (Phase 2 branch 3) --
# mirrors harness.execution._parse_briefing_response's expected shape, the
# same ai_core-must-not-import-business-apps reason as the fixtures above.
FAKE_HARNESS_BRIEFING_RESPONSE = (
    '{"summary": "Fake briefing summary: nothing notable was found.", "findings": []}'
)

# Purpose-aware fixture for `harness.review_summary` (Phase 3 branch 3 as
# `harness.review`; renamed with the Phase 5 D11 purpose split; Codex
# code-review P2, round 12) -- mirrors harness.review_execution.
# _parse_review_response's expected shape (summary/verdict/findings), the
# same reason as the fixtures above. Without this, AI_FAKE_PROVIDER=true
# (the dev/example default) routed every review through the generic
# non-JSON FAKE_RESPONSE, which _parse_review_response can't parse --
# every otherwise-successful review would fail closed as
# review_unavailable in that mode. Defaults to an explicit approval, the
# same benign-default convention FAKE_HARNESS_BRIEFING_RESPONSE already
# uses (an empty findings list, not a hallucinated one).
FAKE_HARNESS_REVIEW_RESPONSE = (
    '{"summary": "Fake review summary: no issues found.", "verdict": "approved", "findings": []}'
)

# Purpose-aware fixture for `harness.diff_summary` (Phase 3 branch 6) --
# mirrors harness.implementation_execution._parse_diff_summary_response.
FAKE_HARNESS_DIFF_SUMMARY_RESPONSE = (
    '{"summary": "Fake diff summary: a small change across the listed files."}'
)


def _latest_user_content(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content", "")).lower()
    return ""


class FakeAdapter(BaseAdapter):
    def _text_for(self, purpose, messages=None):
        if purpose == "memory.extraction":
            return fake_memory_extraction_response(messages)
        if purpose == "memory.episode_extraction":
            return fake_episode_extraction_response(messages)
        if purpose == "memory.consolidation":
            return fake_consolidation_response(messages)
        if purpose == "memory.fact_reconciliation":
            return FAKE_RECONCILIATION_RESPONSE
        if purpose == "memory.reflection":
            return fake_reflection_response(messages)
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
            latest = _latest_user_content(messages)
            if _AGENT_MULTI_STEP_PLAN_TRIGGER in latest:
                return FAKE_AGENT_MULTI_STEP_PLAN_RESPONSE
            return FAKE_AGENT_PLAN_RESPONSE
        if purpose == "agent.read_step":
            return FAKE_AGENT_READ_STEP_RESPONSE
        if purpose == "agent.skill_match":
            latest = _latest_user_content(messages)
            if _AGENT_SKILL_MATCH_TRIGGER in latest:
                return FAKE_AGENT_SKILL_MATCH_MATCHED_RESPONSE
            return FAKE_AGENT_SKILL_MATCH_RESPONSE
        if purpose == "harness.briefing":
            return FAKE_HARNESS_BRIEFING_RESPONSE
        if purpose == "harness.review_summary":
            return FAKE_HARNESS_REVIEW_RESPONSE
        if purpose == "harness.diff_summary":
            return FAKE_HARNESS_DIFF_SUMMARY_RESPONSE
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

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        # A valid WAV reports its real header duration (like the real
        # adapter, D3); anything else keeps the fixed fake duration.
        seconds = wav_seconds_or_zero(audio) or FAKE_AUDIO_SECONDS
        return TranscribeAdapterResult(
            text=FAKE_TRANSCRIPT,
            audio_seconds=seconds,
            output_tokens=self.estimate_tokens(FAKE_TRANSCRIPT),
            raw_usage={"fake": True},
            language=(params or {}).get("language", ""),
        )

    def synthesize(self, text, *, params=None, purpose=""):
        return SynthesizeAdapterResult(
            audio=FAKE_AUDIO_BYTES,
            content_type=FAKE_AUDIO_CONTENT_TYPE,
            audio_seconds=FAKE_AUDIO_SECONDS,
            input_tokens=self.estimate_tokens(text),
            raw_usage={"fake": True},
        )
