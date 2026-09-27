import math

import pytest

from ai_core.adapters.fake import (
    FAKE_AGENT_MULTI_STEP_PLAN_RESPONSE,
    FAKE_AGENT_PLAN_RESPONSE,
    FAKE_AGENT_SKILL_MATCH_MATCHED_RESPONSE,
    FAKE_AGENT_SKILL_MATCH_RESPONSE,
    FAKE_EMBEDDING_DIMENSION,
    FAKE_HARNESS_BRIEFING_RESPONSE,
    FAKE_HARNESS_REVIEW_RESPONSE,
    FAKE_MEMORY_EXTRACTION_RESPONSE,
    FAKE_RESPONSE,
    FAKE_ROUTER_INTENT_RESPONSE,
    FAKE_ROUTER_KNOWLEDGE_QUERY_INTENT_RESPONSE,
    FAKE_ROUTER_MEMORY_QUERY_INTENT_RESPONSE,
    FAKE_ROUTER_TASK_DRAFT_INTENT_RESPONSE,
    FAKE_ROUTER_TOOL_REQUEST_INTENT_RESPONSE,
    FAKE_TASK_DRAFT_RESPONSE,
    FAKE_TRANSCRIPT,
    FakeAdapter,
    embed_text,
)


def make_adapter():
    return FakeAdapter(provider=None)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# The exact strings branch 6's Playwright e2e seeds and queries with — this
# test proves the fake embedding's bag-of-words signal genuinely ranks the
# seeded document above unrelated noise before the e2e test ever depends on
# it (MVP 3 brief §1, round-3 review finding).
SEED_DOCUMENT_TEXT = (
    "Jarvis OS supports offline lantern mode for camping trips without cell signal."
)
QUERY_TEXT = "What lantern mode does Jarvis OS support when camping offline?"
NOISE_DOCUMENT_TEXT = "The quarterly budget review meeting is scheduled for next Tuesday afternoon."


class TestFakeEmbedding:
    def test_embedding_has_fixed_dimension(self):
        vector = embed_text("hello world")
        assert len(vector) == FAKE_EMBEDDING_DIMENSION

    def test_embedding_is_deterministic(self):
        assert embed_text("hello world") == embed_text("hello world")

    def test_embedding_is_l2_normalized(self):
        vector = embed_text("some distinctive shared vocabulary here")
        norm = math.sqrt(sum(v * v for v in vector))
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_empty_text_returns_zero_vector(self):
        assert embed_text("") == [0.0] * FAKE_EMBEDDING_DIMENSION

    def test_seed_document_ranks_above_noise_for_query(self):
        seed_score = cosine(embed_text(SEED_DOCUMENT_TEXT), embed_text(QUERY_TEXT))
        noise_score = cosine(embed_text(NOISE_DOCUMENT_TEXT), embed_text(QUERY_TEXT))
        assert seed_score > noise_score
        assert seed_score - noise_score > 0.2  # not a marginal ranking

    def test_adapter_embed_returns_one_vector_per_text(self):
        result = make_adapter().embed([SEED_DOCUMENT_TEXT, NOISE_DOCUMENT_TEXT])
        assert len(result.vectors) == 2
        assert len(result.vectors[0]) == FAKE_EMBEDDING_DIMENSION
        assert result.input_tokens > 0
        assert result.output_tokens == 0


