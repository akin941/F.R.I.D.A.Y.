"""OpenAI-compatible facade (Phase 4 branch 6, ADR-008 decision 6): Django
`POST /api/v1/openai/chat/completions` and `GET /api/v1/openai/models`,
public through the BFF's `/v1/...` passthrough routes (client base URL
`https://<host>/v1`), for Open WebUI-class clients.

Low trust, chat only:

- **Auth:** a PersonalAccessToken of kind `openai` only
  (`FacadeTokenAuthentication` -> CHANNEL_OPENAI). Bearer-only: a web
  session or a `cli` token never reaches the handlers. The workspace is the
  token's (`scoped_for_actor`); no request field can name another.
- **No tools, no writes, no decisions:** the router runs with
  `allowed_intents={chat, memory_query, knowledge_query}` -- no task draft,
  no tool proposal. `tools`/`functions`, a non-`none` `tool_choice`/
  `function_call`, `n>1` and a non-text `response_format` are refused
  explicitly (400 `unsupported_parameter`) rather than silently dropped.
  `temperature`/`max_tokens`/`top_p`/... are accepted and ignored: the prompt
  version's parameters govern, and provider/tier routing stays server-side.
- **Models:** `friday` -- the full pipeline (router, memory/knowledge
  context, persisted in a server-side conversation, memory extraction as
  `pending_approval` suggestions); `friday-lite` -- ephemeral: the client's
  messages go to the gateway as they are (purpose `facade.ephemeral`), no
  Friday context, nothing persisted, no extraction. It is the target for a
  client's background "task model" (titles, tags, follow-ups).
- **Accounting:** every call goes through `InferenceGateway` and writes
  ModelUsage (`router.intent` + `chat`, or `facade.ephemeral`) with
  `context_report.channel="openai"` and the model alias.
- **Limits:** FACADE_RATE per token (fail-closed throttle), at most
  FACADE_MAX_CONCURRENT_STREAMS completions in flight per token,
  FACADE_MAX_REQUEST_CHARS. Errors use OpenAI's `{"error": {message, type,
  param, code}}` shape; a 429 carries `Retry-After`.
"""

import json
import logging
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, set_rollback

from accounts.authentication import FacadeTokenAuthentication
from accounts.models import PersonalAccessToken
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from assistant.models import Conversation, Message
from assistant.services import (
    build_gateway_stream,
    classify_intent,
    iter_assistant_reply_events,
)
from core.channels import CHANNEL_OPENAI, request_channel, scoped_for_actor
from core.context import request_id_var
from core.throttling import FacadeTokenThrottle, ThrottleBackendUnavailable
from integrations.facade_mapping import resolve_conversation
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

MODEL_FRIDAY = "friday"
MODEL_LITE = "friday-lite"
# `created` of the model objects: a fixed date (the facade's release), not
# a per-request timestamp, so `/v1/models` is stable.
_MODELS_CREATED = 1790467200  # 2026-09-27T00:00:00Z
FACADE_MODELS = (MODEL_FRIDAY, MODEL_LITE)
# Pre-rename model ids (Jarvis -> Friday, 2026-11-20): still accepted so a
# client configured before the rename keeps working, never listed.
LEGACY_MODEL_ALIASES = {"jarvis": MODEL_FRIDAY, "jarvis-lite": MODEL_LITE}

# The router's vocabulary on this channel: no task drafts, no tool requests.
FACADE_INTENTS = frozenset(
    {Message.Intent.CHAT, Message.Intent.MEMORY_QUERY, Message.Intent.KNOWLEDGE_QUERY}
)
EPHEMERAL_PURPOSE = "facade.ephemeral"
CONVERSATION_HEADER = "X-Friday-Conversation-Id"
# Accepted on requests from clients configured before the rename.
LEGACY_CONVERSATION_HEADER = "X-Jarvis-Conversation-Id"

# Roles a client may send. Only `user` turns matter for `friday`;
# `friday-lite` forwards system/developer/user/assistant text as is.
_KNOWN_ROLES = {"system", "developer", "user", "assistant", "tool", "function"}
_LITE_ROLES = {"system": "system", "developer": "system", "user": "user", "assistant": "assistant"}

_STREAM_SLOT_KEY = "openai-facade:streams:%s"


