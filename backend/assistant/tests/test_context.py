import pytest
from django.core.exceptions import ImproperlyConfigured

from accounts.services import register_user
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate, PromptVersion
from assistant.context import build_context
from assistant.models import Conversation, Message
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


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
def system_prompt():
    template = PromptTemplate.objects.create(name="jarvis_system", purpose="chat")
    version = PromptVersion.objects.create(template=template, version=1, content="You are Jarvis.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def test_system_prompt_is_first_message(conversation, alice, system_prompt):
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="Merhaba")
    assert messages[0] == {"role": "system", "content": "You are Jarvis."}


def test_build_context_returns_the_resolved_prompt_version(conversation, alice, system_prompt):
    _, _, prompt_version, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    assert prompt_version == system_prompt


def test_history_is_chronological(conversation, alice, system_prompt):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="first",
        created_by=alice,
    )
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="second",
    )
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="third")
    assert [m["content"] for m in messages[1:3]] == ["first", "second"]


def test_new_user_content_appended_exactly_once(conversation, alice, system_prompt):
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="hello")
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert sum(1 for m in messages if m["content"] == "hello") == 1


def test_partial_and_failed_messages_excluded(conversation, alice, system_prompt):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="partial-one",
        status=Message.Status.PARTIAL,
    )
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="failed-one",
        status=Message.Status.FAILED,
    )
    messages, report, _, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    contents = [m["content"] for m in messages]
    assert "partial-one" not in contents
    assert "failed-one" not in contents
    assert report["history_message_count"] == 0


def test_other_conversations_and_workspaces_excluded(conversation, workspace, alice, system_prompt):
    other_conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
    Message.objects.create(
        workspace=workspace,
        conversation=other_conversation,
        role=Message.Role.USER,
        content="other-conv",
    )
    other_workspace = Workspace.objects.create(name="Other", owner=alice)
    other_ws_conversation = Conversation.objects.create(workspace=other_workspace, created_by=alice)
    Message.objects.create(
        workspace=other_workspace,
        conversation=other_ws_conversation,
        role=Message.Role.USER,
        content="other-ws",
    )
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    contents = [m["content"] for m in messages]
    assert "other-conv" not in contents
    assert "other-ws" not in contents


def test_message_with_mismatched_workspace_is_excluded(
    conversation, workspace, alice, system_prompt
):
    """Defense-in-depth: application code always copies workspace from
    conversation.workspace, but nothing at the DB level enforces that
    invariant (e.g. a stray admin edit) — the context builder must not trust
    the conversation FK alone."""
    other_workspace = Workspace.objects.create(name="Other", owner=alice)
    Message.objects.create(
        workspace=other_workspace,  # mismatched on purpose
        conversation=conversation,
        role=Message.Role.USER,
        content="mismatched-workspace",
    )
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    contents = [m["content"] for m in messages]
    assert "mismatched-workspace" not in contents


def test_unicode_content_preserved(conversation, alice, system_prompt):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="Merhaba dünya çşğıöü 🚀",
    )
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="devam")
    assert "Merhaba dünya çşğıöü 🚀" in [m["content"] for m in messages]


def test_report_counts_and_characters_are_exact(conversation, alice, system_prompt):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="abc",
    )
    messages, report, _, _, _ = build_context(conversation, actor=alice, new_user_content="de")
    assert report["context_builder"] == "v1"
    assert report["history_message_count"] == 1
    assert report["total_message_count"] == len(messages) == 3
    assert report["total_characters"] == sum(len(m["content"]) for m in messages)
    assert report["truncated"] is False


def test_missing_prompt_template_fails_clearly(conversation, alice):
    with pytest.raises(ImproperlyConfigured):
        build_context(conversation, actor=alice, new_user_content="hi")


def test_missing_active_version_fails_clearly(conversation, alice):
    PromptTemplate.objects.create(name="jarvis_system", purpose="chat")
    with pytest.raises(ImproperlyConfigured):
        build_context(conversation, actor=alice, new_user_content="hi")


