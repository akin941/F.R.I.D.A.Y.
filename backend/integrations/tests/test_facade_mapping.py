"""Stateless client -> server conversation mapping of the OpenAI-compatible
facade (Phase 4 branch 6): append, fork with a copied prefix, regenerate,
edit, pin, and isolation between tokens and workspaces."""

import uuid

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from audit.models import AuditLog
from integrations.models import FacadeConversation
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
COMPLETIONS = "/api/v1/openai/chat/completions"


@pytest.fixture(autouse=True)
def setup_ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.FACADE_RATE = "1000/min"
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    for name, purpose in [("friday_system", "chat"), ("friday_router_intent", "router.intent")]:
        template = PromptTemplate.objects.create(name=name, purpose=purpose)
        version = PromptVersion.objects.create(template=template, version=1, content="x")
        template.active_version = version
        template.save(update_fields=["active_version"])


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_token(user, workspace):
    raw = TOKEN_PREFIX + uuid.uuid4().hex
    PersonalAccessToken.objects.create(
        user=user,
        workspace=workspace,
        name="open webui",
        kind=PersonalAccessToken.Kind.OPENAI,
        token_hash=hash_token(raw),
    )
    return raw


@pytest.fixture
def token(owner, workspace):
    return make_token(owner, workspace)


def send(raw, *turns, pin=None):
    """POST a history given as alternating user/assistant texts (user
    first); returns (conversation, reply text)."""
    messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": text}
        for index, text in enumerate(turns)
    ]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    extra = {"HTTP_X_FRIDAY_CONVERSATION_ID": str(pin)} if pin else {}
    response = client.post(
        COMPLETIONS, {"model": "friday", "messages": messages}, format="json", **extra
    )
    assert response.status_code == 200, response.content
    conversation = Conversation.objects.get(pk=response["X-Friday-Conversation-Id"])
    return conversation, response.json()["choices"][0]["message"]["content"]


def history(conversation):
    return [
        (m.role, m.content)
        for m in conversation.messages.filter(status=Message.Status.COMPLETE).order_by(
            "created_at", "id"
        )
    ]


def test_append_continues_one_conversation(token):
    first, reply = send(token, "u1")
    second, _ = send(token, "u1", reply, "u2")
    assert second == first
    assert [content for role, content in history(first) if role == "user"] == ["u1", "u2"]
    assert Conversation.objects.count() == 1
    assert FacadeConversation.objects.get().user_turn_hashes[-1]


def test_client_assistant_text_never_matters_for_matching(token):
    first, _ = send(token, "u1")
    second, _ = send(token, "u1", "a client-side rewrite of the reply", "u2")
    assert second == first