class FacadeError(exceptions.APIException):
    """An error in OpenAI's wire shape. `openai_type`/`openai_code`/`param`
    fill `{"error": {"message", "type", "param", "code"}}` -- codes here are
    OpenAI-client vocabulary, not the Friday API's `core.error_codes`
    (nothing in the Friday UI ever reads a facade response)."""

    def __init__(
        self, status_code, message, *, openai_type="invalid_request_error", code=None, param=None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.openai_type = openai_type
        self.openai_code = code
        self.param = param


def _unsupported(param, message=None):
    return FacadeError(
        400,
        message or f"'{param}' is not supported by Friday (chat only, no tools).",
        code="unsupported_parameter",
        param=param,
    )


def _invalid(message, param=None, code=None):
    return FacadeError(400, message, code=code, param=param)


def _error_body(message, openai_type, code=None, param=None):
    return {"error": {"message": message, "type": openai_type, "param": param, "code": code}}


def _openai_shape(exc):
    """(status, body) for any exception reaching the facade's handler."""
    if isinstance(exc, FacadeError):
        return exc.status_code, _error_body(
            str(exc.detail), exc.openai_type, exc.openai_code, exc.param
        )
    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        return 401, _error_body(
            "Invalid, expired or revoked API key.", "invalid_request_error", "invalid_api_key"
        )
    if isinstance(exc, exceptions.PermissionDenied):
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else "permission_denied"
        return 403, _error_body(str(exc.detail), "permission_error", code)
    if isinstance(exc, exceptions.Throttled):
        return 429, _error_body(
            "Rate limit reached for this API key.", "rate_limit_error", "rate_limit_exceeded"
        )
    if isinstance(exc, ThrottleBackendUnavailable):
        return 503, _error_body(
            "Rate-limit backend unavailable; try again shortly.",
            "server_error",
            "service_unavailable",
        )
    if isinstance(exc, exceptions.ParseError):
        return 400, _error_body(
            "The request body is not valid JSON.", "invalid_request_error", "invalid_json"
        )
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return 415, _error_body(
            "Send the request body as application/json.",
            "invalid_request_error",
            "unsupported_media_type",
        )
    if isinstance(exc, exceptions.MethodNotAllowed):
        return 405, _error_body(str(exc.detail), "invalid_request_error", "method_not_allowed")
    return exc.status_code, _error_body(str(exc.detail), "invalid_request_error", None)


def facade_exception_handler(exc, context):
    """DRF exception handler for the facade views: OpenAI's error shape,
    `WWW-Authenticate` on a 401, `Retry-After` on a 429. Anything that is
    not an APIException stays a server error (None -> re-raised by DRF)."""
    if not isinstance(exc, exceptions.APIException):
        return None
    status_code, body = _openai_shape(exc)
    set_rollback()
    headers = {}
    auth_header = getattr(exc, "auth_header", None)
    if auth_header:
        headers["WWW-Authenticate"] = auth_header
    wait = getattr(exc, "wait", None)
    if isinstance(exc, exceptions.Throttled) and wait is not None:
        headers["Retry-After"] = str(max(1, int(wait)))
    elif isinstance(exc, FacadeError) and status_code == 429:
        headers["Retry-After"] = "1"
    return Response(body, status=status_code, headers=headers)


class IsFacadeChannel(BasePermission):
    """Defense in depth next to the authenticator: only CHANNEL_OPENAI, and
    never an agent service account."""

    message = "This endpoint accepts only an OpenAI-compatible API key of a human user."
    code = "permission_denied"

    def has_permission(self, request, view):
        return request_channel(request) == CHANNEL_OPENAI and not getattr(
            request.user, "is_agent", False
        )


class _FacadeView(APIView):
    authentication_classes = [FacadeTokenAuthentication]
    permission_classes = [IsAuthenticated, IsFacadeChannel]
    parser_classes = [JSONParser]
    # Plain JSON: the error body is OpenAI's shape, never the Friday API's
    # `code` backstop (core.api_errors.CodedJSONRenderer).
    renderer_classes = [JSONRenderer]

    def get_exception_handler(self):
        return facade_exception_handler

    def perform_content_negotiation(self, request, force=False):
        # Clients may send `Accept: text/event-stream`; streaming responses
        # are built by hand, every other body is JSON.
        return (JSONRenderer(), "application/json")


def _model_object(model_id):
    return {"id": model_id, "object": "model", "created": _MODELS_CREATED, "owned_by": "friday"}


class FacadeModelsView(_FacadeView):
    def get(self, request):
        return JsonResponse(
            {"object": "list", "data": [_model_object(model) for model in FACADE_MODELS]}
        )


# --- request parsing ---


def _content_text(content, index):
    """A message's text: a string, or the text parts of a content-part list.
    Image/audio/file parts are refused (chat text only)."""
    param = f"messages[{index}].content"
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if not isinstance(part, dict):
                raise _invalid("Content parts must be objects.", param)
            if part.get("type") != "text":
                raise _unsupported(param, "Only text content parts are supported.")
            text = part.get("text")
            if not isinstance(text, str):
                raise _invalid("A text content part needs a string 'text'.", param)
            texts.append(text)
        return "\n".join(texts)
    raise _invalid("Message content must be a string or a list of content parts.", param)


def _parse_messages(body):
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _invalid("'messages' must be a non-empty array.", "messages")
    parsed = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise _invalid("Each message must be an object.", f"messages[{index}]")
        role = message.get("role")
        if role not in _KNOWN_ROLES:
            raise _invalid(f"Unknown message role {role!r}.", f"messages[{index}].role")
        parsed.append((role, _content_text(message.get("content"), index)))
    return parsed


def _reject_unsupported(body):
    for param in ("tools", "functions"):
        if body.get(param):
            raise _unsupported(param)
    for param in ("tool_choice", "function_call"):
        if body.get(param) not in (None, "none"):
            raise _unsupported(param)
    if body.get("n") not in (None, 1):
        raise _unsupported("n", "Only n=1 is supported.")
    response_format = body.get("response_format")
    if response_format is not None:
        kind = response_format.get("type") if isinstance(response_format, dict) else None
        if kind != "text":
            raise _unsupported("response_format", "Only the 'text' response format is supported.")


def _parse_stream_flags(body):
    stream = body.get("stream", False)
    if stream is None:
        stream = False
    if not isinstance(stream, bool):
        raise _invalid("'stream' must be a boolean.", "stream")
    options = body.get("stream_options") or {}
    if not isinstance(options, dict):
        raise _invalid("'stream_options' must be an object.", "stream_options")
    return stream, bool(stream and options.get("include_usage"))


def _check_length(chars):
    if chars > settings.FACADE_MAX_REQUEST_CHARS:
        raise _invalid(
            f"The request is too long ({chars} characters; the limit is "
            f"{settings.FACADE_MAX_REQUEST_CHARS}).",
            "messages",
            "context_length_exceeded",
        )


# --- per-token stream slots ---


class _StreamSlot:
    """One of the token's FACADE_MAX_CONCURRENT_STREAMS in-flight
    completions, counted in the shared cache (Redis in the daily stack).
    Released exactly once, when the response body is done or the client
    went away; the key's TTL bounds a slot a dead worker never released."""

    def __init__(self, key):
        self.key = key
        self._released = False

    def release(self):
        if self._released:
            return
        self._released = True
        try:
            cache.decr(self.key)
        except ValueError:
            pass  # expired meanwhile: nothing left to release
        except Exception:
            logger.exception("Could not release a facade stream slot")


def acquire_stream_slot(token_id):
    key = _STREAM_SLOT_KEY % token_id
    ttl = settings.FACADE_STREAM_SLOT_TTL_SECONDS
    try:
        cache.add(key, 0, timeout=ttl)
        try:
            count = cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.add(key, 0, timeout=ttl)
            count = cache.incr(key)
        cache.touch(key, ttl)
    except Exception as exc:  # fail closed, like the throttles
        logger.exception("Facade stream-slot backend unavailable")
        raise ThrottleBackendUnavailable() from exc
    slot = _StreamSlot(key)
    if count > settings.FACADE_MAX_CONCURRENT_STREAMS:
        slot.release()
        raise FacadeError(
            429,
            "Too many concurrent requests for this API key.",
            openai_type="rate_limit_error",
            code="concurrency_limit_exceeded",
        )
    return slot


# --- wire format ---


def _sse(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


_SSE_DONE = b"data: [DONE]\n\n"


def _openai_usage(usage):
    prompt = usage.get("input_tokens", 0) if usage else 0
    completion = usage.get("output_tokens", 0) if usage else 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _gateway_error_body(error_type):
    return _error_body(
        f"The model provider failed ({error_type}).", "server_error", error_type or "error"
    )


class _Completion:
    def __init__(self, completion_id, model):
        self.id = completion_id
        self.model = model
        self.created = int(time.time())

    def chunk(self, delta, finish_reason=None, include_usage=False):
        payload = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if include_usage:
            payload["usage"] = None
        return payload

    def usage_chunk(self, usage):
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [],
            "usage": _openai_usage(usage),
        }

    def full(self, text, usage):
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": _openai_usage(usage),
        }


