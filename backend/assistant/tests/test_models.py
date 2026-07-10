import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from accounts.services import register_user
from ai_core.models import ModelUsage
from assistant.models import Conversation, Message
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def bob():
    return register_user(email="bob@test.dev", password=PASSWORD, name="Bob")


@pytest.fixture
def workspace(alice):
    return Workspace.objects.get(owner=alice)


@pytest.fixture
def conversation(workspace, alice):
    return Conversation.objects.create(workspace=workspace, title="Test", created_by=alice)


# --- basic model shape ---


def test_conversation_has_uuid_and_timestamps(conversation):
    assert conversation.id is not None
    assert conversation.created_at is not None
    assert conversation.updated_at is not None


def test_conversation_title_defaults_to_empty(workspace, alice):
    conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
    assert conversation.title == ""


def test_conversation_str_falls_back_to_uuid_when_title_empty(workspace, alice):
    conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
    assert str(conversation.id) in str(conversation)


def test_conversation_str_uses_title_when_present(conversation):
    assert str(conversation) == "Test"


def test_message_default_status_is_complete(conversation, alice):
    message = Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=alice,
    )
    assert message.status == Message.Status.COMPLETE


def test_message_role_and_status_choices():
    assert set(Message.Role.values) == {"user", "assistant", "system"}
    assert set(Message.Status.values) == {"complete", "partial", "failed"}


def test_message_workspace_matches_conversation_workspace_convention(conversation, alice):
    message = Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=alice,
    )
    assert message.workspace_id == conversation.workspace_id


# --- scoping (for_actor) ---


def test_conversation_for_actor_scopes_by_membership(alice, bob, conversation):
    assert Conversation.objects.for_actor(alice).count() == 1
    assert Conversation.objects.for_actor(bob).count() == 0


def test_message_for_actor_scopes_by_membership(alice, bob, conversation):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=alice,
    )
    assert Message.objects.for_actor(alice).count() == 1
    assert Message.objects.for_actor(bob).count() == 0


@pytest.mark.parametrize(
    "role",
    [
        WorkspaceMember.Role.OWNER,
        WorkspaceMember.Role.ADMIN,
        WorkspaceMember.Role.MEMBER,
        WorkspaceMember.Role.VIEWER,
    ],
)
def test_conversation_visible_to_every_membership_role(workspace, conversation, bob, role):
    WorkspaceMember.objects.filter(workspace=workspace, user=bob).delete()
    WorkspaceMember.objects.create(workspace=workspace, user=bob, role=role)
    assert Conversation.objects.for_actor(bob, workspace=workspace).count() == 1


def test_non_member_excluded(bob, conversation):
    assert Conversation.objects.for_actor(bob).count() == 0


def test_anonymous_excluded(conversation):
    assert Conversation.objects.for_actor(None).count() == 0


def test_workspace_narrowing(alice, workspace, conversation):
    other_workspace = Workspace.objects.create(name="Other", owner=alice)
    WorkspaceMember.objects.create(
        workspace=other_workspace, user=alice, role=WorkspaceMember.Role.OWNER
    )
    assert Conversation.objects.for_actor(alice, workspace=other_workspace).count() == 0
    assert Conversation.objects.for_actor(alice, workspace=workspace).count() == 1


# --- deletion behavior ---


def test_conversation_deletion_cascades_to_messages(conversation, alice):
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=alice,
    )
    conversation_id = conversation.id
    conversation.delete()
    assert Message.objects.filter(conversation_id=conversation_id).count() == 0


def test_user_deletion_sets_conversation_created_by_null(workspace):
    guest = User.objects.create_user(email="guest@test.dev", password=PASSWORD, name="Guest")
    WorkspaceMember.objects.create(
        workspace=workspace, user=guest, role=WorkspaceMember.Role.MEMBER
    )
    conversation = Conversation.objects.create(workspace=workspace, created_by=guest, title="x")
    guest.delete()
    conversation.refresh_from_db()
    assert conversation.created_by is None


def test_user_deletion_sets_message_created_by_null(conversation, workspace):
    guest = User.objects.create_user(email="guest2@test.dev", password=PASSWORD, name="Guest2")
    WorkspaceMember.objects.create(
        workspace=workspace, user=guest, role=WorkspaceMember.Role.MEMBER
    )
    message = Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="hi",
        created_by=guest,
    )
    guest.delete()
    message.refresh_from_db()
    assert message.created_by is None


def test_model_usage_deletion_sets_message_field_null(conversation):
    usage = ModelUsage.objects.create(purpose="chat", workspace=conversation.workspace)
    message = Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="hi",
        model_usage=usage,
    )
    usage.delete()
    message.refresh_from_db()
    assert message.model_usage is None


def test_model_usage_cannot_attach_to_multiple_messages(conversation):
    usage = ModelUsage.objects.create(purpose="chat", workspace=conversation.workspace)
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="a",
        model_usage=usage,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Message.objects.create(
                workspace=conversation.workspace,
                conversation=conversation,
                role=Message.Role.ASSISTANT,
                content="b",
                model_usage=usage,
            )
