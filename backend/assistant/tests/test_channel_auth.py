"""PAT/CLI-channel behavior on the chat endpoints (MVP 9 §1/§2):
scoped_for_actor() workspace fixation and Message.channel audit stamping."""

import json

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from core.channels import CHANNEL_CLI
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


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
def other_workspace(owner):
    """A second workspace the same user is a member of, distinct from the
    PAT's bound workspace -- proves fixation isn't just "any workspace the
    underlying user belongs to"."""
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    ws = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=ws, user=owner, role=WorkspaceMember.Role.MEMBER)
    return ws


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
    template = PromptTemplate.objects.create(name="friday_system", purpose="chat")
    version = PromptVersion.objects.create(template=template, version=1, content="You are Friday.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def router_intent_prompt():
    template = PromptTemplate.objects.create(name="friday_router_intent", purpose="router.intent")
    version = PromptVersion.objects.create(
        template=template, version=1, content="Classify the message."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def pat(owner, workspace):
    raw = "jpat_test-raw-token-0000000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="test cli", token_hash=hash_token(raw)
    )
    return raw


def pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


def _parse_sse(response):
    body = b"".join(response.streaming_content)
    events = []
    for chunk in body.split(b"\n\n"):
        if chunk.startswith(b"data: "):
            events.append(json.loads(chunk[len(b"data: ") :]))
    return events


def test_pat_create_conversation_without_workspace_body_field_succeeds(pat, workspace):
    response = pat_client(pat).post("/api/v1/ai/conversations", {}, format="json")
    assert response.status_code == 201
    assert str(response.data["workspace"]) == str(workspace.id)


def test_pat_create_conversation_via_form_encoded_body_does_not_500(pat, workspace):
    """DRF exposes `request.data` as an immutable QueryDict for
    form-encoded requests -- proves the view copies before injecting the
    bound workspace rather than mutating it in place (Codex review, branch
    1 round 1)."""
    response = pat_client(pat).post("/api/v1/ai/conversations", {}, format="multipart")
    assert response.status_code == 201
    assert str(response.data["workspace"]) == str(workspace.id)


def test_pat_create_conversation_with_non_object_json_body_is_400_not_500(pat):
    """A syntactically valid but non-object JSON body (e.g. a bare list)
    has nothing sensible to inject a workspace into -- must fall through to
    the serializer's own 400, not raise TypeError trying to subscript a
    list (Codex review, branch 1 round 2)."""
    response = pat_client(pat).post("/api/v1/ai/conversations", [], format="json")
    assert response.status_code == 400


def test_pat_create_conversation_with_mismatched_body_workspace_is_forced_to_bound(
    pat, workspace, other_workspace
):
    response = pat_client(pat).post(
        "/api/v1/ai/conversations", {"workspace": str(other_workspace.id)}, format="json"
    )
    assert response.status_code == 201
    assert str(response.data["workspace"]) == str(workspace.id)


def test_pat_list_conversations_without_query_param_succeeds_scoped_to_bound_workspace(
    pat, owner, workspace, other_workspace
):
    Conversation.objects.create(workspace=workspace, created_by=owner)
    Conversation.objects.create(workspace=other_workspace, created_by=owner)

    response = pat_client(pat).get("/api/v1/ai/conversations")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert str(response.data[0]["workspace"]) == str(workspace.id)


def test_pat_list_conversations_with_mismatched_query_workspace_is_forced_to_bound(
    pat, owner, workspace, other_workspace
):
    Conversation.objects.create(workspace=workspace, created_by=owner)
    Conversation.objects.create(workspace=other_workspace, created_by=owner)

    response = pat_client(pat).get(f"/api/v1/ai/conversations?workspace={other_workspace.id}")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert str(response.data[0]["workspace"]) == str(workspace.id)


def test_pat_by_id_message_list_for_conversation_in_a_different_workspace_returns_404(
    pat, owner, other_workspace
):
    """Proves the round-2 P1 fix's by-id-lookup coverage: the PAT's
    underlying user IS a member of other_workspace too, but the PAT itself
    is bound to a different one -- a by-id lookup for a conversation that
    genuinely exists (in the wrong workspace) must still 404, not resolve
    via a bare for_actor(user) membership check."""
    conversation = Conversation.objects.create(workspace=other_workspace, created_by=owner)
    response = pat_client(pat).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    assert response.status_code == 404


def test_pat_send_message_stamps_channel_cli_on_both_rows(
    pat, owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = pat_client(pat).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi from cli"},
        format="json",
    )
    assert response.status_code == 200
    events = _parse_sse(response)
    assert events[-1]["type"] == "done"

    user_message = Message.objects.get(
        conversation=conversation, role=Message.Role.USER, content="hi from cli"
    )
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert user_message.channel == CHANNEL_CLI
    assert assistant_message.channel == CHANNEL_CLI


def test_web_send_message_still_stamps_channel_web(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, web_client
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = web_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi from web"},
        format="json",
    )
    assert response.status_code == 200
    _parse_sse(response)  # consume the generator to trigger persistence

    user_message = Message.objects.get(
        conversation=conversation, role=Message.Role.USER, content="hi from web"
    )
    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    assert user_message.channel == Message.Channel.WEB
    assert assistant_message.channel == Message.Channel.WEB


def test_pat_message_list_never_reveals_overlay_source_workspaces(
    pat, owner, workspace, fake_provider, system_prompt, router_intent_prompt, web_client
):
    """Codex round-1 P1 (p1-personal-overlay): a web-channel turn in this
    conversation used the cross-workspace overlay and persisted
    Message.overlay_source_workspaces naming a workspace the PAT was never
    bound to. The PAT IS legitimately allowed to read this conversation
    (it's the PAT's own bound workspace, scoped_for_actor lets it through)
    -- but that provenance field must still never reach a low-trust read,
    even for a message the channel can otherwise fully see."""
    from memory.models import Memory, compute_fingerprint

    company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
    overlay_memory = Memory.objects.create(
        workspace=company,
        memory_type=Memory.Type.WORK_LOG,
        status=Memory.Status.APPROVED,
        source=Memory.Source.SYSTEM_GENERATED,
        title="Log",
        content="shipped the migration",
        fingerprint=compute_fingerprint("shipped the migration"),
    )
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    web_response = web_client(owner).post(
        f"/api/v1/ai/conversations/{conversation.id}/messages",
        {"content": "hi from web"},
        format="json",
    )
    assert web_response.status_code == 200
    _parse_sse(web_response)

    assistant_message = Message.objects.get(conversation=conversation, role=Message.Role.ASSISTANT)
    # Sanity check: the row really does carry the provenance this test guards.
    assert assistant_message.overlay_source_workspaces == {str(overlay_memory.id): str(company.id)}

    listing = pat_client(pat).get(f"/api/v1/ai/conversations/{conversation.id}/messages")
    assert listing.status_code == 200
    serialized = [m for m in listing.data if m["role"] == "assistant"][0]
    assert serialized["overlay_source_workspaces"] == {}
    # The memory id itself (and the reply content) are unaffected -- only
    # the cross-workspace provenance mapping is redacted.
    assert str(overlay_memory.id) in serialized["memories_used"]