def _stream_body(events, completion, include_usage, slot):
    """Structured reply events -> OpenAI `chat.completion.chunk` SSE: a
    role chunk, content chunks, a `finish_reason: "stop"` chunk, the usage
    chunk when asked for, `[DONE]`. A gateway error mid-stream is one
    `data: {"error": {...}}` line, then `[DONE]` (the `friday` reply is
    persisted `partial`/`failed` by the assistant pipeline)."""
    try:
        yield _sse(completion.chunk({"role": "assistant", "content": ""}, None, include_usage))
        for event in events:
            kind = event["type"]
            if kind == "chunk":
                yield _sse(completion.chunk({"content": event["text"]}, None, include_usage))
            elif kind == "done":
                yield _sse(completion.chunk({}, "stop", include_usage))
                if include_usage:
                    yield _sse(completion.usage_chunk(event["usage"]))
            elif kind == "error":
                yield _sse(_gateway_error_body(event["error_type"]))
            # Any other event (a `draft`) cannot occur on this channel:
            # FACADE_INTENTS excludes both draft-producing intents.
        yield _SSE_DONE
    finally:
        events.close()
        slot.release()


class _StreamingBody:
    """The streaming response body. Django registers `close()` as a
    resource closer, so the stream slot is released even when the server
    closes a response it never started iterating (closing a generator that
    never ran skips its `finally`)."""

    def __init__(self, events, completion, include_usage, slot):
        self._events = events
        self._slot = slot
        self._generator = _stream_body(events, completion, include_usage, slot)

    def __iter__(self):
        return self._generator

    def close(self):
        try:
            self._generator.close()
        finally:
            self._events.close()
            self._slot.release()


