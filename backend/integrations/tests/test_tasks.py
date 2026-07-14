"""integrations.tasks unit tests: link-code consumption (atomicity,
expiry, group-chat rejection, replace-on-relink) and the Telegram relay
path (reusing the same classify_intent/build_gateway_stream pipeline the
web chat endpoint uses -- MVP 9 §4)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from integrations.models import LinkedTelegramChat, TelegramLinkCode
from integrations.tasks import process_telegram_update, sweep_expired_link_codes
from tasks.models import Task
from workspaces.models import Workspace

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


def make_link_code(user, workspace, **overrides):
    fields = {
        "workspace": workspace,
        "user": user,
        "code": "abc123",
        "expires_at": timezone.now() + timedelta(minutes=10),
    }
    fields.update(overrides)
    return TelegramLinkCode.objects.create(**fields)


def private_update(chat_id, text, chat_type="private"):
    return {
        "update_id": 1,
        "message": {"chat": {"id": chat_id, "type": chat_type}, "text": text},
    }


# --- link flow ---


def test_valid_code_links_creates_conversation_and_marks_consumed(owner, workspace):
    link_code = make_link_code(owner, workspace)
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "/link abc123"))

    link_code.refresh_from_db()
    assert link_code.consumed_at is not None
    linked = LinkedTelegramChat.objects.get(chat_id="chat-1")
    assert linked.user == owner
    assert linked.workspace == workspace
    assert Conversation.objects.filter(id=linked.conversation_id).exists()
    send.assert_called_once()
    assert "linked" in send.call_args.args[1].lower()


def test_expired_code_is_rejected_and_creates_nothing(owner, workspace):
    make_link_code(owner, workspace, expires_at=timezone.now() - timedelta(minutes=1))
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "/link abc123"))

    assert not LinkedTelegramChat.objects.exists()
    assert "invalid or expired" in send.call_args.args[1].lower()


def test_already_consumed_code_is_rejected_and_creates_nothing(owner, workspace):
    make_link_code(owner, workspace, consumed_at=timezone.now())
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "/link abc123"))

    assert not LinkedTelegramChat.objects.exists()
    assert "invalid or expired" in send.call_args.args[1].lower()


def test_unknown_code_is_rejected(owner, workspace):
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "/link nope"))

    assert not LinkedTelegramChat.objects.exists()
    assert "invalid or expired" in send.call_args.args[1].lower()


def test_code_cannot_be_consumed_twice_redelivery_safe(owner, workspace):
    """Proves the round-6 P2 fix: an atomic claim, not a plain
    read-then-write -- a second delivery of the same update (Telegram's
    at-least-once redelivery) must never double-link."""
    make_link_code(owner, workspace)
    with mock.patch("integrations.tasks.telegram_client.send_message"):
        process_telegram_update(private_update("chat-1", "/link abc123"))
        process_telegram_update(private_update("chat-1", "/link abc123"))

    assert LinkedTelegramChat.objects.filter(chat_id="chat-1").count() == 1


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_non_private_chat_never_consumes_a_code_or_links(owner, workspace, chat_type):
    """Proves the round-1 P1 fix -- an otherwise-valid code sent from a
    group must be silently ignored, never consumed."""
    link_code = make_link_code(owner, workspace)
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("group-1", "/link abc123", chat_type=chat_type))

    link_code.refresh_from_db()
    assert link_code.consumed_at is None
    assert not LinkedTelegramChat.objects.exists()
    send.assert_not_called()


def test_relinking_replaces_the_existing_user_workspace_row(owner, workspace):
    first_code = make_link_code(owner, workspace, code="first")
    with mock.patch("integrations.tasks.telegram_client.send_message"):
        process_telegram_update(private_update("chat-old", "/link first"))
    old_conversation_id = LinkedTelegramChat.objects.get(
        user=owner, workspace=workspace
    ).conversation_id

    second_code = make_link_code(owner, workspace, code="second")
    with mock.patch("integrations.tasks.telegram_client.send_message"):
        process_telegram_update(private_update("chat-new", "/link second"))

    assert LinkedTelegramChat.objects.filter(user=owner, workspace=workspace).count() == 1
    linked = LinkedTelegramChat.objects.get(user=owner, workspace=workspace)
    assert linked.chat_id == "chat-new"
    # The old conversation is untouched, simply no longer referenced.
    assert Conversation.objects.filter(id=old_conversation_id).exists()
    assert first_code.pk and second_code.pk  # sanity: both codes were real rows


# --- relay flow ---


def test_relay_from_unlinked_chat_asks_to_link_first(owner, workspace):
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "hello"))

    assert Message.objects.count() == 0
    assert Conversation.objects.count() == 0
    assert "link" in send.call_args.args[1].lower()


def _link(owner, workspace, chat_id="chat-1"):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    return LinkedTelegramChat.objects.create(
        workspace=workspace, user=owner, chat_id=chat_id, conversation=conversation
    )


def test_relay_stamps_channel_telegram_on_both_rows_and_records_model_usage(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt
):
    linked = _link(owner, workspace)
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "hi there"))

    user_message = Message.objects.get(
        conversation=linked.conversation, role=Message.Role.USER, content="hi there"
    )
    assistant_message = Message.objects.get(
        conversation=linked.conversation, role=Message.Role.ASSISTANT
    )
    assert user_message.channel == Message.Channel.TELEGRAM
    assert assistant_message.channel == Message.Channel.TELEGRAM
    assert ModelUsage.objects.filter(purpose="chat").exists()
    send.assert_called_once()
    assert send.call_args.args[0] == "chat-1"


def test_relay_task_draft_appends_a_read_only_description_never_a_confirm_path(
    owner, workspace, fake_provider, system_prompt, router_intent_prompt, task_draft_prompt
):
    linked = _link(owner, workspace)
    with mock.patch("integrations.tasks.telegram_client.send_message") as send:
        process_telegram_update(private_update("chat-1", "remind me to call mom"))

    user_message = Message.objects.get(conversation=linked.conversation, role=Message.Role.USER)
    assert user_message.intent == Message.Intent.TASK_DRAFT
    reply_text = send.call_args.args[1]
    assert "web chat" in reply_text.lower()
    assert "fake extracted task" in reply_text.lower()
    # No Task was created -- pure description, no confirm path in this MVP.
    assert not Task.objects.exists()


# --- periodic sweep ---


def test_sweep_deletes_only_expired_unconsumed_codes(owner, workspace):
    expired_unconsumed = make_link_code(
        owner, workspace, code="a", expires_at=timezone.now() - timedelta(minutes=1)
    )
    expired_consumed = make_link_code(
        owner,
        workspace,
        code="b",
        expires_at=timezone.now() - timedelta(minutes=1),
        consumed_at=timezone.now(),
    )
    still_valid = make_link_code(owner, workspace, code="c")

    deleted = sweep_expired_link_codes()

    assert deleted == 1
    assert not TelegramLinkCode.objects.filter(pk=expired_unconsumed.pk).exists()
    assert TelegramLinkCode.objects.filter(pk=expired_consumed.pk).exists()
    assert TelegramLinkCode.objects.filter(pk=still_valid.pk).exists()
