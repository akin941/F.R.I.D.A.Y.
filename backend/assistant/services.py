"""Chat orchestration: gateway call + SSE framing (ADR-003 — ai_core is the
single door for LLM traffic; this module never talks to a provider SDK)."""

import json
import uuid
from datetime import date

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from assistant.context import build_context
from assistant.intent import IntentParseError, parse_intent
from assistant.models import Conversation, Message
from assistant.task_draft import TaskDraftParseError, parse_task_draft
from audit.services import record_audit
from core.context import request_id_var
from memory.jobs import create_extraction_job

_ROUTER_INTENT_PROMPT_NAME = "jarvis_router_intent"
_TASK_DRAFT_PROMPT_NAME = "jarvis_router_task_draft"

# Router-local tool vocabulary (MVP 6 §4) -- NOT on `ToolSpec` itself:
# `tools/registry.py`'s own module docstring already rules out adding a
# searchable-description field there for a single-tool MVP (premature
# abstraction). Names come from `tools.registry.list_tool_names()` (so an
# unregistered/removed tool is never described to the classifier); this map
# supplies the natural-language description for whichever names ARE
# registered. A name with no entry here still resolves (deliberately) --
# missing prose is a documentation gap, not a reason to make an otherwise
# valid tool unreachable.
_TOOL_DESCRIPTIONS = {
    "vault_export": "export approved memories/notes to the Obsidian vault",
}


def _tool_vocabulary_line():
    from tools.registry import list_tool_names

    names = list_tool_names()
    if not names:
        return ""
    described = [
        f"{name} ({_TOOL_DESCRIPTIONS.get(name, 'no description available')})" for name in names
    ]
    return (
        "\n\nRegistered tools you may match a tool_request message against: "
        + ", ".join(described)
        + ". Set tool_name to the exact matched name; use null for every other intent."
    )


