"""Phase 4 branch 4 (chat redesign) API surface: conversation rename, the
channel badge source (`origin_channel`), and the message-list fields the
reloaded chat needs (`channel`, `confirmed_task`, `chunk_documents`).

Role x endpoint for the PATCH: every workspace role may rename, exactly
like the existing private flag (one-way, see test_views.py); outsiders get
404, anonymous 401, and a PAT is fixed to its bound workspace.
"""

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from assistant.models import Conversation, Message
from audit.models import AuditLog
from knowledge.models import KnowledgeChunk, KnowledgeDocument
from tasks.models import Task
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def conversation(workspace, owner):
    return Conversation.objects.create(workspace=workspace, created_by=owner, title="Eski")


@pytest.fixture(params=ROLES)
def member_user(request, workspace, owner):
    role = request.param
    if role == WorkspaceMember.Role.OWNER:
        return owner
    user = User.objects.create_user(email=f"{role}@test.dev", password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def pat_client(owner, workspace):
    raw = "jpat_test-raw-token-rename-000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def detail_url(conversation):
    return f"/api/v1/ai/conversations/{conversation.id}"


def messages_url(conversation):
    return f"/api/v1/ai/conversations/{conversation.id}/messages"


def add_message(conversation, role=Message.Role.USER, **fields):
    return Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=role,
        content=fields.pop("content", "hi"),
        **fields,
    )


# --- rename: role x endpoint ---


