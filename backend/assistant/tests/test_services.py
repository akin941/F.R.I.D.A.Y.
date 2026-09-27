import json

import pytest
from django.core.exceptions import ImproperlyConfigured

from accounts.services import register_user
from ai_core.exceptions import GatewayError
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.intent import IntentParseError
from assistant.models import Conversation, Message
from assistant.services import (
    build_gateway_stream,
    classify_intent,
    create_conversation,
    mark_conversation_private,
    sse_event,
    stream_assistant_reply,
)
from assistant.task_draft import TaskDraftParseError
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from core.context import get_request_id, request_id_var
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def workspace(alice):
    return Workspace.objects.get(owner=alice)


@pytest.fixture
def conversation(workspace, alice):
    return Conversation.objects.create(workspace=workspace, created_by=alice)


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def system_prompt():
    template = PromptTemplate.objects.create(name="jarvis_system", purpose="chat")
    version = PromptVersion.objects.create(template=template, version=1, content="You are Jarvis.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def router_intent_prompt():
    template = PromptTemplate.objects.create(name="jarvis_router_intent", purpose="router.intent")
    version = PromptVersion.objects.create(
        template=template, version=1, content="Classify the message."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def task_draft_prompt():
    template = PromptTemplate.objects.create(
        name="jarvis_router_task_draft", purpose="router.task_draft"
    )
    version = PromptVersion.objects.create(
        template=template, version=1, content="Extract the task."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def _decode(event: bytes) -> dict:
    assert event.startswith(b"data: ")
    assert event.endswith(b"\n\n")
    return json.loads(event[len(b"data: ") : -len(b"\n\n")].decode("utf-8"))


# --- create_conversation: audited conversation creation ---


def test_create_conversation_creates_audit_row(alice, workspace):
    conversation = create_conversation(workspace=workspace, actor=alice, title="Hi")
    assert conversation.title == "Hi"
    assert conversation.created_by == alice

    entry = AuditLog.objects.get(action="assistant.conversation.created")
    assert entry.actor == alice
    assert entry.workspace == workspace
    assert entry.resource_type == "assistant.conversation"
    assert entry.resource_id == str(conversation.id)
    assert entry.metadata == {"private": False}


def test_create_conversation_is_atomic_with_its_audit_row(alice, workspace, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr("assistant.services.record_audit", boom)
    with pytest.raises(RuntimeError):
        create_conversation(workspace=workspace, actor=alice, title="Hi")
    assert Conversation.objects.count() == 0


# --- sse_event framing ---


def test_sse_event_framing_and_unicode():
    event = sse_event({"type": "chunk", "text": "Merhaba 🚀"})
    assert _decode(event) == {"type": "chunk", "text": "Merhaba 🚀"}


# --- stream_assistant_reply: happy paths ---


def test_multiple_chunks_one_done_event_and_persistence(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(
            conversation, iter(["Hel", "lo"]), usage_holder, request_id="test-request-id"
        )
    )
    payloads = [_decode(e) for e in events]
    assert [p["type"] for p in payloads] == ["chunk", "chunk", "done"]
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.content == "Hello"
    assert message.status == Message.Status.COMPLETE
    assert payloads[-1]["message_id"] == str(message.id)


# --- draft SSE event (MVP 6 §3/§5) ---


def test_draft_event_precedes_done_and_never_replaces_it(conversation):
    usage_holder = {}
    draft_event = {"type": "draft", "kind": "task", "source_message_id": "abc", "title": "Call mom"}
    events = list(
        stream_assistant_reply(
            conversation,
            iter(["Hel", "lo"]),
            usage_holder,
            request_id="test-request-id",
            draft_event=draft_event,
        )
    )
    payloads = [_decode(e) for e in events]
    assert [p["type"] for p in payloads] == ["chunk", "chunk", "draft", "done"]
    assert payloads[-2] == draft_event


def test_no_draft_event_when_none_passed(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(
            conversation, iter(["hi"]), usage_holder, request_id="test-request-id"
        )
    )
    payloads = [_decode(e) for e in events]
    assert [p["type"] for p in payloads] == ["chunk", "done"]


def test_draft_event_never_emitted_on_error_terminal_event(conversation):
    """A draft was already decided independently of whether the chat reply
    itself streams successfully -- but this MVP's ordering contract only
    covers `chunk...chunk -> draft -> done`; on a gateway failure the
    terminal event is `error`, and `draft` must not appear at all rather
    than appearing before the wrong terminal event."""
    usage_holder = {}
    draft_event = {"type": "draft", "kind": "task", "source_message_id": "abc", "title": "x"}
    events = list(
        stream_assistant_reply(
            conversation,
            _raise_before_any_chunk("timeout"),
            usage_holder,
            request_id="test-request-id",
            draft_event=draft_event,
        )
    )
    payloads = [_decode(e) for e in events]
    assert [p["type"] for p in payloads] == ["error"]


def test_done_usage_values_come_from_usage_holder(conversation):
    usage = ModelUsage.objects.create(
        purpose="chat",
        workspace=conversation.workspace,
        input_tokens=3,
        output_tokens=5,
        estimated_cost="0.001",
    )
    usage_holder = {"usage": usage}
    events = list(
        stream_assistant_reply(
            conversation, iter(["hi"]), usage_holder, request_id="test-request-id"
        )
    )
    done = _decode(events[-1])
    assert done["usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "estimated_cost": str(usage.estimated_cost),
    }
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.model_usage == usage


def test_empty_successful_stream(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(conversation, iter([]), usage_holder, request_id="test-request-id")
    )
    assert [_decode(e)["type"] for e in events] == ["done"]
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.content == ""
    assert message.status == Message.Status.COMPLETE


def test_message_persisted_before_done_event_reaches_consumer(conversation):
    """Regression: `done` must never reach the client before the row exists —
    a client that fetches the conversation right after `done` would otherwise
    race the write."""
    usage_holder = {}
    gen = stream_assistant_reply(conversation, iter([]), usage_holder, request_id="test-request-id")
    done_event = next(gen)  # empty stream: first (and only) item is `done`
    assert _decode(done_event)["type"] == "done"
    assert Message.objects.filter(role=Message.Role.ASSISTANT).exists()


# --- stream_assistant_reply: gateway failure ---


def _raise_before_any_chunk(error_type):
    def gen():
        raise GatewayError(error_type)
        yield  # pragma: no cover — unreachable, makes this a generator

    return gen()


def _raise_after_one_chunk(error_type):
    def gen():
        yield "partial "
        raise GatewayError(error_type)

    return gen()


def test_gateway_error_before_chunks_persists_failed(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(
            conversation,
            _raise_before_any_chunk("timeout"),
            usage_holder,
            request_id="test-request-id",
        )
    )
    assert [_decode(e) for e in events] == [{"type": "error", "error_type": "timeout"}]
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert message.content == ""


def test_message_persisted_before_error_event_reaches_consumer(conversation):
    usage_holder = {}
    gen = stream_assistant_reply(
        conversation, _raise_before_any_chunk("timeout"), usage_holder, request_id="test-request-id"
    )
    error_event = next(gen)
    assert _decode(error_event)["type"] == "error"
    assert Message.objects.filter(
        role=Message.Role.ASSISTANT, status=Message.Status.FAILED
    ).exists()


def test_gateway_error_after_chunks_persists_partial(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(
            conversation,
            _raise_after_one_chunk("provider_error"),
            usage_holder,
            request_id="test-request-id",
        )
    )
    types = [_decode(e)["type"] for e in events]
    assert types == ["chunk", "error"]
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.content == "partial "


def test_partial_message_still_linked_to_usage(conversation):
    usage = ModelUsage.objects.create(purpose="chat", workspace=conversation.workspace)
    usage_holder = {"usage": usage}

    def gen():
        yield "partial "
        raise GatewayError("provider_error")

    list(stream_assistant_reply(conversation, gen(), usage_holder, request_id="test-request-id"))
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.model_usage == usage


# --- stream_assistant_reply: client disconnect ---


def test_disconnect_before_any_chunk_persists_failed(conversation):
    usage_holder = {}
    outer = stream_assistant_reply(
        conversation, _raise_before_any_chunk_exit(), usage_holder, request_id="test-request-id"
    )
    with pytest.raises(GeneratorExit):
        next(outer)
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert message.content == ""


def _raise_before_any_chunk_exit():
    def gen():
        raise GeneratorExit()
        yield  # pragma: no cover — unreachable, makes this a generator

    return gen()


def _two_chunks():
    yield "a"
    yield "b"


def test_disconnect_after_chunks_persists_partial(conversation):
    usage_holder = {}
    outer = stream_assistant_reply(
        conversation, _two_chunks(), usage_holder, request_id="test-request-id"
    )
    first = next(outer)
    assert _decode(first)["type"] == "chunk"
    outer.close()
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.content == "a"


def test_inner_gateway_generator_is_closed_on_disconnect(conversation):
    closed = []

    def inner():
        try:
            yield "a"
            yield "b"
        finally:
            closed.append(True)

    usage_holder = {}
    outer = stream_assistant_reply(
        conversation, inner(), usage_holder, request_id="test-request-id"
    )
    next(outer)
    outer.close()  # raises no RuntimeError => no event was yielded after GeneratorExit
    assert closed == [True]


# --- build_gateway_stream: no_provider normalization ---


def test_no_provider_normalized_into_sse_error_without_second_usage_row(
    conversation, alice, system_prompt
):
    gateway_stream, usage_holder, _, _, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    events = list(
        stream_assistant_reply(
            conversation, gateway_stream, usage_holder, request_id="test-request-id"
        )
    )
    assert [_decode(e) for e in events] == [{"type": "error", "error_type": "no_provider"}]
    # Three rows total (no double-write for the chat call itself): one from
    # build_context's memory-retrieval embed() call and one from its
    # knowledge-retrieval embed() call (both no_provider, since no embedding
    # provider exists here either), one from this complete() call.
    assert ModelUsage.objects.filter(purpose="chat").count() == 1
    assert ModelUsage.objects.count() == 3
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED


def test_full_fake_gateway_integration_creates_one_usage_linked(
    conversation, alice, system_prompt, fake_provider
):
    gateway_stream, usage_holder, _, _, _ = build_gateway_stream(
        conversation, actor=alice, content="Merhaba", channel=CHANNEL_WEB
    )
    events = list(
        stream_assistant_reply(
            conversation, gateway_stream, usage_holder, request_id="test-request-id"
        )
    )
    assert _decode(events[-1])["type"] == "done"
    # fake_provider is chat-only; the memory- and knowledge-retrieval
    # embed() calls still each write their own (no_provider) ModelUsage row.
    assert ModelUsage.objects.filter(purpose="chat").count() == 1
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.COMPLETE
    assert message.model_usage == ModelUsage.objects.get(purpose="chat")


def test_gateway_call_keeps_the_version_resolved_at_context_build_time(
    conversation, alice, system_prompt, fake_provider
):
    """Regression: build_gateway_stream must reuse the exact PromptVersion
    build_context resolved, not re-query `active_version` — otherwise a
    repoint between the two calls would desync the recorded prompt lineage
    from the prompt content actually sent."""
    gateway_stream, usage_holder, _, _, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    new_version = PromptVersion.objects.create(
        template=system_prompt.template, version=2, content="A different prompt."
    )
    system_prompt.template.active_version = new_version
    system_prompt.template.save(update_fields=["active_version"])

    list(
        stream_assistant_reply(
            conversation, gateway_stream, usage_holder, request_id="test-request-id"
        )
    )
    usage = ModelUsage.objects.get(purpose="chat")
    assert usage.prompt_version == system_prompt


# --- request_id propagation into the deferred generator ---


def test_request_id_is_restored_during_deferred_iteration(conversation):
    """Regression: RequestIDMiddleware resets the contextvar as soon as the
    view returns the (unconsumed) StreamingHttpResponse — well before this
    generator actually runs — so it must re-publish request_id itself."""
    seen = []

    def gen():
        seen.append(get_request_id())
        yield "hi"
        seen.append(get_request_id())

    token = request_id_var.set(None)  # simulate the middleware having already reset it
    try:
        list(stream_assistant_reply(conversation, gen(), {}, request_id="req-123"))
    finally:
        request_id_var.reset(token)

    assert seen == ["req-123", "req-123"]
    assert get_request_id() is None  # restored to the pre-call value afterwards


# --- memories_used on failure paths (MVP 2 §2) ---


def test_memory_ids_persist_on_no_provider_failure(conversation, alice, system_prompt):
    from memory.models import Memory, compute_fingerprint

    row = Memory.objects.create(
        workspace=conversation.workspace,
        memory_type=Memory.Type.PREFERENCE,
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        title="Coffee",
        content="Drinks coffee black.",
        fingerprint=compute_fingerprint("Drinks coffee black."),
    )
    gateway_stream, usage_holder, memory_ids, _, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    events = list(
        stream_assistant_reply(
            conversation,
            gateway_stream,
            usage_holder,
            request_id="test-request-id",
            memory_ids=memory_ids,
        )
    )
    assert _decode(events[-1])["type"] == "error"
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert message.memories_used == [str(row.id)]


def test_memory_ids_persist_on_disconnect(conversation):
    outer = stream_assistant_reply(
        conversation,
        _two_chunks(),
        {},
        request_id="test-request-id",
        memory_ids=["11111111-1111-1111-1111-111111111111"],
    )
    next(outer)
    outer.close()
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.memories_used == ["11111111-1111-1111-1111-111111111111"]


# --- chunks_used contract (MVP 3 branch 4, mirrors memories_used exactly) ---


def test_chunk_ids_in_done_event_and_persisted(conversation):
    usage_holder = {}
    events = list(
        stream_assistant_reply(
            conversation,
            iter(["Hel", "lo"]),
            usage_holder,
            request_id="test-request-id",
            chunk_ids=["22222222-2222-2222-2222-222222222222"],
        )
    )
    done = _decode(events[-1])
    assert done["chunks_used"] == ["22222222-2222-2222-2222-222222222222"]
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.chunks_used == ["22222222-2222-2222-2222-222222222222"]


def test_chunks_used_defaults_to_empty_list(conversation):
    events = list(
        stream_assistant_reply(conversation, iter(["hi"]), {}, request_id="test-request-id")
    )
    assert _decode(events[-1])["chunks_used"] == []
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.chunks_used == []


def test_chunk_ids_persist_on_no_provider_failure(conversation, alice, system_prompt):
    gateway_stream, usage_holder, _, chunk_ids, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    events = list(
        stream_assistant_reply(
            conversation,
            gateway_stream,
            usage_holder,
            request_id="test-request-id",
            chunk_ids=chunk_ids,
        )
    )
    assert _decode(events[-1])["type"] == "error"
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert message.chunks_used == []  # no embedding provider exists in this test


def test_chunk_ids_persist_with_real_chunk_on_chat_no_provider_failure(
    conversation, alice, system_prompt
):
    """Sibling of test_memory_ids_persist_on_no_provider_failure (fourth
    independent-review pass, branch 4): proves a real, non-empty chunk_ids
    computed by build_context(, channel=CHANNEL_WEB) for THIS call survives an unrelated
    chat-gateway failure — not just the trivially-empty case above, where no
    embedding provider exists at all. Only an EMBEDDING-capability provider
    is created here (no CHAT one), so the knowledge-retrieval step succeeds
    independently of the chat complete() call, which fails no_provider."""
    from ai_core.adapters.fake import embed_text
    from ai_core.models import ModelProvider
    from knowledge.models import KnowledgeChunk, KnowledgeDocument

    embedding_provider = ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )
    document = KnowledgeDocument.objects.create(
        workspace=conversation.workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title="Doc",
        status=KnowledgeDocument.Status.READY,
        raw_text="hi there",
        content_hash="hash-real-chunk-e2e",
    )
    chunk = KnowledgeChunk.objects.create(
        workspace=conversation.workspace,
        document=document,
        ordinal=0,
        content="hi there",
        embedding=embed_text("hi there"),
        embedding_provider=embedding_provider,
    )
    gateway_stream, usage_holder, _, chunk_ids, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    assert chunk_ids == [str(chunk.id)]  # sanity: build_context really found it

    events = list(
        stream_assistant_reply(
            conversation,
            gateway_stream,
            usage_holder,
            request_id="test-request-id",
            chunk_ids=chunk_ids,
        )
    )
    assert _decode(events[-1])["type"] == "error"
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert message.chunks_used == [str(chunk.id)]


def test_chunk_ids_persist_on_disconnect(conversation):
    outer = stream_assistant_reply(
        conversation,
        _two_chunks(),
        {},
        request_id="test-request-id",
        chunk_ids=["33333333-3333-3333-3333-333333333333"],
    )
    next(outer)
    outer.close()
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert message.chunks_used == ["33333333-3333-3333-3333-333333333333"]


# --- extraction job allocation on assistant message persist (MVP 2 §3) ---

from memory.models import MemoryExtractionJob  # noqa: E402


def test_complete_reply_creates_an_extraction_job(conversation):
    outer = stream_assistant_reply(conversation, _two_chunks(), {}, request_id="test-request-id")
    list(outer)
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.COMPLETE
    job = MemoryExtractionJob.objects.get(conversation=conversation)
    assert job.through_message == message
    assert job.after_message is None
    assert job.status == MemoryExtractionJob.Status.PENDING
    assert job.workspace == conversation.workspace


def test_partial_reply_does_not_create_an_extraction_job(conversation):
    outer = stream_assistant_reply(conversation, _two_chunks(), {}, request_id="test-request-id")
    next(outer)
    outer.close()
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.PARTIAL
    assert not MemoryExtractionJob.objects.filter(conversation=conversation).exists()


def test_failed_reply_does_not_create_an_extraction_job(conversation):
    outer = stream_assistant_reply(
        conversation,
        _raise_before_any_chunk_exit(),
        {},
        request_id="test-request-id",
    )
    with pytest.raises(GeneratorExit):
        next(outer)
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert not MemoryExtractionJob.objects.filter(conversation=conversation).exists()


def test_no_provider_failure_does_not_create_an_extraction_job(conversation, alice, system_prompt):
    gateway_stream, usage_holder, memory_ids, _, _ = build_gateway_stream(
        conversation, actor=alice, content="hi", channel=CHANNEL_WEB
    )
    list(
        stream_assistant_reply(
            conversation,
            gateway_stream,
            usage_holder,
            request_id="test-request-id",
            memory_ids=memory_ids,
        )
    )
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.FAILED
    assert not MemoryExtractionJob.objects.filter(conversation=conversation).exists()


def test_two_complete_replies_allocate_adjacent_non_overlapping_jobs(conversation):
    list(stream_assistant_reply(conversation, _two_chunks(), {}, request_id="req-1"))
    list(stream_assistant_reply(conversation, _two_chunks(), {}, request_id="req-2"))
    jobs = list(
        MemoryExtractionJob.objects.filter(conversation=conversation).order_by("created_at")
    )
    assert len(jobs) == 2
    assert jobs[0].after_message is None
    assert jobs[1].after_message_id == jobs[0].through_message_id
    conversation.refresh_from_db()
    assert conversation.last_extracted_message_id == jobs[1].through_message_id


def test_private_conversation_creates_no_extraction_job(conversation, alice):
    """Enforcement point (a) of the private-flag gate (Phase 1
    p1-work-log) — allocation-time, the common case."""
    mark_conversation_private(conversation=conversation, actor=alice)
    list(stream_assistant_reply(conversation, _two_chunks(), {}, request_id="req-1"))
    message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert message.status == Message.Status.COMPLETE
    assert not MemoryExtractionJob.objects.filter(conversation=conversation).exists()


# --- mark_conversation_private: the one-way private flag (Phase 1 p1-work-log) ---


class TestMarkConversationPrivate:
    def test_sets_private_and_audits(self, conversation, alice):
        updated = mark_conversation_private(conversation=conversation, actor=alice)
        assert updated.private is True
        entry = AuditLog.objects.get(action="assistant.conversation.marked_private")
        assert entry.actor == alice
        assert entry.resource_id == str(conversation.id)

    def test_idempotent_repeat_is_a_noop_without_a_second_audit_row(self, conversation, alice):
        mark_conversation_private(conversation=conversation, actor=alice)
        before = AuditLog.objects.filter(action="assistant.conversation.marked_private").count()
        mark_conversation_private(conversation=conversation, actor=alice)
        after = AuditLog.objects.filter(action="assistant.conversation.marked_private").count()
        assert after == before == 1

    def test_cascades_to_already_extracted_memories(self, conversation, alice):
        from memory.models import Memory, compute_fingerprint

        content = "Prefers dark mode."
        pending = Memory.objects.create(
            workspace=conversation.workspace,
            memory_type=Memory.Type.PREFERENCE,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
            title="Dark mode",
            content=content,
            fingerprint=compute_fingerprint(content),
            source_conversation=conversation,
        )
        mark_conversation_private(conversation=conversation, actor=alice)
        pending.refresh_from_db()
        assert pending.status == Memory.Status.REJECTED


# --- classify_intent: router classification (MVP 6 §1) ---


class TestClassifyIntent:
    def test_missing_prompt_template_raises_improperly_configured(self, conversation, alice):
        with pytest.raises(ImproperlyConfigured):
            classify_intent(conversation, actor=alice, content="hi")

    def test_missing_active_version_raises_improperly_configured(self, conversation, alice):
        PromptTemplate.objects.create(name="jarvis_router_intent", purpose="router.intent")
        with pytest.raises(ImproperlyConfigured):
            classify_intent(conversation, actor=alice, content="hi")

    def test_fake_provider_classifies_as_chat_and_records_one_usage_row(
        self, conversation, alice, fake_provider, router_intent_prompt
    ):
        result = classify_intent(conversation, actor=alice, content="hi")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}
        assert ModelUsage.objects.filter(purpose="router.intent").count() == 1

    def test_no_provider_gateway_error_degrades_to_chat(
        self, conversation, alice, router_intent_prompt
    ):
        # No ModelProvider row registered at all -> GatewayError("no_provider").
        # The gateway still records its own ModelUsage row for the attempt
        # (success=False) before raising — ADR-003's "no gateway call skips
        # ModelUsage" guarantee applies to this failure path too, not just
        # the happy path.
        result = classify_intent(conversation, actor=alice, content="hi")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}
        assert ModelUsage.objects.filter(purpose="router.intent").count() == 1
        assert not ModelUsage.objects.get(purpose="router.intent").success

    def test_malformed_classifier_output_degrades_to_chat(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        import assistant.services as services_module

        def _boom(raw_text):
            raise IntentParseError("boom")

        monkeypatch.setattr(services_module, "parse_intent", _boom)
        result = classify_intent(conversation, actor=alice, content="hi")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}
        # The gateway call itself succeeded (FakeAdapter returned a response);
        # only our own post-hoc schema parsing failed — one successful
        # ModelUsage row still exists, distinct from the no-provider case.
        assert ModelUsage.objects.filter(purpose="router.intent").count() == 1
        assert ModelUsage.objects.get(purpose="router.intent").success

    def test_tool_request_intent_payload_carries_tool_name(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        import assistant.services as services_module

        def _tool_request(raw_text):
            return {"intent": Message.Intent.TOOL_REQUEST, "tool_name": "vault_export"}

        monkeypatch.setattr(services_module, "parse_intent", _tool_request)
        result = classify_intent(conversation, actor=alice, content="export my vault")
        assert result == {
            "intent": Message.Intent.TOOL_REQUEST,
            "intent_payload": {"tool_name": "vault_export", "payload": {}},
        }

    def test_non_tool_non_task_draft_intent_gets_empty_payload(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        """`task_draft` gets its own dedicated extraction pipeline (see
        TestClassifyIntentTaskDraft below) — this test covers every OTHER
        non-tool intent, which still gets a bare empty payload."""
        import assistant.services as services_module

        def _memory_query(raw_text):
            return {"intent": Message.Intent.MEMORY_QUERY, "tool_name": None}

        monkeypatch.setattr(services_module, "parse_intent", _memory_query)
        result = classify_intent(conversation, actor=alice, content="what do I like")
        assert result == {"intent": Message.Intent.MEMORY_QUERY, "intent_payload": {}}

    def test_unregistered_tool_name_degrades_to_chat(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        """MVP 6 §4: a tool_name that doesn't (or no longer) name a
        registered tool must never surface a confirm card for something
        that can't actually be confirmed -- it degrades exactly like a
        malformed classification, straight to chat."""
        import assistant.services as services_module

        def _unknown_tool(raw_text):
            return {"intent": Message.Intent.TOOL_REQUEST, "tool_name": "definitely_not_a_tool"}

        monkeypatch.setattr(services_module, "parse_intent", _unknown_tool)
        result = classify_intent(conversation, actor=alice, content="do the thing")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}

    def test_router_intent_prompt_lists_registered_tool_names(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        """The classification prompt's tool vocabulary is built from
        `tools.registry.list_tool_names()` at call time, not hardcoded --
        proves the vocabulary line actually reaches the gateway payload."""
        import assistant.services as services_module
        from ai_core.adapters.base import AdapterResult
        from ai_core.gateway import InferenceGateway

        captured = {}

        def _capture_complete(self, **kwargs):
            captured.update(kwargs)
            return AdapterResult(
                text='{"intent": "chat", "tool_name": null}',
                input_tokens=1,
                output_tokens=1,
                finish_reason="stop",
            )

        monkeypatch.setattr(InferenceGateway, "complete", _capture_complete)
        services_module.classify_intent(conversation, actor=alice, content="hi")
        system_content = captured["messages"][0]["content"]
        assert "vault_export" in system_content


# --- classify_intent's task_draft extraction pipeline (MVP 6 §3) ---


def _classified_as_task_draft(monkeypatch):
    import assistant.services as services_module

    def _task_draft(raw_text):
        return {"intent": Message.Intent.TASK_DRAFT, "tool_name": None}

    monkeypatch.setattr(services_module, "parse_intent", _task_draft)


class TestClassifyIntentTaskDraft:
    def test_missing_task_draft_prompt_template_raises_improperly_configured(
        self, conversation, alice, fake_provider, router_intent_prompt, monkeypatch
    ):
        _classified_as_task_draft(monkeypatch)
        with pytest.raises(ImproperlyConfigured):
            classify_intent(conversation, actor=alice, content="remind me to call mom")

    def test_happy_path_extracts_title_and_due_date(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        """FakeAdapter's router.task_draft fixture always returns a fixed
        title with no due date — proves the extraction plumbing end-to-end."""
        _classified_as_task_draft(monkeypatch)
        result = classify_intent(conversation, actor=alice, content="remind me to call mom")
        assert result == {
            "intent": Message.Intent.TASK_DRAFT,
            "intent_payload": {"title": "Fake extracted task", "due_date": None},
        }
        assert ModelUsage.objects.filter(purpose="router.task_draft").count() == 1

    def test_extraction_gateway_error_degrades_whole_turn_to_chat(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        """The FIRST (router.intent) call still succeeds via FakeAdapter;
        only the SECOND (router.task_draft) call fails -- the whole turn
        must still degrade to chat, not persist a task_draft intent with no
        usable draft."""
        _classified_as_task_draft(monkeypatch)
        from ai_core.gateway import InferenceGateway

        original_complete = InferenceGateway.complete

        def _maybe_fail(self, purpose, messages, **kwargs):
            if purpose == "router.task_draft":
                raise GatewayError("no_provider")
            return original_complete(self, purpose, messages, **kwargs)

        monkeypatch.setattr(InferenceGateway, "complete", _maybe_fail)
        result = classify_intent(conversation, actor=alice, content="remind me to call mom")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}

    def test_malformed_extraction_output_degrades_whole_turn_to_chat(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        _classified_as_task_draft(monkeypatch)
        import assistant.services as services_module

        def _boom(raw_text):
            raise TaskDraftParseError("boom")

        monkeypatch.setattr(services_module, "parse_task_draft", _boom)
        result = classify_intent(conversation, actor=alice, content="remind me to call mom")
        assert result == {"intent": Message.Intent.CHAT, "intent_payload": {}}

    def test_client_local_date_threads_into_the_reference_date_instruction(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        _classified_as_task_draft(monkeypatch)
        from ai_core.gateway import InferenceGateway

        captured = {}
        original_complete = InferenceGateway.complete

        def _capture(self, purpose, messages, **kwargs):
            if purpose == "router.task_draft":
                captured["messages"] = messages
            return original_complete(self, purpose, messages, **kwargs)

        monkeypatch.setattr(InferenceGateway, "complete", _capture)
        classify_intent(
            conversation,
            actor=alice,
            content="remind me tomorrow",
            client_local_date="2026-07-10",
        )
        system_content = captured["messages"][0]["content"]
        assert "2026-07-10" in system_content

    def test_missing_client_local_date_uses_absolute_only_fallback_instruction(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        _classified_as_task_draft(monkeypatch)
        from ai_core.gateway import InferenceGateway

        captured = {}
        original_complete = InferenceGateway.complete

        def _capture(self, purpose, messages, **kwargs):
            if purpose == "router.task_draft":
                captured["messages"] = messages
            return original_complete(self, purpose, messages, **kwargs)

        monkeypatch.setattr(InferenceGateway, "complete", _capture)
        classify_intent(conversation, actor=alice, content="remind me tomorrow")
        system_content = captured["messages"][0]["content"]
        assert "No reference date is available" in system_content

    def test_malformed_client_local_date_uses_absolute_only_fallback_instruction(
        self,
        conversation,
        alice,
        fake_provider,
        router_intent_prompt,
        task_draft_prompt,
        monkeypatch,
    ):
        """A malformed client_local_date (never rejected at the serializer
        level -- see assistant/serializers.py) must degrade exactly like a
        missing one, never guess against server time."""
        _classified_as_task_draft(monkeypatch)
        from ai_core.gateway import InferenceGateway

        captured = {}
        original_complete = InferenceGateway.complete

        def _capture(self, purpose, messages, **kwargs):
            if purpose == "router.task_draft":
                captured["messages"] = messages
            return original_complete(self, purpose, messages, **kwargs)

        monkeypatch.setattr(InferenceGateway, "complete", _capture)
        classify_intent(
            conversation, actor=alice, content="remind me tomorrow", client_local_date="not-a-date"
        )
        system_content = captured["messages"][0]["content"]
        assert "No reference date is available" in system_content