def test_builder_makes_no_llm_call(conversation, alice, system_prompt, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("context builder must not call the gateway")

    monkeypatch.setattr(InferenceGateway, "complete", boom)
    build_context(conversation, actor=alice, new_user_content="hi")


# --- memory injection (context builder v1, MVP 2 §2) ---

from memory.models import Memory, compute_fingerprint  # noqa: E402


def _memory(workspace, content, **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.PREFERENCE,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.USER_CREATED,
        "title": "Note",
        "content": content,
        "fingerprint": compute_fingerprint(content),
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


def test_approved_memory_is_injected_into_system_message_only(
    conversation, workspace, alice, system_prompt
):
    row = _memory(workspace, "Drinks coffee black.")
    messages, report, _, memory_ids, _ = build_context(
        conversation, actor=alice, new_user_content="hi"
    )
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("You are Jarvis.")
    assert "Drinks coffee black." in messages[0]["content"]
    assert all("Drinks coffee black." not in m["content"] for m in messages[1:])
    assert memory_ids == [str(row.id)]
    assert report["memory_ids"] == [str(row.id)]
    assert report["memory_count"] == 1


def test_private_memory_never_reaches_the_gateway_payload(
    conversation, workspace, alice, system_prompt
):
    """The exact-payload negative contract: Memory-sourced private
    title/content appear in NO message of the payload the gateway will
    receive (`build_gateway_stream` passes this list verbatim)."""
    _memory(
        workspace,
        "Extremely private fact.",
        title="Private title",
        sensitivity=Memory.Sensitivity.PRIVATE,
    )
    messages, report, _, memory_ids, _ = build_context(
        conversation, actor=alice, new_user_content="hi"
    )
    payload = "\n".join(m["content"] for m in messages)
    assert "Extremely private fact." not in payload
    assert "Private title" not in payload
    assert memory_ids == []
    assert report["memory_ids"] == []


def test_non_approved_memories_are_not_injected(conversation, workspace, alice, system_prompt):
    _memory(workspace, "pending suggestion", status=Memory.Status.PENDING_APPROVAL)
    _memory(workspace, "archived note", status=Memory.Status.ARCHIVED)
    _memory(workspace, "rejected note", status=Memory.Status.REJECTED)
    messages, _, _, memory_ids, _ = build_context(conversation, actor=alice, new_user_content="hi")
    payload = "\n".join(m["content"] for m in messages)
    for text in ("pending suggestion", "archived note", "rejected note"):
        assert text not in payload
    assert memory_ids == []


def test_memories_of_other_workspaces_are_not_injected(
    conversation, workspace, alice, system_prompt
):
    """No personal overlay / no cross-workspace leak: only
    conversation.workspace memories qualify."""
    other_workspace = Workspace.objects.create(name="Other", owner=alice)
    _memory(other_workspace, "memory from elsewhere")
    messages, _, _, memory_ids, _ = build_context(conversation, actor=alice, new_user_content="hi")
    assert "memory from elsewhere" not in "\n".join(m["content"] for m in messages)
    assert memory_ids == []


def test_without_memories_system_prompt_is_untouched(conversation, alice, system_prompt):
    messages, _, _, memory_ids, _ = build_context(conversation, actor=alice, new_user_content="hi")
    assert messages[0]["content"] == "You are Jarvis."
    assert memory_ids == []


def test_context_access_audit_fires_from_build(conversation, workspace, alice, system_prompt):
    from audit.models import AuditLog

    row = _memory(workspace, "audited via build")
    build_context(conversation, actor=alice, new_user_content="hi")
    entry = AuditLog.objects.get(action="memory.context_accessed")
    assert entry.metadata["memory_ids"] == [str(row.id)]


def test_no_personal_overlay_for_dual_membership_actor(alice, workspace, system_prompt):
    """Codex finding: the isolation test must use an actor who CAN access
    both workspaces — a personal memory must not leak into a company
    conversation even when the actor is a member of both."""
    from workspaces.models import WorkspaceMember

    company = Workspace.objects.create(name="Acme", owner=alice, type=Workspace.Type.COMPANY)
    WorkspaceMember.objects.create(workspace=company, user=alice, role=WorkspaceMember.Role.OWNER)
    _memory(workspace, "personal preference: dark humor")  # personal workspace
    company_conversation = Conversation.objects.create(workspace=company, created_by=alice)

    messages, _, _, memory_ids, _ = build_context(
        company_conversation, actor=alice, new_user_content="hi"
    )
    payload = "\n".join(m["content"] for m in messages)
    assert "personal preference: dark humor" not in payload
    assert memory_ids == []


def test_private_memory_absent_from_the_payload_the_gateway_receives(
    conversation, workspace, alice, system_prompt, monkeypatch
):
    """Capture at the InferenceGateway.complete boundary itself (not just
    build_context's return): the messages kwarg the gateway is handed must
    not contain Memory-sourced private title/content."""
    from assistant.services import build_gateway_stream

    _memory(
        workspace,
        "Extremely private fact.",
        title="Private title",
        sensitivity=Memory.Sensitivity.PRIVATE,
    )
    visible = _memory(workspace, "Public fact.")

    captured = {}

    def fake_complete(self, **kwargs):
        captured.update(kwargs)

        def empty_stream():
            yield "ok"

        return empty_stream()

    monkeypatch.setattr(InferenceGateway, "complete", fake_complete)
    _, _, memory_ids, _ = build_gateway_stream(conversation, actor=alice, content="hi")

    payload = "\n".join(m["content"] for m in captured["messages"])
    assert "Extremely private fact." not in payload
    assert "Private title" not in payload
    assert "Public fact." in payload
    assert memory_ids == [str(visible.id)]


# --- knowledge chunk injection (context builder v1, MVP 3 branch 4) ---

from ai_core.adapters.fake import embed_text  # noqa: E402
from ai_core.models import ModelProvider  # noqa: E402
from ai_core.tests.test_fake_adapter import SEED_DOCUMENT_TEXT  # noqa: E402
from knowledge.models import KnowledgeChunk, KnowledgeDocument  # noqa: E402


@pytest.fixture
def embedding_provider(settings):
    settings.AI_FAKE_PROVIDER = True
    return ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )


def _ready_chunk(workspace, content, *, provider, sensitivity=KnowledgeDocument.Sensitivity.NORMAL):
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title="Doc",
        status=KnowledgeDocument.Status.READY,
        sensitivity=sensitivity,
        raw_text=content,
        content_hash=f"hash-{content[:16]}",
    )
    return KnowledgeChunk.objects.create(
        workspace=workspace,
        document=document,
        ordinal=0,
        content=content,
        embedding=embed_text(content),
        embedding_provider=provider,
    )


