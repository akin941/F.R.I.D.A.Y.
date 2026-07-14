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
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    response = auth_client(owner).post(
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