def _router_intent_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_ROUTER_INTENT_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_ROUTER_INTENT_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_ROUTER_INTENT_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _task_draft_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_TASK_DRAFT_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_TASK_DRAFT_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_TASK_DRAFT_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _parse_client_local_date(raw):
    """Lenient parse: missing OR malformed both resolve to `None` (MVP 6 §3)
    — `client_local_date` is a diagnostic-only field that must never 400 the
    whole chat POST; `_extract_task_draft` treats `None` as "no reference
    date available" and resolves only absolute dates."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _extract_task_draft(conversation, *, actor, content, client_local_date):
    """Second, focused extraction call for a `task_draft`-classified message
    (MVP 6 §3) — decides *what* the task should contain (title, optional due
    date), mirroring `memory.extraction`'s own "classify, then extract"
    shape. Returns `{"title": str, "due_date": "YYYY-MM-DD" | None}` on
    success, or `None` on any failure (`GatewayError`, `TaskDraftParseError`)
    — the caller (`classify_intent`) degrades the WHOLE turn to plain `chat`
    on `None`, exactly like a `router.intent` classification failure, rather
    than persisting a `task_draft` intent with no usable draft content.
    """
    prompt_version = _task_draft_prompt_version()
    reference_date = _parse_client_local_date(client_local_date)
    if reference_date is not None:
        reference_line = (
            f"\n\nReference date (today, in the user's own local timezone): "
            f"{reference_date.isoformat()}."
        )
    else:
        reference_line = (
            "\n\nNo reference date is available for this message. Resolve "
            "ONLY explicit absolute dates (e.g. '2026-08-01'); leave "
            "due_date null for anything relative (e.g. 'tomorrow', "
            "'Friday') rather than guessing."
        )
    messages = [
        {"role": "system", "content": prompt_version.content + reference_line},
        {"role": "user", "content": content},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="router.task_draft",
            messages=messages,
            actor=actor,
            workspace=conversation.workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return None

    try:
        parsed = parse_task_draft(result.text)
    except TaskDraftParseError:
        return None

    return {
        "title": parsed["title"],
        "due_date": parsed["due_date"].isoformat() if parsed["due_date"] else None,
    }


def classify_intent(conversation, *, actor, content, client_local_date=None):
    """Classify a user's chat message into a router intent (MVP 6 §1).

    Runs its own blocking, non-streaming `purpose="router.intent"` gateway
    call, independent of and prior to `build_gateway_stream()` — the caller
    (`ConversationMessageListView.post`) calls this before persisting the
    user `Message` row, so the classification result can be attached at
    creation time.

    Never raises for a classification failure: a `GatewayError` (no
    provider, timeout, etc.) or `IntentParseError` (malformed/unexpected
    model output) both degrade to a bare `chat` result with an empty
    `intent_payload` — a router that can't classify must never block or
    corrupt the underlying conversation. A missing/misconfigured
    `jarvis_router_intent` prompt template is a distinct, deploy-time
    misconfiguration (mirrors `assistant.context.build_context`'s treatment
    of `jarvis_system`) and is NOT caught here — it always raises
    `ImproperlyConfigured`.

    When classification decides `task_draft`, this also runs the second
    `router.task_draft` extraction call (MVP 6 §3) and folds its result into
    `intent_payload` — if THAT fails (for any of the same reasons), the
    whole turn degrades to plain `chat`, not a `task_draft` intent with no
    usable draft content.

    When classification decides `tool_request`, the named `tool_name` is
    verified against `tools.registry.list_tool_names()` (MVP 6 §4) — a
    tool_name that doesn't (or no longer) name a registered tool degrades
    the whole turn to plain `chat`, exactly like a malformed classification
    does, rather than surfacing a confirm card for something that can never
    actually be confirmed. This is resolution against the CURRENT registry
    only; no dispatch happens here — creating the `ApprovalRequest` only
    ever happens later, on confirm (see `assistant.views`).

    Returns `{"intent": Message.Intent, "intent_payload": dict}`.
    """
    from tools.registry import list_tool_names

    prompt_version = _router_intent_prompt_version()
    messages = [
        {"role": "system", "content": prompt_version.content + _tool_vocabulary_line()},
        {"role": "user", "content": content},
    ]
    gateway = InferenceGateway()
    try:
        result = gateway.complete(
            purpose="router.intent",
            messages=messages,
            actor=actor,
            workspace=conversation.workspace,
            prompt_version=prompt_version,
            stream=False,
        )
    except GatewayError:
        return {"intent": Message.Intent.CHAT, "intent_payload": {}}

    try:
        parsed = parse_intent(result.text)
    except IntentParseError:
        return {"intent": Message.Intent.CHAT, "intent_payload": {}}

    if parsed["intent"] == Message.Intent.TOOL_REQUEST:
        if parsed["tool_name"] not in list_tool_names():
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        return {
            "intent": parsed["intent"],
            "intent_payload": {"tool_name": parsed["tool_name"], "payload": {}},
        }

    if parsed["intent"] == Message.Intent.TASK_DRAFT:
        draft = _extract_task_draft(
            conversation, actor=actor, content=content, client_local_date=client_local_date
        )
        if draft is None:
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        return {"intent": Message.Intent.TASK_DRAFT, "intent_payload": draft}

    return {"intent": parsed["intent"], "intent_payload": {}}


@transaction.atomic
def create_conversation(*, workspace, actor, title=""):
    conversation = Conversation.objects.create(
        workspace=workspace,
        title=title,
        created_by=actor,
    )
    record_audit(
        action="assistant.conversation.created",
        actor=actor,
        workspace=workspace,
        resource=conversation,
    )
    return conversation


def build_gateway_stream(conversation, *, actor, content, intent=Message.Intent.CHAT):
    """Start the gateway call and return (generator, usage_holder, memory_ids,
    chunk_ids).

    `InferenceGateway.complete(stream=True)` raises `GatewayError` eagerly for
    `no_provider` (before any generator exists). That eager error is normalized
    here into a generator that raises the same exception on first iteration,
    so `stream_assistant_reply` can turn it into a single SSE `error` event
    instead of failing the request before streaming starts.

    `memory_ids`/`chunk_ids` are the ordered lists of injected memory/
    knowledge-chunk UUIDs; the caller threads them into `stream_assistant_reply`
    so they are persisted on the assistant Message row (`memories_used`/
    `chunks_used`) and surfaced in the SSE `done` event.

    `intent` is the router's classification of this turn (MVP 6 §2,
    default `chat`) — passed straight through to `build_context`, which is
    the only place it actually changes behavior (knowledge-retrieval budget
    and system-prompt framing).
    """
    gateway = InferenceGateway()
    messages, context_report, prompt_version, memory_ids, chunk_ids = build_context(
        conversation, actor=actor, new_user_content=content, intent=intent
    )
    usage_holder = {}
    try:
        gateway_stream = gateway.complete(
            purpose="chat",
            messages=messages,
            actor=actor,
            workspace=conversation.workspace,
            prompt_version=prompt_version,
            stream=True,
            context_report=context_report,
            on_usage=lambda usage: usage_holder.__setitem__("usage", usage),
        )
    except GatewayError as exc:
        gateway_stream = _eager_error_stream(exc)
    return gateway_stream, usage_holder, memory_ids, chunk_ids


def _eager_error_stream(exc):
    raise exc
    yield  # pragma: no cover — makes this a generator; unreachable


def stream_assistant_reply(
    conversation,
    gateway_stream,
    usage_holder,
    *,
    request_id,
    memory_ids=None,
    chunk_ids=None,
    draft_event=None,
):
    """`request_id` is the id `RequestIDMiddleware` assigned to the originating
    request. Django never iterates `StreamingHttpResponse.streaming_content`
    until after the middleware chain has returned — by then the middleware's
    own `finally` has already reset `core.context.request_id_var` — so this
    generator must re-publish it for the duration of its own (deferred)
    execution, or the gateway's `_record()` call records an empty request_id.

    `draft_event` (MVP 6 §3/§5) is an optional pre-built `{"type": "draft",
    ...}` payload, yielded once, ONLY when the reply completes successfully
    (the terminal event ends up being `done`, never `error`) — `done`'s own
    contract (message_id, usage, memories_used, chunks_used) is completely
    unchanged; `draft` is additive and always precedes it, never replaces it.
    """
    token = request_id_var.set(request_id)
    try:
        yield from _stream_assistant_reply(
            conversation,
            gateway_stream,
            usage_holder,
            memory_ids or [],
            chunk_ids or [],
            draft_event,
        )
    finally:
        request_id_var.reset(token)


def _stream_assistant_reply(
    conversation, gateway_stream, usage_holder, memory_ids, chunk_ids, draft_event=None
):
    message_id = uuid.uuid4()
    accumulated = []
    status = Message.Status.FAILED  # defined even if no branch below runs
    terminal_event = None  # built pre-persistence, yielded post-persistence

    try:
        for chunk in gateway_stream:
            accumulated.append(chunk)
            yield sse_event({"type": "chunk", "text": chunk})
    except GatewayError as exc:
        status = Message.Status.PARTIAL if accumulated else Message.Status.FAILED
        terminal_event = {"type": "error", "error_type": exc.error_type}
    except GeneratorExit:
        status = Message.Status.PARTIAL if accumulated else Message.Status.FAILED
        gateway_stream.close()  # close the inner gateway generator explicitly
        raise  # no new yield here
    else:
        status = Message.Status.COMPLETE
        usage = usage_holder.get("usage")
        terminal_event = {
            "type": "done",
            "message_id": str(message_id),
            "usage": {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "estimated_cost": str(usage.estimated_cost) if usage else "0",
            },
            "memories_used": memory_ids,
            "chunks_used": chunk_ids,
        }
    finally:
        # Single Message write — id is fixed up front, and (per review) the
        # row is committed here, before `done`/`error` is yielded below, so a
        # client that reacts to the terminal event can never race the write.
        _persist_assistant_message(
            conversation,
            message_id=message_id,
            content="".join(accumulated),
            status=status,
            usage=usage_holder.get("usage"),
            memory_ids=memory_ids,
            chunk_ids=chunk_ids,
        )

    if draft_event is not None and terminal_event["type"] == "done":
        yield sse_event(draft_event)
    yield sse_event(terminal_event)


@transaction.atomic
def _persist_assistant_message(
    conversation, *, message_id, content, status, usage, memory_ids, chunk_ids
):
    """The single write site for the assistant `Message` row.

    When the reply completes, this also allocates a `MemoryExtractionJob`
    (MVP 2 §3) — under a lock on `Conversation` acquired FIRST, before the
    message itself is created, in the same transaction. Lock-before-insert
    means two concurrent persists on the same conversation serialize in
    insertion order, so the extraction cursor
    (`Conversation.last_extracted_message`) can never be read stale and two
    jobs can never claim overlapping ranges. Only `COMPLETE` replies get a
    job — a partial/failed reply's content is not something worth
    distilling.
    """
    locked_conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    message = Message.objects.create(
        id=message_id,
        workspace=locked_conversation.workspace,
        conversation=locked_conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        status=status,
        model_usage=usage,
        memories_used=memory_ids,
        chunks_used=chunk_ids,
    )
    if status == Message.Status.COMPLETE:
        create_extraction_job(conversation=locked_conversation, through_message=message)
    return message


def sse_event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