def test_ready_chunk_is_injected_into_system_message_only(
    conversation, workspace, alice, system_prompt, embedding_provider
):
    from ai_core.tests.test_fake_adapter import QUERY_TEXT

    chunk = _ready_chunk(workspace, SEED_DOCUMENT_TEXT, provider=embedding_provider)
    messages, report, _, _, chunk_ids = build_context(
        conversation, actor=alice, new_user_content=QUERY_TEXT
    )
    assert messages[0]["role"] == "system"
    assert SEED_DOCUMENT_TEXT in messages[0]["content"]
    assert all(SEED_DOCUMENT_TEXT not in m["content"] for m in messages[1:])
    assert chunk_ids == [str(chunk.id)]
    assert report["chunk_ids"] == [str(chunk.id)]
    assert report["chunk_count"] == 1


def test_private_document_chunk_never_reaches_the_gateway_payload(
    conversation, workspace, alice, system_prompt, embedding_provider, monkeypatch
):
    """Exact-payload negative contract for knowledge, mirroring the memory
    one above: a private document's chunk must appear in NO message of the
    payload the gateway will actually receive."""
    from ai_core.tests.test_fake_adapter import QUERY_TEXT
    from assistant.services import build_gateway_stream

    _ready_chunk(
        workspace,
        "Extremely private lantern schematics.",
        provider=embedding_provider,
        sensitivity=KnowledgeDocument.Sensitivity.PRIVATE,
    )
    visible = _ready_chunk(workspace, SEED_DOCUMENT_TEXT, provider=embedding_provider)

    captured = {}

    def fake_complete(self, **kwargs):
        captured.update(kwargs)

        def empty_stream():
            yield "ok"

        return empty_stream()

    monkeypatch.setattr(InferenceGateway, "complete", fake_complete)
    _, _, _, chunk_ids = build_gateway_stream(conversation, actor=alice, content=QUERY_TEXT)

    payload = "\n".join(m["content"] for m in captured["messages"])
    assert "Extremely private lantern schematics." not in payload
    assert SEED_DOCUMENT_TEXT in payload
    assert chunk_ids == [str(visible.id)]


def test_without_knowledge_chunks_system_prompt_is_untouched(conversation, alice, system_prompt):
    messages, _, _, _, chunk_ids = build_context(conversation, actor=alice, new_user_content="hi")
    assert messages[0]["content"] == "You are Jarvis."
    assert chunk_ids == []