def test_regenerate_forks_with_the_copied_prefix(token):
    source, reply1 = send(token, "u1")
    send(token, "u1", reply1, "u2")
    assert len(history(source)) == 4

    fork, _ = send(token, "u1", reply1, "u2")  # regenerate the second answer
    assert fork != source
    assert len(history(source)) == 4  # the original is untouched
    rows = history(fork)
    assert rows[:2] == history(source)[:2]  # u1 + its reply, copied
    assert [role for role, _ in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[2] == ("user", "u2")

    # The extraction cursor started after the copies: the fork's only job
    # covers the new turn, never the copied prefix again.
    from memory.models import MemoryExtractionJob

    copies = list(fork.messages.order_by("created_at", "id"))[:2]
    job = MemoryExtractionJob.objects.get(conversation=fork)
    assert job.after_message_id == copies[1].id
    audit = AuditLog.objects.get(action="assistant.conversation.forked")
    assert audit.metadata["source_conversation"] == str(source.id)
    assert audit.metadata["copied_messages"] == 2
    assert audit.metadata["channel"] == "openai"


def test_edit_forks_and_the_next_turn_follows_the_fork(token):
    source, reply1 = send(token, "u1")
    send(token, "u1", reply1, "u2")
    fork, reply2 = send(token, "u1", reply1, "u2 edited")
    assert fork != source
    later, _ = send(token, "u1", reply1, "u2 edited", reply2, "u3")
    assert later == fork
    assert [c for r, c in history(fork) if r == "user"] == ["u1", "u2 edited", "u3"]


def test_regenerating_the_first_answer_forks_with_nothing_copied(token):
    source, _ = send(token, "u1")
    fork, _ = send(token, "u1")
    assert fork != source
    assert [c for r, c in history(fork) if r == "user"] == ["u1"]


def test_turns_the_server_never_saw_still_continue(token):
    """A turn answered by another model (Open WebUI lets you switch) is in
    the client history but not on the server: that is a continuation, not a
    divergence."""
    first, reply1 = send(token, "u1")
    second, _ = send(token, "u1", reply1, "asked another model", "its answer", "u3")
    assert second == first
    assert [c for r, c in history(first) if r == "user"] == ["u1", "u3"]
    binding = FacadeConversation.objects.get()
    assert len(binding.user_turn_hashes) == 3
    assert binding.user_turn_message_ids[1] is None


def test_history_brought_along_starts_a_conversation_that_then_continues(token):
    first, reply = send(token, "old question", "old answer", "u2")
    assert [c for r, c in history(first) if r == "user"] == ["u2"]
    second, _ = send(token, "old question", "old answer", "u2", reply, "u3")
    assert second == first


def test_fork_after_unseen_turns_copies_only_the_shared_prefix(token):
    source, reply1 = send(token, "u1")
    send(token, "u1", reply1, "other model q", "other model a", "u3")
    fork, _ = send(token, "u1", reply1, "other model q", "other model a", "u3 edited")
    assert fork != source
    assert [c for r, c in history(fork) if r == "user"] == ["u1", "u3 edited"]


def test_a_new_chat_with_the_same_first_message_is_a_new_conversation(token):
    first, _ = send(token, "hello")
    assert send(token, "hello")[0] != first  # regenerate of turn one == new chat


def test_identical_first_messages_under_two_tokens_never_share(owner, workspace, token):
    other_token = make_token(owner, workspace)
    first, reply = send(token, "same start")
    second, _ = send(other_token, "same start")
    assert first != second
    third, _ = send(other_token, "same start", reply, "next")
    assert third == second


def test_pin_appends_to_the_named_conversation(owner, workspace, token):
    web_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    pinned, _ = send(token, "anything", pin=web_conversation.id)
    assert pinned == web_conversation
    assert [c for r, c in history(web_conversation) if r == "user"] == ["anything"]
    assert not FacadeConversation.objects.exists()  # pins bypass the mapping


def test_pre_rename_pin_header_is_still_accepted(owner, workspace, token):
    """Jarvis -> Friday (2026-11-20): X-Jarvis-Conversation-Id still pins."""
    web_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.post(
        COMPLETIONS,
        {"model": "friday", "messages": [{"role": "user", "content": "legacy pin"}]},
        format="json",
        HTTP_X_JARVIS_CONVERSATION_ID=str(web_conversation.id),
    )
    assert response.status_code == 200, response.content
    assert response["X-Friday-Conversation-Id"] == str(web_conversation.id)
    assert [c for r, c in history(web_conversation) if r == "user"] == ["legacy pin"]


def test_pinning_another_workspaces_conversation_is_404(owner, token):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    theirs = Conversation.objects.create(
        workspace=Workspace.objects.get(owner=stranger), created_by=stranger
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.post(
        COMPLETIONS,
        {"model": "friday", "messages": [{"role": "user", "content": "x"}]},
        format="json",
        HTTP_X_FRIDAY_CONVERSATION_ID=str(theirs.id),
    )
    assert response.status_code == 404
    assert not theirs.messages.exists()


def test_pinning_the_owners_other_workspace_is_404(owner, token):
    """The owner is a member of a second workspace; the token is bound to
    the first one -- the pin cannot cross the binding."""
    from workspaces.models import WorkspaceMember

    firm_owner = register_user(email="firm@test.dev", password=PASSWORD, name="Firm")
    firm = Workspace.objects.get(owner=firm_owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.MEMBER)
    firm_conversation = Conversation.objects.create(workspace=firm, created_by=owner)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.post(
        COMPLETIONS,
        {"model": "friday", "messages": [{"role": "user", "content": "x"}]},
        format="json",
        HTTP_X_FRIDAY_CONVERSATION_ID=str(firm_conversation.id),
    )
    assert response.status_code == 404


def test_malformed_pin_is_400(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = client.post(
        COMPLETIONS,
        {"model": "friday", "messages": [{"role": "user", "content": "x"}]},
        format="json",
        HTTP_X_FRIDAY_CONVERSATION_ID="not-a-uuid",
    )
    assert response.status_code == 400


def test_bindings_stay_in_the_tokens_workspace(owner, workspace, token):
    send(token, "u1")
    binding = FacadeConversation.objects.get()
    assert binding.workspace == workspace
    assert binding.conversation.workspace == workspace
    assert FacadeConversation.objects.for_actor(owner, workspace=workspace).count() == 1
    stranger = register_user(email="s2@test.dev", password=PASSWORD, name="S2")
    assert not FacadeConversation.objects.for_actor(stranger).exists()


def test_a_private_source_forks_private(owner, token):
    from assistant.services import mark_conversation_private

    source, reply = send(token, "u1")
    send(token, "u1", reply, "u2")
    mark_conversation_private(conversation=source, actor=owner)
    fork, _ = send(token, "u1", reply, "u2")
    assert fork != source
    assert fork.private