def _collect(events):
    """Drain the events for a non-streaming response: (text, usage) or a
    FacadeError for a gateway failure."""
    texts = []
    try:
        for event in events:
            if event["type"] == "chunk":
                texts.append(event["text"])
            elif event["type"] == "done":
                return "".join(texts), event["usage"]
            elif event["type"] == "error":
                error_type = event["error_type"]
                raise FacadeError(
                    503 if error_type == "no_provider" else 502,
                    f"The model provider failed ({error_type}).",
                    openai_type="server_error",
                    code=error_type,
                )
    finally:
        events.close()
    raise FacadeError(502, "The reply ended unexpectedly.", openai_type="server_error")


def _ephemeral_events(gateway_stream, usage_holder, *, request_id):
    """`friday-lite`'s events, in the same shape as the assistant pipeline's
    (`chunk`s, then `done` or `error`) but nothing is persisted."""
    token = request_id_var.set(request_id)
    try:
        try:
            for text in gateway_stream:
                yield {"type": "chunk", "text": text}
        except GatewayError as exc:
            yield {"type": "error", "error_type": exc.error_type}
            return
        except GeneratorExit:
            gateway_stream.close()
            raise
        usage = usage_holder.get("usage")
        yield {
            "type": "done",
            "usage": {
                "input_tokens": usage.input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
            },
        }
    finally:
        request_id_var.reset(token)


def _raise_eagerly(exc):
    raise exc
    yield  # pragma: no cover -- makes this a generator