class TestPurposeAwareFixtures:
    def test_default_purpose_returns_chat_fixture(self):
        result = make_adapter().complete([{"role": "user", "content": "hi"}], {})
        assert result.text == FAKE_RESPONSE

    def test_chat_purpose_returns_chat_fixture(self):
        result = make_adapter().complete([{"role": "user", "content": "hi"}], {}, purpose="chat")
        assert result.text == FAKE_RESPONSE

    def test_memory_extraction_purpose_returns_extraction_fixture(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "hi"}], {}, purpose="memory.extraction"
        )
        assert result.text == FAKE_MEMORY_EXTRACTION_RESPONSE

    def test_stream_default_purpose_reassembles_to_chat_fixture(self):
        gen = make_adapter().stream([{"role": "user", "content": "hi"}], {})
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration:
            pass
        assert "".join(chunks) == FAKE_RESPONSE

    def test_stream_memory_extraction_purpose_reassembles_to_extraction_fixture(self):
        gen = make_adapter().stream(
            [{"role": "user", "content": "hi"}], {}, purpose="memory.extraction"
        )
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration:
            pass
        assert "".join(chunks) == FAKE_MEMORY_EXTRACTION_RESPONSE

    def test_router_intent_purpose_returns_router_fixture(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "hi"}], {}, purpose="router.intent"
        )
        assert result.text == FAKE_ROUTER_INTENT_RESPONSE

    def test_stream_router_intent_purpose_reassembles_to_router_fixture(self):
        gen = make_adapter().stream(
            [{"role": "user", "content": "hi"}], {}, purpose="router.intent"
        )
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration:
            pass
        assert "".join(chunks) == FAKE_ROUTER_INTENT_RESPONSE

    def test_router_intent_content_trigger_task_draft(self):
        """MVP 6 §6: content-based triggers exist ONLY so the Playwright e2e
        suite (a real Docker backend process it can't monkeypatch) can still
        exercise every intent, using the exact phrases the unit tests
        already send when they monkeypatch `parse_intent` directly."""
        result = make_adapter().complete(
            [{"role": "user", "content": "remind me to call mom"}], {}, purpose="router.intent"
        )
        assert result.text == FAKE_ROUTER_TASK_DRAFT_INTENT_RESPONSE

    def test_router_intent_content_trigger_tool_request(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "export my vault please"}], {}, purpose="router.intent"
        )
        assert result.text == FAKE_ROUTER_TOOL_REQUEST_INTENT_RESPONSE

    def test_router_intent_content_trigger_memory_query(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "what do I like to eat"}], {}, purpose="router.intent"
        )
        assert result.text == FAKE_ROUTER_MEMORY_QUERY_INTENT_RESPONSE

    def test_router_intent_content_trigger_knowledge_query(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "please summarize my knowledge base"}],
            {},
            purpose="router.intent",
        )
        assert result.text == FAKE_ROUTER_KNOWLEDGE_QUERY_INTENT_RESPONSE

    def test_router_intent_no_trigger_still_returns_chat(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "hi there, how are you"}], {}, purpose="router.intent"
        )
        assert result.text == FAKE_ROUTER_INTENT_RESPONSE

    def test_router_intent_trigger_checks_latest_user_message_only(self):
        """A trigger phrase in an earlier turn (history) must not leak into
        classifying an unrelated later message."""
        result = make_adapter().complete(
            [
                {"role": "user", "content": "remind me to call mom"},
                {"role": "assistant", "content": "Sure, confirm below."},
                {"role": "user", "content": "actually never mind, just saying hi"},
            ],
            {},
            purpose="router.intent",
        )
        assert result.text == FAKE_ROUTER_INTENT_RESPONSE

    def test_task_draft_purpose_returns_task_draft_fixture(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "hi"}], {}, purpose="router.task_draft"
        )
        assert result.text == FAKE_TASK_DRAFT_RESPONSE

    def test_stream_task_draft_purpose_reassembles_to_task_draft_fixture(self):
        gen = make_adapter().stream(
            [{"role": "user", "content": "hi"}], {}, purpose="router.task_draft"
        )
        chunks = []
        try:
            while True:
                chunks.append(next(gen))
        except StopIteration:
            pass
        assert "".join(chunks) == FAKE_TASK_DRAFT_RESPONSE

    def test_agent_plan_purpose_returns_single_read_step_fixture(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "summarize my notes"}], {}, purpose="agent.plan"
        )
        assert result.text == FAKE_AGENT_PLAN_RESPONSE

    def test_agent_plan_content_trigger_returns_multi_step_fixture(self):
        """MVP 7 §6: mirrors router.intent's content-trigger convention so the
        Playwright e2e suite (a real Docker backend process it can't
        monkeypatch) can drive a goal through all three step kinds in one
        plan."""
        result = make_adapter().complete(
            [{"role": "user", "content": "give me the full plan for this"}],
            {},
            purpose="agent.plan",
        )
        assert result.text == FAKE_AGENT_MULTI_STEP_PLAN_RESPONSE

    def test_agent_plan_trigger_checks_latest_user_message_only(self):
        """A trigger phrase in an earlier turn (history) must not leak into
        planning for an unrelated later goal."""
        result = make_adapter().complete(
            [
                {"role": "user", "content": "give me the full plan for this"},
                {"role": "assistant", "content": "Sure, here is the plan."},
                {"role": "user", "content": "actually just summarize my notes"},
            ],
            {},
            purpose="agent.plan",
        )
        assert result.text == FAKE_AGENT_PLAN_RESPONSE

    def test_agent_skill_match_purpose_returns_null_by_default(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "do something unrelated"}],
            {},
            purpose="agent.skill_match",
        )
        assert result.text == FAKE_AGENT_SKILL_MATCH_RESPONSE

    def test_agent_skill_match_content_trigger_returns_matched_fixture(self):
        """MVP 8 §6: mirrors router.intent's/agent.plan's content-trigger
        convention so the Playwright e2e suite (a real Docker backend
        process it can't monkeypatch) can drive a goal through the skill-
        selection path."""
        result = make_adapter().complete(
            [{"role": "user", "content": "please export my weekly notes for me"}],
            {},
            purpose="agent.skill_match",
        )
        assert result.text == FAKE_AGENT_SKILL_MATCH_MATCHED_RESPONSE

    def test_agent_skill_match_trigger_checks_latest_user_message_only(self):
        result = make_adapter().complete(
            [
                {"role": "user", "content": "export my weekly notes"},
                {"role": "assistant", "content": "Sure, exporting now."},
                {"role": "user", "content": "actually never mind, do something else"},
            ],
            {},
            purpose="agent.skill_match",
        )
        assert result.text == FAKE_AGENT_SKILL_MATCH_RESPONSE

    def test_harness_briefing_purpose_returns_briefing_fixture(self):
        result = make_adapter().complete(
            [{"role": "user", "content": "<repo_transcript>...</repo_transcript>"}],
            {},
            purpose="harness.briefing",
        )
        assert result.text == FAKE_HARNESS_BRIEFING_RESPONSE

    def test_harness_review_purpose_returns_review_fixture(self):
        """Codex code-review P2, branch 3 round 12: without a purpose-
        aware fixture for the review summary (`harness.review_summary`
        since the Phase 5 D11 purpose split), AI_FAKE_PROVIDER=true routed
        every review through the generic non-JSON FAKE_RESPONSE, which
        `_parse_review_response` can't parse -- every otherwise-
        successful review failed closed as `review_unavailable` in that
        (dev/example-default) mode."""
        result = make_adapter().complete(
            [{"role": "user", "content": "<repo_transcript>...</repo_transcript>"}],
            {},
            purpose="harness.review_summary",
        )
        assert result.text == FAKE_HARNESS_REVIEW_RESPONSE

    def test_real_adapters_accept_purpose_kwarg_without_using_it(self):
        """The purpose kwarg is an ai_core-internal API addition for every
        adapter, not a fake-only parameter — real adapters must accept it
        even though they ignore it."""
        import inspect

        from ai_core.adapters.anthropic import AnthropicAdapter
        from ai_core.adapters.openai_compat import OpenAICompatibleAdapter

        for adapter_cls in (AnthropicAdapter, OpenAICompatibleAdapter):
            complete_params = inspect.signature(adapter_cls.complete).parameters
            stream_params = inspect.signature(adapter_cls.stream).parameters
            assert "purpose" in complete_params
            assert "purpose" in stream_params