def test_no_personal_overlay_for_dual_membership_actor_knowledge(
    alice, workspace, system_prompt, embedding_provider
):
    """Knowledge-side sibling of the memory dual-membership test above: the
    isolation test must use an actor who CAN access both workspaces — a
    personal-workspace knowledge chunk must not leak into a company
    conversation even when the actor is a member of both."""
    from ai_core.tests.test_fake_adapter import QUERY_TEXT
    from workspaces.models import WorkspaceMember

    company = Workspace.objects.create(name="Acme", owner=alice, type=Workspace.Type.COMPANY)
    WorkspaceMember.objects.create(workspace=company, user=alice, role=WorkspaceMember.Role.OWNER)
    _ready_chunk(workspace, SEED_DOCUMENT_TEXT, provider=embedding_provider)  # personal workspace
    company_conversation = Conversation.objects.create(workspace=company, created_by=alice)

    messages, _, _, _, chunk_ids = build_context(
        company_conversation, actor=alice, new_user_content=QUERY_TEXT
    )
    payload = "\n".join(m["content"] for m in messages)
    assert SEED_DOCUMENT_TEXT not in payload
    assert chunk_ids == []


# --- intent-aware framing (MVP 6 §2) ---


def test_context_report_records_intent_default_chat(conversation, alice, system_prompt):
    _, report, _, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    assert report["intent"] == Message.Intent.CHAT


def test_context_report_records_the_passed_intent(conversation, alice, system_prompt):
    _, report, _, _, _ = build_context(
        conversation, actor=alice, new_user_content="hi", intent=Message.Intent.TASK_DRAFT
    )
    assert report["intent"] == Message.Intent.TASK_DRAFT


def test_knowledge_query_intent_adds_framing_instruction(conversation, alice, system_prompt):
    messages, _, _, _, _ = build_context(
        conversation,
        actor=alice,
        new_user_content="hi",
        intent=Message.Intent.KNOWLEDGE_QUERY,
    )
    assert "specifically asking about their knowledge base" in messages[0]["content"]
    assert "remembered/curated information" not in messages[0]["content"]


def test_memory_query_intent_adds_framing_instruction(conversation, alice, system_prompt):
    messages, _, _, _, _ = build_context(
        conversation,
        actor=alice,
        new_user_content="hi",
        intent=Message.Intent.MEMORY_QUERY,
    )
    assert "remembered/curated information" in messages[0]["content"]
    assert "specifically asking about their knowledge base" not in messages[0]["content"]


def test_chat_intent_adds_no_framing_instruction(conversation, alice, system_prompt):
    messages, _, _, _, _ = build_context(conversation, actor=alice, new_user_content="hi")
    assert messages[0]["content"] == "You are Jarvis."


def test_task_draft_and_tool_request_intents_add_no_framing_instruction(
    conversation, alice, system_prompt
):
    """§2 only defines framing for memory_query/knowledge_query — every other
    intent (including the ones §3/§4 will later dispatch on) must leave the
    system prompt exactly as today."""
    for intent in (Message.Intent.TASK_DRAFT, Message.Intent.TOOL_REQUEST):
        messages, _, _, _, _ = build_context(
            conversation, actor=alice, new_user_content="hi", intent=intent
        )
        assert messages[0]["content"] == "You are Jarvis."


def test_knowledge_query_widens_the_retrieval_budget(
    conversation, workspace, alice, system_prompt, embedding_provider
):
    """MVP 6 §2: a knowledge_query turn must select MORE than the default
    MAX_CONTEXT_CHUNKS when enough eligible chunks exist — proves the
    widened window actually reaches select_context_chunks, not just that
    the parameter exists."""
    from knowledge.context import MAX_CONTEXT_CHUNKS

    for i in range(MAX_CONTEXT_CHUNKS + 5):
        # `_ready_chunk`'s content_hash is derived from the first 16 chars —
        # the variant index must lead the string, not trail it, or every
        # variant collides on the shared SEED_DOCUMENT_TEXT prefix and hits
        # the (workspace, content_hash) unique constraint.
        _ready_chunk(workspace, f"variant {i} {SEED_DOCUMENT_TEXT}", provider=embedding_provider)

    from ai_core.tests.test_fake_adapter import QUERY_TEXT

    _, _, _, _, chat_chunk_ids = build_context(
        conversation, actor=alice, new_user_content=QUERY_TEXT
    )
    _, _, _, _, widened_chunk_ids = build_context(
        conversation,
        actor=alice,
        new_user_content=QUERY_TEXT,
        intent=Message.Intent.KNOWLEDGE_QUERY,
    )
    assert len(chat_chunk_ids) == MAX_CONTEXT_CHUNKS
    assert len(widened_chunk_ids) == MAX_CONTEXT_CHUNKS + 5
