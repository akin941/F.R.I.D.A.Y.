"""Telegram relay + link-code lifecycle (MVP 9 §4/§5). Autodiscovered by
`config.celery.app.autodiscover_tasks()`.

`process_telegram_update` is the ONLY consumer of a raw Telegram webhook
payload -- the webhook view (integrations/views.py) does nothing but verify
the secret header and enqueue this task, so a slow LLM gateway call never
runs inside Telegram's own response-wait window (see that view's docstring
for why that matters under at-least-once redelivery).
"""

import json
import uuid

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import models, transaction
from django.utils import timezone

from assistant.models import Message
from assistant.services import (
    build_gateway_stream,
    classify_intent,
    create_conversation,
    stream_assistant_reply,
)
from integrations import telegram_client
from integrations.models import LinkedTelegramChat, TelegramLinkCode
from workspaces.models import Workspace

_LINK_COMMAND_PREFIX = "/link "


def _parse_rate(rate):
    """Same format DRF's SimpleRateThrottle.parse_rate accepts
    ("<count>/<period>", period one of s/m/h/d) -- reimplemented locally
    (rather than instantiating SimpleRateThrottle, which requires a
    view-bound `.scope`) since the parsing itself doesn't touch `self` at
    all in DRF's own implementation either."""
    num, period = rate.split("/")
    return int(num), {"s": 1, "m": 60, "h": 3600, "d": 86400}[period[0]]


# Mirrors the "chat" scope's budget (ScopedRateThrottle, assistant/views.py)
# -- the Telegram relay path is a Celery task, never passes through DRF's
# view-level throttling at all, so without an equivalent check here a
# linked chat could enqueue unbounded provider calls (Codex review,
# branch 3 round 1). Fixed-window counter via Django's cache framework --
# Redis-backed wherever REDIS_URL is set (Phase 4 branch 1), so the count
# is shared across every worker process.
_RELAY_RATE_LIMIT, _RELAY_RATE_WINDOW_SECONDS = _parse_rate(
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["chat"]
)


def _relay_rate_limited(user_id):
    cache_key = f"telegram-relay-throttle:{user_id}"
    cache.add(cache_key, 0, timeout=_RELAY_RATE_WINDOW_SECONDS)
    return cache.incr(cache_key) > _RELAY_RATE_LIMIT


@shared_task
def process_telegram_update(update):
    """`update` is the raw, already-JSON-decoded Telegram Update object
    (DRF has already parsed the webhook request body -- see
    TelegramWebhookView.post)."""
    message = update.get("message")
    if not message:
        return  # non-message updates (edited_message, channel_post, ...) ignored

    chat = message.get("chat") or {}
    # Every inbound update is checked against chat.type == "private" BEFORE
    # anything else runs (MVP 9 §4, round-1 P1 fix): a group chat has no
    # per-sender identity in this design, only a per-chat one -- consuming
    # no link code and relaying no message here is non-negotiable, not a
    # best-effort choice.
    if chat.get("type") != "private":
        return
    chat_id = str(chat.get("id"))
    text = (message.get("text") or "").strip()
    if not text:
        return  # a sticker/photo/etc. with no caption -- nothing to relay

    if text.startswith(_LINK_COMMAND_PREFIX):
        _handle_link(chat_id, text[len(_LINK_COMMAND_PREFIX) :].strip())
        return

    _handle_relay(chat_id, text)