class FacadeChatCompletionsView(_FacadeView):
    throttle_classes = [FacadeTokenThrottle]

    def post(self, request):
        body = request.data
        if not isinstance(body, dict):
            raise _invalid("The request body must be a JSON object.")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            raise _invalid("'model' is required.", "model")
        model = LEGACY_MODEL_ALIASES.get(model, model)
        if model not in FACADE_MODELS:
            raise FacadeError(
                404,
                f"The model '{model}' does not exist. Available: {', '.join(FACADE_MODELS)}.",
                code="model_not_found",
                param="model",
            )
        _reject_unsupported(body)
        stream, include_usage = _parse_stream_flags(body)
        messages = _parse_messages(body)
        if model == MODEL_LITE:
            plan = _plan_lite(messages)
        else:
            plan = _plan_friday(request, messages)

        # The token's own workspace, re-checked every request: a token whose
        # user lost membership is a 404, never a fallback.
        workspace = scoped_for_actor(request, Workspace.objects).first()
        token = PersonalAccessToken.objects.filter(
            pk=request.auth.token_id, user=request.user
        ).first()
        if workspace is None or token is None:
            raise FacadeError(404, "The API key's workspace is not available.", code="not_found")

        # Validation is done without a slot; the slot covers the gateway work.
        slot = acquire_stream_slot(request.auth.token_id)
        try:
            if model == MODEL_LITE:
                events, completion, headers = _start_lite(request, plan, workspace)
            else:
                events, completion, headers = _start_friday(request, plan, workspace, token)
        except BaseException:
            slot.release()
            raise

        if stream:
            response = StreamingHttpResponse(
                _StreamingBody(events, completion, include_usage, slot),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
        else:
            try:
                text, usage = _collect(events)
            finally:
                slot.release()
            response = JsonResponse(completion.full(text, usage))
        for name, value in headers.items():
            response[name] = value
        return response


# --- friday: the full, persisted pipeline ---


def _plan_friday(request, messages):
    if messages[-1][0] != "user":
        raise _invalid("The last message must have role 'user'.", "messages")
    user_turns = [text for role, text in messages if role == "user"]
    if not user_turns[-1].strip():
        raise _invalid("The last user message is empty.", "messages")
    _check_length(len(user_turns[-1]))
    return {"user_turns": user_turns, "pinned": _pinned_conversation(request)}


def _pinned_conversation(request):
    raw = request.headers.get(CONVERSATION_HEADER) or request.headers.get(
        LEGACY_CONVERSATION_HEADER
    )
    if not raw:
        return None
    try:
        conversation_id = uuid.UUID(raw)
    except ValueError as exc:
        raise _invalid(f"{CONVERSATION_HEADER} must be a UUID.") from exc
    conversations = scoped_for_actor(request, Conversation.objects)
    conversation = conversations.filter(id=conversation_id).first()
    if conversation is None:
        raise FacadeError(404, "Conversation not found.", code="conversation_not_found")
    return conversation


def _start_friday(request, plan, workspace, token):
    """Map the turn to its conversation, then run the web chat's pipeline
    (classify -> context -> persist the user turn -> reply events) on
    CHANNEL_OPENAI with the chat-only router."""
    user = request.user
    user_turns = plan["user_turns"]
    content = user_turns[-1]
    resolution = resolve_conversation(
        actor=user,
        token=token,
        workspace=workspace,
        user_turns=user_turns,
        pinned=plan["pinned"],
    )
    conversation = resolution.conversation
    classification = classify_intent(
        conversation,
        actor=user,
        content=content,
        allowed_intents=FACADE_INTENTS,
        context_report={"channel": CHANNEL_OPENAI, "model": MODEL_FRIDAY},
        # Connector reads never run over the facade (ADR-010 decision 4):
        # FACADE_INTENTS excludes connector_read, and no read tool is ever
        # registered for this channel either.
        channel=CHANNEL_OPENAI,
    )
    gateway_stream, usage_holder, memory_ids, chunk_ids, _overlay = build_gateway_stream(
        conversation,
        actor=user,
        content=content,
        channel=CHANNEL_OPENAI,
        intent=classification["intent"],
        report_extra={"model": MODEL_FRIDAY},
    )
    Message.objects.create(
        id=resolution.user_message_id,
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content=content,
        status=Message.Status.COMPLETE,
        created_by=user,
        intent=classification["intent"],
        intent_payload=classification["intent_payload"],
        channel=Message.Channel.OPENAI,
    )
    reply_id = uuid.uuid4()
    events = iter_assistant_reply_events(
        conversation,
        gateway_stream,
        usage_holder,
        request_id=request.request_id,
        message_id=reply_id,
        memory_ids=memory_ids,
        chunk_ids=chunk_ids,
        channel=Message.Channel.OPENAI,
    )
    headers = {CONVERSATION_HEADER: str(conversation.id)}
    return events, _Completion(f"chatcmpl-{reply_id}", MODEL_FRIDAY), headers


# --- friday-lite: ephemeral, nothing persisted ---


def _plan_lite(messages):
    forwarded = [
        {"role": _LITE_ROLES[role], "content": text}
        for role, text in messages
        if role in _LITE_ROLES
    ]
    if not any(m["role"] == "user" for m in forwarded):
        raise _invalid("At least one user message is required.", "messages")
    _check_length(sum(len(m["content"]) for m in forwarded))
    return {"messages": forwarded}


def _start_lite(request, plan, workspace):
    forwarded = plan["messages"]
    usage_holder = {}
    try:
        gateway_stream = InferenceGateway().complete(
            purpose=EPHEMERAL_PURPOSE,
            messages=forwarded,
            actor=request.user,
            workspace=workspace,
            stream=True,
            context_report={
                "channel": CHANNEL_OPENAI,
                "model": MODEL_LITE,
                "message_count": len(forwarded),
                "total_characters": sum(len(m["content"]) for m in forwarded),
            },
            on_usage=lambda usage: usage_holder.__setitem__("usage", usage),
        )
    except GatewayError as exc:
        gateway_stream = _raise_eagerly(exc)
    events = _ephemeral_events(gateway_stream, usage_holder, request_id=request.request_id)
    return events, _Completion(f"chatcmpl-{uuid.uuid4()}", MODEL_LITE), {}
