import pytest
from django.db import IntegrityError
from django.utils import timezone

from accounts.services import register_user
from assistant.models import Conversation
from integrations.models import LinkedTelegramChat, TelegramLinkCode
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def test_link_code_code_is_unique(owner, workspace):
    TelegramLinkCode.objects.create(
        workspace=workspace, user=owner, code="dup", expires_at=timezone.now()
    )
    with pytest.raises(IntegrityError):
        TelegramLinkCode.objects.create(
            workspace=workspace, user=owner, code="dup", expires_at=timezone.now()
        )


def test_linked_chat_chat_id_is_unique(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="123", conversation=conversation
    )
    other_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    other = register_user(email="other@test.dev", password=PASSWORD, name="Other")
    with pytest.raises(IntegrityError):
        LinkedTelegramChat.objects.create(
            workspace=workspace, user=other, chat_id="123", conversation=other_conversation
        )


def test_linked_chat_user_workspace_pair_is_unique(owner, workspace):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id="123", conversation=conversation
    )
    other_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    with pytest.raises(IntegrityError):
        LinkedTelegramChat.objects.create(
            workspace=workspace, user=owner, chat_id="456", conversation=other_conversation
        )