@transaction.atomic
def _consume_link_code(code, chat_id):
    """Atomically claims an unexpired, unconsumed TelegramLinkCode and, in
    the SAME locked transaction, replaces any pre-existing LinkedTelegramChat
    that would otherwise collide -- either on the model's own
    (user, workspace) UniqueConstraint, or on chat_id's independent
    uniqueness (a physical Telegram chat can only ever back one row).
    Mirrors agents/execution.py's identical select_for_update()-locked-claim
    shape. A second concurrent delivery of the same code (Telegram's
    at-least-once redelivery) simply fails to claim the already-consumed
    row and returns None -- never a silent double-link. Returns the new
    LinkedTelegramChat, or None if the code wasn't claimable."""
    now = timezone.now()
    link_code = (
        TelegramLinkCode.objects.select_for_update()
        .select_related("user")
        .filter(code=code, consumed_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if link_code is None:
        return None

    # Same revalidation _handle_relay performs on every relayed message
    # (round-1 P1 fix) applies here too, at claim time: a code minted
    # before its user was deactivated or removed from the workspace must
    # not still be consumable just because it hasn't expired yet (Codex
    # review, branch 3 round 3) -- otherwise the link flow would mutate
    # and audit workspace data on behalf of someone who's since lost
    # access to it.
    still_eligible = (
        link_code.user.is_active
        and Workspace.objects.filter(
            pk=link_code.workspace_id, members__user=link_code.user
        ).exists()
    )
    if not still_eligible:
        return None

    link_code.consumed_at = now
    link_code.save(update_fields=["consumed_at", "updated_at"])

    LinkedTelegramChat.objects.select_for_update().filter(
        models.Q(user=link_code.user, workspace=link_code.workspace) | models.Q(chat_id=chat_id)
    ).delete()

    # create_conversation (assistant.services), not a bare Conversation.
    # objects.create() -- the existing audited creation path
    # (assistant.conversation.created) applies to every conversation this
    # app creates, including one created from the Telegram link flow
    # rather than the web "new conversation" button (Codex review,
    # branch 3 round 2).
    conversation = create_conversation(workspace=link_code.workspace, actor=link_code.user)
    return LinkedTelegramChat.objects.create(
        workspace=link_code.workspace,
        user=link_code.user,
        chat_id=chat_id,
        conversation=conversation,
    )


def _handle_link(chat_id, code):
    linked = _consume_link_code(code, chat_id)
    if linked is None:
        telegram_client.send_message(chat_id, "Invalid or expired code.")
        return
    telegram_client.send_message(chat_id, "Linked! You can now chat with Jarvis here.")


def _handle_relay(chat_id, text):
    linked = (
        LinkedTelegramChat.objects.select_related("conversation", "user")
        .filter(chat_id=chat_id)
        .first()
    )
    if linked is None:
        telegram_client.send_message(
            chat_id, "This chat isn't linked yet -- get a code from the Jarvis settings page."
        )
        return

    # Revalidate the link is still good on every message, not only at link
    # time (Codex review, branch 3 round 1, P1): a user deactivated or
    # removed from the workspace since linking must not keep relaying
    # messages and running gateway calls through the stale row. Unlike
    # JWT/PAT auth, nothing else in this path re-checks either condition.
    still_valid = (
        linked.user.is_active
        and Workspace.objects.filter(pk=linked.workspace_id, members__user=linked.user).exists()
    )
    if not still_valid:
        telegram_client.send_message(
            chat_id, "This link is no longer valid -- please re-link from the Jarvis settings page."
        )
        return

    if _relay_rate_limited(linked.user_id):
        telegram_client.send_message(
            chat_id, "You're sending messages too fast -- please wait a moment and try again."
        )
        return

    conversation = linked.conversation
    # The exact same classify_intent -> build_gateway_stream -> persist
    # pipeline ConversationMessageListView.post runs (assistant/views.py) --
    # same router, same task-draft/tool-request drafting, same ModelUsage
    # accounting. client_local_date is unknown for a Telegram sender (no
    # per-request browser context), so relative due dates simply don't
    # resolve here -- the same graceful degradation an omitted value
    # already gets on the web endpoint.
    classification = classify_intent(
        conversation, actor=linked.user, content=text, client_local_date=None
    )
    gateway_stream, usage_holder, memory_ids, chunk_ids, _overlay_source_workspaces = (
        build_gateway_stream(
            conversation,
            actor=linked.user,
            content=text,
            channel=Message.Channel.TELEGRAM,
            intent=classification["intent"],
        )
    )
    Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content=text,
        status=Message.Status.COMPLETE,
        created_by=linked.user,
        intent=classification["intent"],
        intent_payload=classification["intent_payload"],
        channel=Message.Channel.TELEGRAM,
    )

    reply_text = _consume_stream_to_text(
        conversation, gateway_stream, usage_holder, memory_ids, chunk_ids
    )
    draft_note = _draft_description(classification)
    if draft_note:
        reply_text = f"{reply_text}\n\n{draft_note}" if reply_text else draft_note

    telegram_client.send_message(chat_id, reply_text)


def _consume_stream_to_text(conversation, gateway_stream, usage_holder, memory_ids, chunk_ids):
    """Consumes the SSE generator fully server-side (Telegram has no
    practical token-by-token streaming UX) -- reuses
    assistant.services.stream_assistant_reply as-is, so the assistant
    Message row (channel="telegram") is persisted through the exact same
    single-write-site discipline the web endpoint relies on
    (_persist_assistant_message), not a second, parallel persistence path."""
    chunks = []
    error_type = None
    for raw_event in stream_assistant_reply(
        conversation,
        gateway_stream,
        usage_holder,
        request_id=str(uuid.uuid4()),
        memory_ids=memory_ids,
        chunk_ids=chunk_ids,
        channel=Message.Channel.TELEGRAM,
    ):
        event = json.loads(raw_event.decode()[len("data: ") : -2])
        if event["type"] == "chunk":
            chunks.append(event["text"])
        elif event["type"] == "error":
            error_type = event["error_type"]
    if error_type is not None:
        return f"(Something went wrong: {error_type}.)"
    return "".join(chunks)


def _draft_description(classification):
    """A task_draft/tool_request draft produced by this relay degrades to
    a read-only plain text description -- Telegram never confirms a draft
    itself in this MVP, and neither does any later web-UI action (MVP 9
    §4: the web app's own draft-confirm card only exists for the live SSE
    stream that produced it). Acting on it means re-issuing the same
    request from the web chat."""
    intent = classification["intent"]
    payload = classification["intent_payload"]
    if intent == Message.Intent.TASK_DRAFT:
        due = f" (due {payload['due_date']})" if payload.get("due_date") else ""
        return (
            f'(I would create a task "{payload["title"]}"{due} -- send this from the '
            "web chat if you'd like me to actually do it.)"
        )
    if intent == Message.Intent.TOOL_REQUEST:
        return (
            f'(I would run the "{payload["tool_name"]}" tool -- send this from the '
            "web chat if you'd like me to actually do it.)"
        )
    return None


@shared_task
def sweep_expired_link_codes():
    """Periodic scan (Celery beat, embedded via the worker's `-B` flag).
    Hard-delete is fine here -- a TelegramLinkCode carries no audit value
    once expired and unconsumed, unlike an ApprovalRequest or
    PersonalAccessToken. Also runnable manually via
    `manage.py sweep_telegram_link_codes` (calls this directly, not
    `.delay()` -- no broker/worker required)."""
    deleted, _ = TelegramLinkCode.objects.filter(
        consumed_at__isnull=True, expires_at__lt=timezone.now()
    ).delete()
    return deleted
