"""Async read follow-ups (Phase 12 D11, "async voice turns").

An owner's turn that picks an async read (an n8n workflow that takes
minutes) gets a Friday-authored reply at once -- "Başlattım: <label>.
Sonucu bu konuşmaya yazacağım." -- and a `PendingFollowUp`. When the read
ends, `connectors` calls `tools.registry.notify_async_read(handle)`; the
listener registered here at startup dispatches `compose_followup`, which:

- claims the follow-up (once: a second dispatch never makes a second call);
- **drops** it, with no model call and nothing appended, when the
  conversation is gone (R6) -- final, never replayed;
- for a failed or timed-out read appends a Friday template with the closed
  error code (no model call);
- otherwise makes exactly **one** completion, purpose `chat.followup`, with
  the result fenced as `<connector_result>` (`render_connector_block`) and
  the owner's original request -- no tools, no router, no read, no draft --
  and appends it as an assistant message with `followup_of` = the turn's
  user message. `personal_data` per the read's data class (the conversation
  was already marked by the turn that started a personal read). A refused
  completion (budget, no allowed provider) appends a template instead.

This continues an owner-initiated turn; it is not an unattended run (D11).
Nothing here creates a task, approval, memory, read or notice: the
`assistant.followups_ready` probe announces finished follow-ups.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ai_core.exceptions import BudgetExceeded, GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from assistant.context import render_connector_block
from assistant.models import Conversation, Message, PendingFollowUp
from audit.models import AuditLog
from audit.services import record_audit

logger = logging.getLogger(__name__)

PURPOSE = "chat.followup"
PROMPT_NAME = "friday_chat_followup"
STARTED_TEMPLATE = "Başlattım: {label}. Sonucu bu konuşmaya yazacağım."
# Closed outcome codes -> Friday-authored follow-up text (no model text).
FAILED_TEMPLATES = {
    "timed_out": "{label}: zaman aşımına uğradı, sonuç gelmedi.",
    "budget_refused": "{label}: sonuç geldi ama bütçe sınırı yüzünden özetleyemedim.",
    "local_model_required": (
        "{label}: sonuç geldi ama kişisel veriyi okuyabilecek yerel bir model yok."
    ),
}
FAILED_DEFAULT = "{label}: sonuç alınamadı ({code})."
# A claim this old never finished (a lost worker): the follow-up ends
# `failed` -- its completion is never attempted twice.
CLAIM_TIMEOUT = timedelta(minutes=10)

Status = PendingFollowUp.Status


def started_reply(label):
    return STARTED_TEMPLATE.format(label=label)


def _template(code, label):
    return FAILED_TEMPLATES.get(code, FAILED_DEFAULT).format(label=label, code=code)


def _handle(followup):
    from tools.registry import AsyncReadHandle

    return AsyncReadHandle(id=followup.handle_id, tool=followup.tool_name)


def create_followup(
    *, conversation, source_message, actor, channel, handle, tool_label, source, data_class
):
    """Record the pending follow-up of the turn that started `handle`'s
    read (the chat turn, right after its user message exists). If the read
    already ended meanwhile (a fast worker), its composition is dispatched
    now -- the listener found nothing to wake."""
    followup = PendingFollowUp.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        source_message=source_message,
        created_by=actor,
        channel=channel,
        tool_name=handle.tool,
        tool_label=tool_label[:60],
        source=source[:40],
        data_class=data_class,
        handle_id=handle.id,
    )
    record_audit(
        action="assistant.followup.started",
        actor=actor,
        workspace=conversation.workspace,
        resource=followup,
        metadata={"tool": handle.tool, "channel": channel, "data_class": data_class},
    )
    from tools.registry import fetch_async_read

    if fetch_async_read(handle).terminal:
        dispatch_compose(str(followup.pk))
    return followup


def on_async_read_finished(handle):
    """The `tools.registry` listener (registered in `AssistantConfig.ready`):
    wake the follow-up waiting for this read, if any."""
    followup_id = (
        PendingFollowUp.objects.filter(
            handle_id=handle.id, tool_name=handle.tool, status=Status.PENDING
        )
        .values_list("pk", flat=True)
        .first()
    )
    if followup_id is not None:
        dispatch_compose(str(followup_id))


def dispatch_compose(followup_id):
    """Publish `compose_followup` after commit; a broker failure never
    propagates (the recovery scan re-publishes a pending follow-up whose
    read has ended)."""
    from django.conf import settings

    from assistant.tasks import compose_followup_task

    def publish():
        if settings.CELERY_TASK_ALWAYS_EAGER:
            compose_followup_task.delay(followup_id)
            return
        try:
            compose_followup_task.delay(followup_id)
        except Exception:
            logger.exception("Failed to publish follow-up %s to the broker", followup_id)

    transaction.on_commit(publish)


def _claim(followup_id):
    with transaction.atomic():
        followup = (
            PendingFollowUp.objects.select_for_update(skip_locked=True)
            .filter(pk=followup_id, status=Status.PENDING, claimed_at__isnull=True)
            .first()
        )
        if followup is None:
            return None
        followup.claimed_at = timezone.now()
        followup.save(update_fields=["claimed_at", "updated_at"])
    return followup


def _release(followup):
    PendingFollowUp.objects.filter(pk=followup.pk, status=Status.PENDING).update(
        claimed_at=None, updated_at=timezone.now()
    )


def _conversation_live(followup):
    """The conversation still exists (and, should conversations ever gain a
    trash, is not in it). A follow-up never looks for another home."""
    if followup.conversation_id is None:
        return None
    conversation = Conversation.objects.filter(pk=followup.conversation_id).first()
    if conversation is None or getattr(conversation, "deleted_at", None) is not None:
        return None
    return conversation


def _finish(followup, *, status, outcome="", message=None):
    with transaction.atomic():
        updated = PendingFollowUp.objects.filter(pk=followup.pk, status=Status.PENDING).update(
            status=status,
            outcome=outcome,
            followup_message=message,
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        if updated:
            record_audit(
                action=f"assistant.followup.{status}",
                actor=None,
                actor_type=AuditLog.ActorType.SYSTEM,
                workspace=followup.workspace,
                resource=followup,
                metadata={"tool": followup.tool_name, "outcome": outcome},
            )
    return bool(updated)


def _append(conversation, followup, *, content, usage=None, reads=None):
    """The follow-up message: an assistant row of the same conversation,
    linked to the turn that started the read. Serialized with every other
    write of the conversation (the persist lock)."""
    with transaction.atomic():
        locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
        return Message.objects.create(
            workspace=locked.workspace,
            conversation=locked,
            role=Message.Role.ASSISTANT,
            content=content,
            status=Message.Status.COMPLETE,
            model_usage=usage,
            channel=followup.channel,
            connector_reads=reads or [],
            followup_of_id=followup.source_message_id,
        )


def _prompt_version():
    template = (
        PromptTemplate.objects.select_related("active_version").filter(name=PROMPT_NAME).first()
    )
    if template is None or template.active_version_id is None:
        return None
    return template.active_version


def compose_followup(followup_id):
    """See the module docstring. Returns the follow-up's final status, or
    `None` when nothing was done (not claimable, or the read still runs)."""
    from assistant.services import conversation_has_personal_data, mark_conversation_personal_data
    from tools.registry import ASYNC_OK, DATA_CLASS_PERSONAL, fetch_async_read

    followup = _claim(followup_id)
    if followup is None:
        return None
    conversation = _conversation_live(followup)
    if conversation is None:
        _finish(followup, status=Status.DROPPED, outcome="conversation_gone")
        return Status.DROPPED
    outcome = fetch_async_read(_handle(followup))
    if not outcome.terminal:
        _release(followup)
        return None
    label = followup.tool_label or followup.tool_name
    if outcome.status != ASYNC_OK or outcome.result is None:
        code = outcome.status if outcome.status != "failed" else (outcome.error_code or "failed")
        message = _append(conversation, followup, content=_template(code, label))
        _finish(followup, status=Status.FAILED, outcome=code, message=message)
        return Status.FAILED

    personal = (
        followup.data_class == DATA_CLASS_PERSONAL
        or outcome.data_class == DATA_CLASS_PERSONAL
        or conversation_has_personal_data(conversation)
    )
    gateway = InferenceGateway()
    prompt_version = _prompt_version()
    refused = ""
    if prompt_version is None:
        refused = "no_prompt"
    elif personal and not gateway.has_eligible_provider(purpose=PURPOSE, personal_data=True):
        refused = "local_model_required"
    if refused:
        message = _append(conversation, followup, content=_template(refused, label))
        _finish(followup, status=Status.FAILED, outcome=refused, message=message)
        return Status.FAILED
    if personal:
        mark_conversation_personal_data(conversation=conversation, actor=followup.created_by)

    block, report = render_connector_block(
        outcome.result, tool=followup.tool_name, source=followup.source
    )
    report["data_class"] = DATA_CLASS_PERSONAL if personal else (followup.data_class or "")
    report["mode"] = "async"
    source_text = ""
    if followup.source_message_id is not None:
        source_text = (
            Message.objects.filter(pk=followup.source_message_id)
            .values_list("content", flat=True)
            .first()
            or ""
        )
    messages = [
        {"role": "system", "content": prompt_version.content + block},
        {"role": "user", "content": source_text or label},
    ]
    try:
        result = gateway.complete(
            purpose=PURPOSE,
            messages=messages,
            actor=followup.created_by,
            workspace=conversation.workspace,
            prompt_version=prompt_version,
            stream=False,
            context_report={
                "followup_of": str(followup.source_message_id or ""),
                "connector_tool": followup.tool_name,
            },
            personal_data=personal,
        )
    except BudgetExceeded:
        code = "budget_refused"
    except NoProviderAvailable:
        code = "local_model_required" if personal else "no_provider"
    except GatewayError as exc:
        code = exc.error_type or "model_error"
    else:
        text = (result.text or "").strip()
        if not text:
            message = _append(
                conversation, followup, content=_template("empty", label), usage=result.usage
            )
            _finish(followup, status=Status.FAILED, outcome="empty", message=message)
            return Status.FAILED
        message = _append(conversation, followup, content=text, usage=result.usage, reads=[report])
        _finish(followup, status=Status.DONE, message=message)
        return Status.DONE
    message = _append(conversation, followup, content=_template(code, label))
    _finish(followup, status=Status.FAILED, outcome=code, message=message)
    return Status.FAILED


def recover_followups(now=None):
    """Every minute: re-dispatch pending follow-ups whose read has ended
    (a lost notification) and fail claims that never finished (a lost
    worker -- the completion is never attempted again)."""
    from tools.registry import fetch_async_read

    now = now or timezone.now()
    redispatched = lost = 0
    stale = PendingFollowUp.objects.filter(
        status=Status.PENDING, claimed_at__lt=now - CLAIM_TIMEOUT
    )
    for followup in stale:
        if _finish(followup, status=Status.FAILED, outcome="worker_lost"):
            lost += 1
    waiting = PendingFollowUp.objects.filter(
        status=Status.PENDING,
        claimed_at__isnull=True,
        created_at__lt=now - timedelta(seconds=60),
    )
    for followup in waiting:
        if fetch_async_read(_handle(followup)).terminal:
            dispatch_compose(str(followup.pk))
            redispatched += 1
    return {"redispatched": redispatched, "worker_lost": lost}
