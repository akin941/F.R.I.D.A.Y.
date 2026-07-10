import json

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.models import User
from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from audit.models import AuditLog
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


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
    """Every POST that reaches classify_intent() needs this configured,
    exactly like `system_prompt` above — mirrors seed_ai_defaults' real
    jarvis_router_intent entry (MVP 6 §1)."""
    template = PromptTemplate.objects.create(name="jarvis_router_intent", purpose="router.intent")
    version = PromptVersion.objects.create(
        template=template, version=1, content="Classify the message."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def task_draft_prompt():
    """Needed only by task_draft-classified POSTs (MVP 6 §3) -- mirrors
    seed_ai_defaults' real jarvis_router_task_draft entry."""
    template = PromptTemplate.objects.create(
        name="jarvis_router_task_draft", purpose="router.task_draft"
    )
    version = PromptVersion.objects.create(
        template=template, version=1, content="Extract the task."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def make_member(workspace, email, role):
    user = User.objects.create_user(email=email, password=PASSWORD, name=email.split("@")[0])
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _parse_sse(response):
    body = b"".join(response.streaming_content)
    events = []
    for chunk in body.split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return events


@pytest.fixture(params=ROLES)
def member_user(request, workspace, owner):
    role = request.param
    if role == WorkspaceMember.Role.OWNER:
        return owner
    return make_member(workspace, f"{role}@test.dev", role)


# --- role x endpoint matrix: every role currently allows every operation ---


def test_role_can_list_conversations(member_user, workspace, owner):
    Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(member_user).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 200
    assert len(response.data) == 1


def test_role_can_create_conversation(member_user, workspace):
    response = auth_client(member_user).post(
        "/api/v1/ai/conversations",
        {"workspace": str(workspace.id), "title": "Hi"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["title"] == "Hi"

    entry = AuditLog.objects.get(action="assistant.conversation.created")
    assert entry.actor == member_user
    assert entry.workspace == workspace
    assert entry.resource_type == "assistant.conversation"
    assert entry.resource_id == response.data["id"]
    assert entry.metadata == {}


def test_role_can_list_messages(member_user, workspace, owner):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=owner,
    )
    response = auth_client(member_user).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    assert response.status_code == 200
    assert len(response.data) == 1


def test_role_can_send_message(
    member_user, workspace, owner, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(member_user).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    assert events[-1]["type"] == "done"


# --- auth ---


def test_unauthenticated_requests_return_401(workspace):
    client = APIClient()
    conversation = Conversation.objects.create(workspace=workspace)
    assert client.get(f"/api/v1/ai/conversations?workspace={workspace.id}").status_code == 401
    assert (
        client.post(
            "/api/v1/ai/conversations", {"workspace": str(workspace.id)}, format="json"
        ).status_code
        == 401
    )
    assert client.get(f"/api/v1/ai/conversations/{conversation.id}/messages").status_code == 401
    assert (
        client.post(
            f"/api/v1/ai/conversations/{conversation.id}/messages",
            {"content": "hi"},
            format="json",
        ).status_code
        == 401
    )


# --- workspace scoping and validation ---


def test_create_conversation_in_inaccessible_workspace_returns_404(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    response = auth_client(owner).post(
        "/api/v1/ai/conversations", {"workspace": str(other_workspace.id)}, format="json"
    )
    assert response.status_code == 404


def test_list_conversations_requires_workspace_param(owner):
    assert auth_client(owner).get("/api/v1/ai/conversations").status_code == 400


def test_list_conversations_malformed_workspace_uuid_returns_400(owner):
    response = auth_client(owner).get("/api/v1/ai/conversations?workspace=not-a-uuid")
    assert response.status_code == 400


def test_list_conversations_inaccessible_valid_workspace_returns_empty(owner):
    stranger = register_user(email="stranger2@test.dev", password=PASSWORD, name="Stranger2")
    other_workspace = Workspace.objects.get(owner=stranger)
    response = auth_client(owner).get(f"/api/v1/ai/conversations?workspace={other_workspace.id}")
    assert response.status_code == 200
    assert response.data == []


def test_cross_workspace_message_list_returns_404(owner):
    stranger = register_user(email="stranger3@test.dev", password=PASSWORD, name="Stranger3")
    other_workspace = Workspace.objects.get(owner=stranger)
    conversation = Conversation.objects.create(workspace=other_workspace, created_by=stranger)
    response = auth_client(owner).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    assert response.status_code == 404


def test_cross_workspace_message_post_returns_404(owner):
    stranger = register_user(email="stranger4@test.dev", password=PASSWORD, name="Stranger4")
    other_workspace = Workspace.objects.get(owner=stranger)
    conversation = Conversation.objects.create(workspace=other_workspace, created_by=stranger)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 404
    assert Message.objects.filter(conversation=conversation).count() == 0


def test_empty_and_whitespace_content_returns_400_and_creates_nothing(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    for content in ["", "   "]:
        response = auth_client(owner).post(
            f"/api/v1/ai/conversations/{conversation.id}/messages",
            {"content": content},
            format="json",
        )
        assert response.status_code == 400
    assert Message.objects.filter(conversation=conversation).count() == 0
    assert ModelUsage.objects.count() == 0


def test_meaningful_whitespace_in_content_is_preserved(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    content = "  def f():\n    return 1\n"
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": content},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.content == content


# --- ordering ---


def test_conversation_ordering_is_descending(owner, workspace):
    first = Conversation.objects.create(workspace=workspace, created_by=owner, title="first")
    second = Conversation.objects.create(workspace=workspace, created_by=owner, title="second")
    response = auth_client(owner).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    ids = [c["id"] for c in response.data]
    assert ids == [str(second.id), str(first.id)]


def test_message_ordering_is_ascending(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    first = Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="a",
        created_by=owner,
    )
    second = Message.objects.create(
        workspace=workspace, conversation=conversation, role=Message.Role.ASSISTANT, content="b"
    )
    response = auth_client(owner).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    ids = [m["id"] for m in response.data]
    assert ids == [str(first.id), str(second.id)]


# --- SSE contract over HTTP ---


def test_user_message_persisted_before_sse_consumption(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hello"},
        format="json",
    )
    assert Message.objects.filter(
        conversation=conversation, role=Message.Role.USER, content="hello"
    ).exists()
    assert not Message.objects.filter(
        conversation=conversation, role=Message.Role.ASSISTANT
    ).exists()
    list(response.streaming_content)
    assert Message.objects.filter(conversation=conversation, role=Message.Role.ASSISTANT).exists()


def test_response_headers_and_content_type_exact(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    assert response["Cache-Control"] == "no-cache"
    assert response["X-Accel-Buffering"] == "no"
    list(response.streaming_content)


def test_accept_event_stream_header_is_not_rejected(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    """Regression: a standards-compliant SSE client sends
    `Accept: text/event-stream`. DRF's content negotiation must not 406 that
    before the view ever runs."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
        HTTP_ACCEPT="text/event-stream",
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    list(response.streaming_content)


def test_accept_event_stream_header_still_returns_json_errors(owner, workspace):
    """Same Accept header, but a request that ends in a plain DRF Response
    (400) — must still come back as JSON, not raw dict-as-bytes."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "   "},
        format="json",
        HTTP_ACCEPT="text/event-stream",
    )
    assert response.status_code == 400
    assert response["Content-Type"] == "application/json"


def test_user_message_not_echoed_via_sse(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hello-unique-marker"},
        format="json",
    )
    events = _parse_sse(response)
    for event in events:
        assert event.get("text") != "hello-unique-marker"


def test_full_fake_gateway_flow_creates_one_usage_linked(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    events = _parse_sse(response)
    assert events[-1]["type"] == "done"
    # fake_provider is chat-only; the knowledge-retrieval embed() call still
    # writes its own (no_provider) ModelUsage row, so two rows exist total.
    assert ModelUsage.objects.filter(purpose="chat").count() == 1
    assistant_message = Message.objects.get(role=Message.Role.ASSISTANT)
    assert assistant_message.model_usage == ModelUsage.objects.get(purpose="chat")


# --- throttling: only the costly POST path is rate-limited ---


def test_chat_post_is_throttled_after_configured_limit(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, monkeypatch
):
    # ScopedRateThrottle.THROTTLE_RATES is a plain class attribute snapshotted
    # from api_settings at import time, so overriding settings.REST_FRAMEWORK
    # at test-runtime has no effect on it — patch the class attribute instead.
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"chat": "2/min"})
    cache.clear()
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    client = auth_client(owner)
    for _ in range(2):
        response = client.post(
            f"/api/v1/ai/conversations/{conversation.id}/messages",
            {"content": "hi"},
            format="json",
        )
        assert response.status_code == 200
        list(response.streaming_content)
    third = client.post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert third.status_code == 429


def test_chat_get_is_not_throttled(owner, workspace, monkeypatch):
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"chat": "1/min"})
    cache.clear()
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    client = auth_client(owner)
    for _ in range(5):
        response = client.get(f"/api/v1/ai/conversations/{conversation.id}/messages")
        assert response.status_code == 200


# --- memories_used contract (MVP 2 §2) ---


def test_memories_used_in_done_event_and_persisted(
    member_user, workspace, fake_provider, system_prompt, router_intent_prompt
):
    from memory.models import Memory, compute_fingerprint

    memory_row = Memory.objects.create(
        workspace=workspace,
        memory_type=Memory.Type.PREFERENCE,
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        title="Coffee",
        content="Drinks coffee black.",
        fingerprint=compute_fingerprint("Drinks coffee black."),
    )
    conversation = Conversation.objects.create(workspace=workspace, created_by=member_user)
    response = auth_client(member_user).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    done = events[-1]
    assert done["type"] == "done"
    assert done["memories_used"] == [str(memory_row.id)]

    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.memories_used == [str(memory_row.id)]

    listing = auth_client(member_user).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    serialized = [m for m in listing.data if m["role"] == "assistant"][0]
    assert serialized["memories_used"] == [str(memory_row.id)]


def test_memories_used_defaults_to_empty(
    member_user, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=member_user)
    response = auth_client(member_user).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    events = _parse_sse(response)
    assert events[-1]["memories_used"] == []
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.memories_used == []


# --- chunks_used contract (MVP 3 branch 4) ---


def test_chunks_used_in_done_event_and_persisted(
    member_user, workspace, fake_provider, system_prompt, router_intent_prompt
):
    from ai_core.adapters.fake import embed_text
    from ai_core.tests.test_fake_adapter import QUERY_TEXT, SEED_DOCUMENT_TEXT
    from knowledge.models import KnowledgeChunk, KnowledgeDocument

    embedding_provider = ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title="Lantern mode",
        status=KnowledgeDocument.Status.READY,
        raw_text=SEED_DOCUMENT_TEXT,
        content_hash="hash-seed",
    )
    chunk = KnowledgeChunk.objects.create(
        workspace=workspace,
        document=document,
        ordinal=0,
        content=SEED_DOCUMENT_TEXT,
        embedding=embed_text(SEED_DOCUMENT_TEXT),
        embedding_provider=embedding_provider,
    )

    conversation = Conversation.objects.create(workspace=workspace, created_by=member_user)
    response = auth_client(member_user).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": QUERY_TEXT},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    done = events[-1]
    assert done["type"] == "done"
    assert done["chunks_used"] == [str(chunk.id)]

    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.chunks_used == [str(chunk.id)]

    listing = auth_client(member_user).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    serialized = [m for m in listing.data if m["role"] == "assistant"][0]
    assert serialized["chunks_used"] == [str(chunk.id)]


def test_chunks_used_defaults_to_empty(
    member_user, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=member_user)
    response = auth_client(member_user).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    events = _parse_sse(response)
    assert events[-1]["chunks_used"] == []
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.chunks_used == []


# --- intent/intent_payload contract (MVP 6 §1) ---


def test_user_message_classified_as_chat_by_default(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    """FakeAdapter's router.intent fixture always classifies as chat — the
    plumbing end-to-end proof that classification runs and is persisted."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.CHAT
    assert user_message.intent_payload == {}

    listing = auth_client(owner).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    serialized = [m for m in listing.data if m["role"] == "user"][0]
    assert serialized["intent"] == Message.Intent.CHAT
    assert serialized["intent_payload"] == {}


def test_assistant_message_never_gets_a_classified_intent(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    """Classification interprets the user's own message — it must never be
    attached to the assistant's reply row."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    list(response.streaming_content)
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert assistant_message.intent == ""
    assert assistant_message.intent_payload == {}


def test_classifier_gateway_error_degrades_to_chat_without_blocking_reply(
    owner, workspace, system_prompt, router_intent_prompt
):
    """No ModelProvider registered at all -> GatewayError('no_provider') for
    the classify call. The user message must still be created (classified as
    plain chat), and the request must not blow up — the underlying chat
    reply path (which also has no provider) still degrades independently via
    its own existing error-event contract."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    assert events[-1]["type"] == "error"
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.CHAT
    assert user_message.intent_payload == {}


def test_malformed_classifier_output_degrades_to_chat_at_view_level(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, monkeypatch
):
    import assistant.services as services_module

    def _boom(raw_text):
        raise services_module.IntentParseError("boom")

    monkeypatch.setattr(services_module, "parse_intent", _boom)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.CHAT
    assert user_message.intent_payload == {}


def test_tool_request_intent_persists_tool_name_in_payload(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, monkeypatch
):
    import assistant.services as services_module

    def _tool_request(raw_text):
        return {"intent": Message.Intent.TOOL_REQUEST, "tool_name": "vault_export"}

    monkeypatch.setattr(services_module, "parse_intent", _tool_request)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "export my vault"},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.TOOL_REQUEST
    assert user_message.intent_payload == {"tool_name": "vault_export", "payload": {}}


def test_tool_request_emits_draft_event_before_done_and_creates_no_approval_request(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, monkeypatch
):
    """MVP 6 §4: classification alone must never call
    approvals.services.create_request -- this is the regression test for
    that HIGH finding from plan-review, never to reopen."""
    import assistant.services as services_module
    from approvals.models import ApprovalRequest

    def _tool_request(raw_text):
        return {"intent": Message.Intent.TOOL_REQUEST, "tool_name": "vault_export"}

    monkeypatch.setattr(services_module, "parse_intent", _tool_request)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "export my vault"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    types = [e["type"] for e in events]
    assert types[-2:] == ["draft", "done"]

    draft = events[-2]
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert draft["kind"] == "tool"
    assert draft["source_message_id"] == str(user_message.id)
    assert draft["tool_name"] == "vault_export"
    assert draft["payload"] == {}
    assert ApprovalRequest.objects.count() == 0


def test_unregistered_tool_request_degrades_to_chat_at_view_level(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, monkeypatch
):
    import assistant.services as services_module

    def _unknown_tool(raw_text):
        return {"intent": Message.Intent.TOOL_REQUEST, "tool_name": "definitely_not_a_tool"}

    monkeypatch.setattr(services_module, "parse_intent", _unknown_tool)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "do the thing"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    assert all(e["type"] != "draft" for e in events)
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.CHAT
    assert user_message.intent_payload == {}


def test_router_intent_purpose_records_its_own_model_usage_row(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    """ADR-003: no gateway call skips ModelUsage — router.intent joins chat
    as a second, independently-recorded purpose for the same turn."""
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    list(response.streaming_content)
    assert ModelUsage.objects.filter(purpose="router.intent").count() == 1
    assert ModelUsage.objects.filter(purpose="chat").count() == 1


# --- task_draft: draft SSE event + client_local_date (MVP 6 §3) ---


def _classified_as_task_draft(monkeypatch):
    import assistant.services as services_module

    def _task_draft(raw_text):
        return {"intent": Message.Intent.TASK_DRAFT, "tool_name": None}

    monkeypatch.setattr(services_module, "parse_intent", _task_draft)


def test_task_draft_emits_draft_event_before_done_with_source_message_id(
    owner,
    workspace,
    fake_provider,
    system_prompt,
    router_intent_prompt,
    task_draft_prompt,
    monkeypatch,
):
    _classified_as_task_draft(monkeypatch)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "remind me to call mom", "client_local_date": "2026-07-10"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    types = [e["type"] for e in events]
    assert types[-2:] == ["draft", "done"]

    draft = events[-2]
    user_message = Message.objects.get(conversation=conversation, role=Message.Role.USER)
    assert draft["kind"] == "task"
    assert draft["source_message_id"] == str(user_message.id)
    assert draft["title"] == "Fake extracted task"
    assert draft["due_date"] is None


def test_chat_intent_never_emits_a_draft_event(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    events = _parse_sse(response)
    assert events[-1]["type"] == "done"
    assert all(e["type"] != "draft" for e in events)


def test_client_local_date_is_optional(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi"},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)


def test_malformed_client_local_date_does_not_400(
    owner,
    workspace,
    fake_provider,
    system_prompt,
    router_intent_prompt,
    task_draft_prompt,
    monkeypatch,
):
    """A malformed client_local_date must degrade gracefully (extraction
    falls back to absolute-only resolution), never reject the whole POST."""
    _classified_as_task_draft(monkeypatch)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "remind me tomorrow", "client_local_date": "not-a-date"},
        format="json",
    )
    assert response.status_code == 200
    list(response.streaming_content)