class TestFakeAudioAdapters:
    def test_transcribe_returns_fixed_transcript_and_duration(self):
        adapter = FakeAdapter(provider=None)
        result = adapter.transcribe(b"any-bytes", content_type="audio/wav", purpose="voice.stt")
        assert result.text == FAKE_TRANSCRIPT
        assert result.audio_seconds > 0
        assert result.input_tokens == 0  # audio in, not text
        assert result.output_tokens > 0  # transcript estimate

    def test_synthesize_returns_bytes_and_content_type(self):
        adapter = FakeAdapter(provider=None)
        result = adapter.synthesize("Merhaba", purpose="voice.tts")
        assert isinstance(result.audio, bytes) and result.audio
        assert result.content_type == "audio/wav"
        assert result.audio_seconds > 0
        assert result.input_tokens > 0  # source-text estimate
        assert result.output_tokens == 0  # audio out, not text

    def test_real_adapters_accept_audio_kwargs_without_supporting_them(self):
        """Every adapter inherits BaseAdapter's transcribe/synthesize, which
        raise `not_supported` — the base contract exists so the gateway can
        call them uniformly; real STT/TTS wiring is MVP 12 §2-§3."""
        from ai_core.adapters.base import AdapterError
        from ai_core.adapters.openai_compat import OpenAICompatibleAdapter

        adapter = OpenAICompatibleAdapter(provider=None)
        with pytest.raises(AdapterError):
            adapter.transcribe(b"x", purpose="voice.stt")
        with pytest.raises(AdapterError):
            adapter.synthesize("x", purpose="voice.tts")