def test_every_role_can_rename(member_user, conversation):
    response = auth_client(member_user).patch(
        detail_url(conversation), {"title": "Yeni ad"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["title"] == "Yeni ad"
    conversation.refresh_from_db()
    assert conversation.title == "Yeni ad"
    entry = AuditLog.objects.get(action="assistant.conversation.renamed")
    # Free user text never lands in the audit log.
    assert "Yeni ad" not in str(entry.metadata)


def test_rename_to_the_same_title_writes_no_audit(owner, conversation):
    response = auth_client(owner).patch(detail_url(conversation), {"title": "Eski"}, format="json")
    assert response.status_code == 200
    assert not AuditLog.objects.filter(action="assistant.conversation.renamed").exists()


def test_blank_title_clears_the_name(owner, conversation):
    response = auth_client(owner).patch(detail_url(conversation), {"title": ""}, format="json")
    assert response.status_code == 200
    conversation.refresh_from_db()
    assert conversation.title == ""


def test_too_long_title_is_400(owner, conversation):
    response = auth_client(owner).patch(
        detail_url(conversation), {"title": "x" * 256}, format="json"
    )
    assert response.status_code == 400
    conversation.refresh_from_db()
    assert conversation.title == "Eski"


def test_empty_patch_body_is_400(owner, conversation):
    response = auth_client(owner).patch(detail_url(conversation), {}, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "invalid"


def test_rename_and_mark_private_in_one_patch(owner, conversation):
    response = auth_client(owner).patch(
        detail_url(conversation), {"title": "Gizli", "private": True}, format="json"
    )
    assert response.status_code == 200
    assert response.data["title"] == "Gizli"
    assert response.data["private"] is True


def test_rename_with_private_false_is_rejected_atomically(owner, conversation):
    response = auth_client(owner).patch(
        detail_url(conversation), {"title": "Yeni", "private": False}, format="json"
    )
    assert response.status_code == 400
    conversation.refresh_from_db()
    assert conversation.title == "Eski"
    assert conversation.private is False


def test_rename_of_another_users_conversation_is_404(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    foreign = Conversation.objects.create(
        workspace=Workspace.objects.get(owner=stranger), created_by=stranger, title="Onların"
    )
    response = auth_client(owner).patch(detail_url(foreign), {"title": "Benim"}, format="json")
    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.title == "Onların"


def test_anonymous_rename_is_401(conversation):
    response = APIClient().patch(detail_url(conversation), {"title": "x"}, format="json")
    assert response.status_code == 401


def test_pat_can_rename_in_its_bound_workspace(owner, workspace, conversation):
    response = pat_client(owner, workspace).patch(
        detail_url(conversation), {"title": "CLI adı"}, format="json"
    )
    assert response.status_code == 200
    conversation.refresh_from_db()
    assert conversation.title == "CLI adı"


def test_pat_cannot_rename_outside_its_bound_workspace(owner, workspace):
    stranger = register_user(email="stranger2@test.dev", password=PASSWORD, name="Stranger")
    other = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=other, user=owner, role=WorkspaceMember.Role.MEMBER)
    foreign = Conversation.objects.create(workspace=other, created_by=owner, title="Diğer")
    response = pat_client(owner, workspace).patch(
        detail_url(foreign), {"title": "x"}, format="json"
    )
    assert response.status_code == 404


# --- origin_channel (channel badge source) ---


def test_list_carries_the_first_messages_channel(owner, workspace):
    telegram = Conversation.objects.create(workspace=workspace, created_by=owner, title="tg")
    add_message(telegram, channel=Message.Channel.TELEGRAM)
    add_message(telegram, role=Message.Role.ASSISTANT, channel=Message.Channel.WEB)
    empty = Conversation.objects.create(workspace=workspace, created_by=owner, title="empty")

    response = auth_client(owner).get(f"/api/v1/ai/conversations?workspace={workspace.id}")

    by_id = {row["id"]: row for row in response.data}
    assert by_id[str(telegram.id)]["origin_channel"] == "telegram"
    assert by_id[str(empty.id)]["origin_channel"] is None


def test_detail_and_patch_carry_origin_channel(owner, conversation):
    add_message(conversation, channel=Message.Channel.CLI)
    client = auth_client(owner)
    assert client.get(detail_url(conversation)).data["origin_channel"] == "cli"
    patched = client.patch(detail_url(conversation), {"title": "y"}, format="json")
    assert patched.data["origin_channel"] == "cli"


# --- message list: channel, confirmed_task, chunk_documents ---


def test_message_list_exposes_channel_and_intent(owner, conversation):
    add_message(
        conversation,
        channel=Message.Channel.TELEGRAM,
        intent=Message.Intent.TASK_DRAFT,
        intent_payload={"title": "Annemi ara", "due_date": None},
    )
    row = auth_client(owner).get(messages_url(conversation)).data[0]
    assert row["channel"] == "telegram"
    assert row["intent"] == "task_draft"
    assert row["intent_payload"] == {"title": "Annemi ara", "due_date": None}
    assert row["confirmed_task"] is None


def test_confirmed_draft_reports_its_task(owner, workspace, conversation):
    draft = add_message(
        conversation,
        created_by=owner,
        intent=Message.Intent.TASK_DRAFT,
        intent_payload={"title": "Annemi ara", "due_date": None},
    )
    other = add_message(conversation, role=Message.Role.ASSISTANT, content="Tamam")
    client = auth_client(owner)
    created = client.post(
        "/api/v1/tasks",
        {"workspace": str(workspace.id), "title": "Annemi ara", "draft_message_id": str(draft.id)},
        format="json",
    )
    assert created.status_code == 201

    rows = {row["id"]: row for row in client.get(messages_url(conversation)).data}
    assert rows[str(draft.id)]["confirmed_task"] == created.data["id"]
    assert rows[str(other.id)]["confirmed_task"] is None

    Task.objects.all().delete()
    rows = {row["id"]: row for row in client.get(messages_url(conversation)).data}
    assert rows[str(draft.id)]["confirmed_task"] is None


def _chunk(workspace, title):
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title=title,
        status=KnowledgeDocument.Status.READY,
        raw_text=title,
        content_hash=f"hash-{title}",
    )
    chunk = KnowledgeChunk.objects.create(
        workspace=workspace, document=document, ordinal=0, content=title
    )
    return document, chunk


def test_chunk_documents_resolve_only_in_scope_chunks(owner, workspace, conversation):
    document, chunk = _chunk(workspace, "Plan")
    stranger = register_user(email="stranger3@test.dev", password=PASSWORD, name="Stranger")
    _, foreign_chunk = _chunk(Workspace.objects.get(owner=stranger), "Onların")
    reply = add_message(
        conversation,
        role=Message.Role.ASSISTANT,
        chunks_used=[str(chunk.id), str(foreign_chunk.id), "not-a-uuid"],
    )
    plain = add_message(conversation, role=Message.Role.ASSISTANT)

    rows = {row["id"]: row for row in auth_client(owner).get(messages_url(conversation)).data}

    assert rows[str(reply.id)]["chunk_documents"] == {str(chunk.id): str(document.id)}
    assert rows[str(plain.id)]["chunk_documents"] == {}


def test_chunk_of_a_deleted_document_has_no_link(owner, workspace, conversation):
    document, chunk = _chunk(workspace, "Silinecek")
    reply = add_message(conversation, role=Message.Role.ASSISTANT, chunks_used=[str(chunk.id)])
    document.delete()
    rows = {row["id"]: row for row in auth_client(owner).get(messages_url(conversation)).data}
    assert rows[str(reply.id)]["chunks_used"] == [str(chunk.id)]
    assert rows[str(reply.id)]["chunk_documents"] == {}
