"""Chat orchestration: gateway call + SSE framing (ADR-003 — ai_core is the
single door for LLM traffic; this module never talks to a provider SDK)."""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from ai_core.exceptions import GatewayError, NoProviderAvailable
from ai_core.gateway import InferenceGateway
from ai_core.models import PromptTemplate
from assistant.context import build_context, render_connector_block
from assistant.intent import IntentParseError, parse_intent
from assistant.models import Conversation, Message
from assistant.task_draft import TaskDraftParseError, parse_task_draft
from audit.services import record_audit
from core.context import request_id_var
from memory.jobs import create_extraction_job
from memory.services import privatize_conversation_memories

_ROUTER_INTENT_PROMPT_NAME = "friday_router_intent"
_TASK_DRAFT_PROMPT_NAME = "friday_router_task_draft"
_CONNECTOR_ARGS_PROMPT_NAME = "friday_router_connector_args"
_NODE_ACTION_ARGS_PROMPT_NAME = "friday_router_node_action_args"
# Phase 10 D13: the one gated tool whose draft needs an argument step.
NODE_ACTION_TOOL = "node_action"
# The spoken/visible hint of a node action proposal (D13): where the owner
# acts -- never "done", never a question a spoken "evet" could answer.
NODE_ACTION_WHERE_TO_ACT = "Onay ekranda, sonra Mac'te bir tık"

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
    # Phase 10 D13: proposals only -- approved on screen, confirmed on the Mac.
    NODE_ACTION_TOOL: (
        "run an action on the owner's paired Mac (Friday Node): run a repo's "
        "tests, lint, typecheck, build or format check, open or focus an app, "
        "or run a local recipe; it is only proposed here and approved on screen"
    ),
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


def _read_vocabulary_line(read_specs):
    """The connector reads available this turn (Phase 5 branch 4, brief D4):
    Friday-authored names and descriptions only -- never a server's own
    tool description. Empty when nothing is available, so the router never
    hears of `connector_read` for a workspace/channel without a read."""
    if not read_specs:
        return ""
    described = [f"{spec.name} ({spec.description})" for spec in read_specs]
    return (
        "\n\nConnector read tools available for this message: "
        + ", ".join(described)
        + ". When the latest message asks for information one of these tools reads, answer "
        '{"intent": "connector_read", "tool_name": "<exact tool name>"} instead; '
        "connector_read is valid only with one of these names."
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


def _connector_args_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_CONNECTOR_ARGS_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_CONNECTOR_ARGS_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_CONNECTOR_ARGS_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def _node_action_args_prompt_version():
    try:
        template = PromptTemplate.objects.get(name=_NODE_ACTION_ARGS_PROMPT_NAME)
    except PromptTemplate.DoesNotExist as exc:
        raise ImproperlyConfigured(
            f"{_NODE_ACTION_ARGS_PROMPT_NAME} prompt template is not configured"
        ) from exc
    if template.active_version_id is None:
        raise ImproperlyConfigured(
            f"{_NODE_ACTION_ARGS_PROMPT_NAME} prompt template has no active version"
        )
    return template.active_version


def conversation_has_personal_data(conversation):
    """`Conversation.personal_data`, read fresh from the database (D5): the
    flag can be set mid-turn by a personal read, so a cached instance is
    never trusted. Every gateway call of a conversation passes this as
    `personal_data=`."""
    return bool(
        Conversation.objects.filter(pk=conversation.pk)
        .values_list("personal_data", flat=True)
        .first()
    )


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


def _extract_task_draft(
    conversation,
    *,
    actor,
    content,
    client_local_date,
    context_report=None,
    personal_data=False,
):
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
            context_report=context_report,
            personal_data=personal_data,
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


def _allowed_intents_line(allowed):
    names = ", ".join(sorted(allowed))
    return (
        f"\n\nOnly these intents are available on this channel: {names}. "
        "Classify every other message as chat."
    )


def classify_intent(
    conversation,
    *,
    actor,
    content,
    client_local_date=None,
    allowed_intents=None,
    context_report=None,
    channel=None,
    call_context=None,
):
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
    `friday_router_intent` prompt template is a distinct, deploy-time
    misconfiguration (mirrors `assistant.context.build_context`'s treatment
    of `friday_system`) and is NOT caught here — it always raises
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

    `allowed_intents` (Phase 4 branch 6) narrows the router for a
    low-trust, chat-only channel (the OpenAI-compatible facade passes
    `{chat, memory_query, knowledge_query}`): the tool vocabulary is left
    out of the prompt unless `tool_request` is allowed, the prompt names the
    allowed set, and a classification outside it degrades to plain `chat`
    BEFORE any task-draft extraction -- no draft, no tool proposal, no
    second gateway call. `chat` is always allowed. Default `None`: every
    intent (web, CLI, Telegram).

    `context_report` (optional) is recorded on this call's ModelUsage row
    (and the task-draft call's), e.g. the facade's channel and model alias.

    `channel` (Phase 5 branch 4, brief D4) is the request's channel. Only
    with one does the router see the connector-read vocabulary: the read
    tools allowed over that channel whose connector has an enabled binding
    in this workspace (`tools.registry.list_read_tools`). A `connector_read`
    naming anything else degrades to chat. `connector_read` also obeys
    `allowed_intents` -- the facade never allows it. The router sees only
    this message, never history, so connector text can never reach it.

    Every call is made with `personal_data` read fresh off the conversation
    (D5): a conversation that already carries personal-class data routes
    even its router calls to allowed providers only. The router call that
    precedes a first personal read runs without it -- it carries only the
    owner's message and tool names, never connector content.

    `call_context` (Phase 13 D6, a `tools.registry.ToolCallContext`): the
    owner's own message and, for a `node_voice` turn, the speaking node --
    what the "which PC" resolver of a node action proposal decides by. When
    it cannot decide (or the model's node disagrees with the owner's words)
    the result is plain `chat` carrying `reply_text`, the Friday-authored
    ask/refusal the view answers with (no chat completion, no draft).

    Returns `{"intent": Message.Intent, "intent_payload": dict}` (plus
    `reply_text` in that one case).
    """
    from tools.registry import list_read_tools, list_tool_names

    allowed = None
    if allowed_intents is not None:
        allowed = frozenset(allowed_intents) | {Message.Intent.CHAT}
    read_specs = []
    if channel and (allowed is None or Message.Intent.CONNECTOR_READ in allowed):
        read_specs = list_read_tools(workspace=conversation.workspace, channel=channel)
    personal_data = conversation_has_personal_data(conversation)
    prompt_version = _router_intent_prompt_version()
    system_prompt = prompt_version.content
    if allowed is None or Message.Intent.TOOL_REQUEST in allowed:
        system_prompt += _tool_vocabulary_line()
    system_prompt += _read_vocabulary_line(read_specs)
    if allowed is not None:
        system_prompt += _allowed_intents_line(allowed)
    messages = [
        {"role": "system", "content": system_prompt},
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
            context_report=context_report,
            personal_data=personal_data,
        )
    except GatewayError:
        return {"intent": Message.Intent.CHAT, "intent_payload": {}}

    try:
        parsed = parse_intent(result.text)
    except IntentParseError:
        return {"intent": Message.Intent.CHAT, "intent_payload": {}}

    if allowed is not None and parsed["intent"] not in allowed:
        return {"intent": Message.Intent.CHAT, "intent_payload": {}}

    if parsed["intent"] == Message.Intent.CONNECTOR_READ:
        if parsed["tool_name"] not in {spec.name for spec in read_specs}:
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        return {
            "intent": Message.Intent.CONNECTOR_READ,
            "intent_payload": {"tool_name": parsed["tool_name"]},
        }

    if parsed["intent"] == Message.Intent.TOOL_REQUEST:
        if parsed["tool_name"] not in list_tool_names():
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        if parsed["tool_name"] == NODE_ACTION_TOOL:
            proposal = propose_node_action(
                conversation,
                actor=actor,
                content=content,
                channel=channel,
                context_report=context_report,
                personal_data=personal_data,
                call_context=call_context,
            )
            if proposal is None:
                return {"intent": Message.Intent.CHAT, "intent_payload": {}}
            if proposal.get("reply_text"):
                # Phase 13 D6: "which PC" could not be decided -- no draft.
                return {
                    "intent": Message.Intent.CHAT,
                    "intent_payload": {},
                    "reply_text": proposal["reply_text"],
                }
            return {
                "intent": Message.Intent.TOOL_REQUEST,
                "intent_payload": {
                    "tool_name": NODE_ACTION_TOOL,
                    "payload": proposal["payload"],
                    "action_class": proposal["action_class"],
                },
            }
        try:
            payload = draft_tool_payload(
                conversation,
                actor=actor,
                content=content,
                tool_name=parsed["tool_name"],
                context_report=context_report,
            )
        except DraftRefused as refused:
            # Phase 13 D13: e.g. the model's entity is not the one the owner
            # named (`target_mismatch`) -- no draft, Friday's own reply.
            return {
                "intent": Message.Intent.CHAT,
                "intent_payload": {},
                "reply_text": refused.reply,
            }
        if payload is None:
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        return {
            "intent": parsed["intent"],
            "intent_payload": {"tool_name": parsed["tool_name"], "payload": payload},
        }

    if parsed["intent"] == Message.Intent.TASK_DRAFT:
        draft = _extract_task_draft(
            conversation,
            actor=actor,
            content=content,
            client_local_date=client_local_date,
            context_report=context_report,
            personal_data=personal_data,
        )
        if draft is None:
            return {"intent": Message.Intent.CHAT, "intent_payload": {}}
        return {"intent": Message.Intent.TASK_DRAFT, "intent_payload": draft}

    return {"intent": parsed["intent"], "intent_payload": {}}


# --- Node action proposals (Phase 10 D13, branch 4) ---

_NODE_ACTION_ARGS_KEYS = frozenset({"node_id", "capability", "args"})


class NodeActionArgsParseError(Exception):
    """`router.node_action_args` output was not one allowed object."""


def parse_node_action_args(raw_text):
    """Strict parse of the argument step: exactly one JSON object with
    exactly `node_id`, `capability` and `args`. All three null means "no
    action fits" (returns None). Whether the values are valid is the server
    contract's business (`validate_proposal`), not this parser's."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise NodeActionArgsParseError("invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _NODE_ACTION_ARGS_KEYS:
        raise NodeActionArgsParseError("unexpected shape")
    if all(payload[key] is None for key in _NODE_ACTION_ARGS_KEYS):
        return None
    if not isinstance(payload["node_id"], str) or not isinstance(payload["capability"], str):
        raise NodeActionArgsParseError("node_id and capability must be strings")
    if not isinstance(payload["args"], dict):
        raise NodeActionArgsParseError("args must be an object")
    return payload


def _node_action_targets_block(targets):
    return (
        "\n\nNodes and actions available for this message (server contracts, data):\n"
        + json.dumps(targets, ensure_ascii=False, sort_keys=True)
    )


def propose_node_action(
    conversation,
    *,
    actor,
    content,
    channel,
    context_report=None,
    personal_data=False,
    call_context=None,
):
    """The D13 argument step of a `node_action` tool request: one cheap
    `router.node_action_args` call that sees only the owner's message and
    the server-side targets (the actor's own nodes, their invocable action
    contracts, the workspace's 8A repo prefixes), then the server contract
    over its untrusted output (`tools.node_actions.validate_proposal`
    through the proposal port). Returns `{"payload": {node_id, capability,
    args, source_message_id}, "action_class"}` or None -- the turn then
    degrades to plain chat. Web (and node voice) channel only: the card click that requests
    (or, for confirm class, invokes) the action exists only there. Creates
    nothing: the card click goes through `create_request` (gate class) or
    the actions view (confirm class), which re-validate everything.
    Phase 11 D4: the node voice channel proposes too ("testleri çalıştır"
    said to the Mac) -- still a draft card, clicked on screen; the trust
    check caps it at `warn` (`voice_evidence`).

    Phase 13 D6: the validator also resolves "which PC" from `call_context`
    (the owner's message, the speaking node); the targets block names each
    node's name, room, role and platform as data (the prompt text, purpose
    and prompt version are unchanged). A validator refusal returns
    `{"reply_text": ..., "refusal": code}`: no draft, the turn answers with
    that Friday-authored text."""
    from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
    from tools.node_action_ports import proposal_source, proposal_validator

    if channel not in (CHANNEL_WEB, CHANNEL_NODE_VOICE):
        return None
    source, validator = proposal_source(), proposal_validator()
    if source is None or validator is None:
        return None
    workspace = conversation.workspace
    targets = source(workspace=workspace, actor=actor)
    if not targets:
        return None
    prompt_version = _node_action_args_prompt_version()
    try:
        result = InferenceGateway().complete(
            purpose="router.node_action_args",
            messages=[
                {
                    "role": "system",
                    "content": prompt_version.content + _node_action_targets_block(targets),
                },
                {"role": "user", "content": content},
            ],
            actor=actor,
            workspace=workspace,
            prompt_version=prompt_version,
            stream=False,
            context_report={**(context_report or {}), "node_action_targets": len(targets)},
            personal_data=personal_data,
        )
    except GatewayError:
        return None
    try:
        proposal = parse_node_action_args(result.text)
    except NodeActionArgsParseError:
        return None
    if proposal is None:
        return None
    if call_context is None:
        from tools.registry import ToolCallContext

        call_context = ToolCallContext(source_text=content)
    cleaned = validator(workspace=workspace, actor=actor, proposal=proposal, context=call_context)
    if not cleaned:
        return None
    if "refusal" in cleaned:
        return {"reply_text": str(cleaned.get("reply") or ""), "refusal": cleaned["refusal"]}
    return {
        "payload": {
            "node_id": cleaned["node_id"],
            "capability": cleaned["capability"],
            "args": cleaned["args"],
            # Filled with the persisted user message's id by the view.
            "source_message_id": "",
        },
        "action_class": cleaned["class"],
    }


# --- Connector reads (Phase 5 branch 4, ADR-010 decisions 4-5, brief D4/D5) ---

# SSE `error` event types a connector turn can end with instead of a reply
# (no chat completion runs): the frontend catalog has a Turkish message for
# each.
CONNECTOR_ERROR_LOCAL_MODEL_REQUIRED = "connector_local_model_required"
CONNECTOR_ERROR_INVALID_ARGUMENTS = "connector_invalid_arguments"
CONNECTOR_ERROR_READ_FAILED = "connector_read_failed"
# A native bridge's credential was rejected (Google `invalid_grant`): the
# owner reconnects on the host (`manage.py connect_google`, brief D9/R4).
CONNECTOR_ERROR_RECONNECT_REQUIRED = "connector_reconnect_required"
_INVALID_ARGUMENT_CODES = frozenset({"invalid_arguments", "out_of_scope"})
# Bound on the router's argument object (keys), before the serializer runs.
_MAX_CONNECTOR_ARGS = 20


class ConnectorArgsParseError(Exception):
    """`router.connector_args` output was not one JSON object."""


def parse_connector_args(raw_text):
    """Strict parse of the argument-extraction output: exactly one JSON
    object of at most `_MAX_CONNECTOR_ARGS` keys. What the values may be is
    the read tool's serializer's business, not this parser's."""
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ConnectorArgsParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or len(payload) > _MAX_CONNECTOR_ARGS:
        raise ConnectorArgsParseError("arguments must be one small JSON object")
    return payload


def _describe_arguments(serializer_class):
    """The argument schema shown to `router.connector_args`: field names,
    types and requiredness from the Friday-side serializer (never a server
    `inputSchema` or description)."""
    lines = []
    for name, field_obj in serializer_class().fields.items():
        parts = [type(field_obj).__name__.replace("Field", "").lower() or "value"]
        parts.append("required" if field_obj.required else "optional")
        choices = getattr(field_obj, "choices", None)
        if choices:
            parts.append("one of: " + ", ".join(str(c) for c in list(choices)[:20]))
        help_text = getattr(field_obj, "help_text", None)
        line = f"- {name} ({'; '.join(parts)})"
        if help_text:
            line += f": {help_text}"
        lines.append(line)
    return "\n".join(lines) or "- (no arguments)"


def _extract_connector_args(
    conversation, *, actor, content, spec, personal_data, context_report=None
):
    """`router.connector_args` (D4): the read tool is already named, so its
    data class is known -- a `personal` tool's call carries
    `personal_data=True` (D5). Sees only the owner's message and the
    argument schema, never connector content. Returns the argument dict, or
    raises `GatewayError` / `ConnectorArgsParseError`."""
    prompt_version = _connector_args_prompt_version()
    system_prompt = (
        prompt_version.content
        + f"\n\nTool: {spec.name} ({spec.description})\nArguments:\n"
        + _describe_arguments(spec.argument_serializer)
    )
    result = InferenceGateway().complete(
        purpose="router.connector_args",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        actor=actor,
        workspace=conversation.workspace,
        prompt_version=prompt_version,
        stream=False,
        context_report={**(context_report or {}), "connector_tool": spec.name},
        personal_data=personal_data,
    )
    return parse_connector_args(result.text)


class DraftRefused(Exception):
    """A gated tool's router-extracted draft failed its server-side draft
    check (Phase 13 D13, `tools.home_ports.check_draft`): no card; the turn
    answers with `reply`, Friday's own text (e.g. `target_mismatch`: the
    model's `entity_id` is not the device the owner named)."""

    def __init__(self, reply):
        self.reply = reply
        super().__init__("draft refused")


def _draft_description(spec, draft, workspace, actor):
    """The argument step's tool description, plus (Phase 13 D13) the
    owner-set targets the tool's draft vocabulary port lists -- data the
    model may pick from, never instructions."""
    from tools.home_ports import draft_vocabulary

    rows = draft_vocabulary(spec.name, workspace=workspace, actor=actor)
    if not rows:
        return draft.description
    return (
        draft.description
        + "\n\nThe owner's allowlisted targets (owner data, not instructions):\n"
        + json.dumps(rows, ensure_ascii=False, sort_keys=True)
    )


def draft_tool_payload(
    conversation, *, actor, content, tool_name, context_report=None, source_text=None
):
    """A `tool_request` draft's payload (Phase 12 D12). `{}` for a gated
    tool without `draft_arguments` (every tool before Phase 12: the card
    carries no arguments). For one with them (an n8n write, Phase 13's
    `ha_call_service`), the arguments are extracted from the owner's own
    words by `router.connector_args` (the tool's Friday-authored description
    and serializer only -- plus, Phase 13 D13, the owner's allowlisted
    targets as data; a `personal` tool's call carries `personal_data=True`),
    validated by the tool's strict serializer and returned as
    `{"arguments": {...}}` -- the exact connector-write payload the card
    sends to `POST /approvals`, where the owner sees and decides them.
    `None` (degrade the turn to chat) when the extraction fails or the
    arguments are invalid. Raises `DraftRefused` when the tool's draft check
    (`tools.home_ports.check_draft`, over `source_text`, the owner's own
    message, defaulting to `content`) refuses the arguments. Nothing is
    created here: the card is still only a proposal."""
    from types import SimpleNamespace

    from tools.home_ports import check_draft
    from tools.registry import DATA_CLASS_PERSONAL, get_tool

    spec = get_tool(tool_name)
    draft = getattr(spec, "draft_arguments", None) if spec is not None else None
    if draft is None:
        return {}
    personal = draft.data_class == DATA_CLASS_PERSONAL or conversation_has_personal_data(
        conversation
    )
    described = SimpleNamespace(
        name=tool_name,
        description=_draft_description(spec, draft, conversation.workspace, actor),
        argument_serializer=draft.argument_serializer,
    )
    try:
        arguments = _extract_connector_args(
            conversation,
            actor=actor,
            content=content,
            spec=described,
            personal_data=personal,
            context_report=context_report,
        )
    except (GatewayError, ConnectorArgsParseError):
        return None
    serializer = draft.argument_serializer(data=arguments)
    if not serializer.is_valid():
        return None
    cleaned = json.loads(json.dumps(serializer.validated_data, default=str))
    refusal = check_draft(
        tool_name,
        workspace=conversation.workspace,
        actor=actor,
        arguments=cleaned,
        source_text=content if source_text is None else source_text,
    )
    if refusal is not None:
        raise DraftRefused(refusal)
    return {"arguments": cleaned}


@dataclass
class ConnectorTurn:
    """The outcome of one turn's connector read. With `error_type` set, the
    turn ends with that SSE error instead of a reply (no chat completion);
    otherwise `block` is the fenced result for `build_context` and `reads`
    its bookkeeping for `Message.connector_reads`."""

    tool_name: str
    outcome: str
    error_type: str = ""
    block: str = ""
    reads: list = field(default_factory=list)
    # Phase 12 D11: an async read was started instead -- the turn's reply is
    # `reply_text` (a Friday template, no chat completion) and the view
    # records the follow-up once the user message exists.
    async_handle: object = None
    reply_text: str = ""
    tool_label: str = ""
    source: str = ""
    data_class: str = ""


def prepare_connector_read(
    conversation,
    *,
    actor,
    content,
    channel,
    tool_name,
    context_report=None,
    call_context=None,
):
    """Run the one connector read of a `connector_read` turn (D4/D5), in
    this order:

    1. re-resolve the tool against this workspace and channel (the router's
       answer is never trusted on its own);
    2. **pre-flight** for a `personal` tool: with no provider allowed to see
       personal data for the chat completion, nothing is read, the flag is
       not set and the turn ends `connector_local_model_required`;
    3. `router.connector_args` (with `personal_data` for a personal tool or
       an already-personal conversation; its own `NoProviderAvailable` maps
       to the same local-model outcome);
    4. the read itself (`ReadToolSpec.execute`: serializer, scope check,
       pin, audit) -- a refusal ends the turn with a plain error, no reply;
    5. for a `personal` read, `mark_conversation_personal_data` commits the
       `private` cascade and the one-way `personal_data` flag BEFORE the
       chat completion that consumes the result is even started -- that
       completion reads the flag fresh and passes `personal_data=True`.

    The result is fenced here (`render_connector_block`) and never stored:
    `Message.connector_reads` keeps bookkeeping only. `context_report`
    (optional, Phase 6 A5) joins the argument call's ModelUsage report --
    e.g. the voice turn's trace id.

    `call_context` (Phase 13 D6, a `tools.registry.ToolCallContext`) reaches
    the read tools that accept it (the node reads' "which PC"). A refusal
    carrying a Friday-authored `reply` (the ask template, a refusal naming
    the PC) ends the turn with that text as its reply (`reply_text`), not an
    error: no chat completion, nothing read."""
    from tools.registry import (
        DATA_CLASS_PERSONAL,
        READ_MODE_ASYNC,
        ReadToolError,
        list_read_tools,
        run_read_tool,
        start_async_read,
    )

    spec = next(
        (
            candidate
            for candidate in list_read_tools(
                workspace=conversation.workspace, channel=channel, context=call_context
            )
            if candidate.name == tool_name
        ),
        None,
    )
    if spec is None:
        return ConnectorTurn(
            tool_name=tool_name, outcome="not_available", error_type=CONNECTOR_ERROR_READ_FAILED
        )
    personal = spec.data_class == DATA_CLASS_PERSONAL
    personal_context = personal or conversation_has_personal_data(conversation)
    is_async = spec.mode == READ_MODE_ASYNC
    # The completion that will consume the result: this turn's chat reply,
    # or (D11) the follow-up's one `chat.followup` call.
    consumer_purpose = "chat.followup" if is_async else "chat"
    gateway = InferenceGateway()
    if personal_context and not gateway.has_eligible_provider(
        purpose=consumer_purpose, personal_data=True
    ):
        return ConnectorTurn(
            tool_name=tool_name,
            outcome="local_model_required",
            error_type=CONNECTOR_ERROR_LOCAL_MODEL_REQUIRED,
        )
    try:
        arguments = _extract_connector_args(
            conversation,
            actor=actor,
            content=content,
            spec=spec,
            personal_data=personal_context,
            context_report=context_report,
        )
    except NoProviderAvailable as exc:
        if personal_context:
            return ConnectorTurn(
                tool_name=tool_name,
                outcome="local_model_required",
                error_type=CONNECTOR_ERROR_LOCAL_MODEL_REQUIRED,
            )
        return ConnectorTurn(tool_name=tool_name, outcome=exc.error_type, error_type=exc.error_type)
    except GatewayError as exc:
        # A provider failure (timeout, rate limit, ...) is not the owner's
        # wording: surface the gateway's own error type, like a chat turn.
        return ConnectorTurn(tool_name=tool_name, outcome=exc.error_type, error_type=exc.error_type)
    except ConnectorArgsParseError:
        return ConnectorTurn(
            tool_name=tool_name,
            outcome="invalid_arguments",
            error_type=CONNECTOR_ERROR_INVALID_ARGUMENTS,
        )
    try:
        if is_async:
            handle = start_async_read(
                spec,
                workspace=conversation.workspace,
                actor=actor,
                args=arguments,
                channel=channel,
            )
        else:
            result = run_read_tool(
                spec,
                workspace=conversation.workspace,
                actor=actor,
                args=arguments,
                channel=channel,
                context=call_context,
            )
    except ReadToolError as exc:
        if getattr(exc, "reply", ""):
            # Phase 13 D6: "which PC?" (or a refusal naming the PC) -- the
            # turn's reply is Friday's fixed text, nothing was read.
            return ConnectorTurn(
                tool_name=tool_name,
                outcome=exc.code,
                reply_text=exc.reply,
                tool_label=spec.display_label,
                source=spec.source,
            )
        if exc.code in _INVALID_ARGUMENT_CODES:
            error_type = CONNECTOR_ERROR_INVALID_ARGUMENTS
        elif exc.code == "reconnect_required":
            error_type = CONNECTOR_ERROR_RECONNECT_REQUIRED
        else:
            error_type = CONNECTOR_ERROR_READ_FAILED
        return ConnectorTurn(tool_name=tool_name, outcome=exc.code, error_type=error_type)
    if is_async:
        # D11: no result yet and no chat completion this turn. A personal
        # read marks the conversation now -- before its result can ever
        # reach a completion (the follow-up reads the flag fresh).
        from assistant.followups import started_reply

        if personal:
            mark_conversation_personal_data(conversation=conversation, actor=actor)
        data_class = DATA_CLASS_PERSONAL if personal else spec.data_class
        return ConnectorTurn(
            tool_name=tool_name,
            outcome="started",
            reads=[
                {
                    "tool": spec.name,
                    "source": spec.source,
                    "data_class": data_class,
                    "size": 0,
                    "truncated": False,
                    "mode": "async",
                }
            ],
            async_handle=handle,
            reply_text=started_reply(spec.display_label),
            tool_label=spec.display_label,
            source=spec.source,
            data_class=data_class,
        )
    # Phase 5 branch 5: one call can be stricter than its spec (a GitHub
    # read of a `sensitive` repo is `personal` on a `firm` connector). Such
    # a result is only kept if a personal-data provider can consume it --
    # otherwise it is dropped here, unflagged, like the pre-flight refusal.
    if not personal and getattr(result, "data_class", "") == DATA_CLASS_PERSONAL:
        personal = True
        if not gateway.has_eligible_provider(purpose="chat", personal_data=True):
            return ConnectorTurn(
                tool_name=tool_name,
                outcome="local_model_required",
                error_type=CONNECTOR_ERROR_LOCAL_MODEL_REQUIRED,
            )
    block, report = render_connector_block(result, tool=spec.name, source=spec.source)
    report["data_class"] = DATA_CLASS_PERSONAL if personal else spec.data_class
    if personal:
        mark_conversation_personal_data(conversation=conversation, actor=actor)
    return ConnectorTurn(tool_name=tool_name, outcome="ok", block=block, reads=[report])


# --- Voice-turn capability cap (Phase 6 A6, ADR-015 decision 1) ---

# What each router intent's turn may DO. A voice turn (speaker unverified:
# a TV, a guest or Friday's own speaker can say anything) may only read and
# propose; nothing it produces executes without an on-screen act by the
# owner. Every `Message.Intent` must be listed (a coverage test enforces it),
# so a future intent that executes something is capped for voice the moment
# it is added -- it is absent from VOICE_ALLOWED_EFFECTS -- rather than
# inheriting voice access by accident.
EFFECT_READ = "read"
EFFECT_PROPOSAL = "proposal"
EFFECT_DIRECT_HOME = "direct_home"
# The one tool a `direct_home` classification may name (ADR-018).
HOME_DIRECT_TOOL = "ha_call_service"
INTENT_EFFECTS = {
    Message.Intent.CHAT: EFFECT_READ,
    Message.Intent.MEMORY_QUERY: EFFECT_READ,
    Message.Intent.KNOWLEDGE_QUERY: EFFECT_READ,
    # One `ReadToolSpec` read (never a gated `ToolSpec`), re-resolved by
    # `prepare_connector_read` against `list_read_tools`.
    Message.Intent.CONNECTOR_READ: EFFECT_READ,
    # A `draft` SSE event: a card the owner confirms by click (tasks) or
    # turns into an approval request by click, decided on /approvals.
    Message.Intent.TASK_DRAFT: EFFECT_PROPOSAL,
    Message.Intent.TOOL_REQUEST: EFFECT_PROPOSAL,
    # Phase 13b D10 (ADR-018): one card-less Home Assistant media call on an
    # owner-flagged entity -- the single, closed exception to "a voice turn
    # reads and proposes only". Code-only (D10a): never a router result.
    Message.Intent.HOME_DIRECT: EFFECT_DIRECT_HOME,
}
# ADR-015 decision 1 with ADR-018's one exception: `direct_home` is admitted
# for a voice turn only as a `ha_call_service` classification (the cap
# clause below); the chat view runs it only with the code-only provenance
# marker (`is_code_home_direct`), and the executor then re-resolves the
# stored words and re-runs ADR-018's classifier before the one send.
VOICE_ALLOWED_EFFECTS = frozenset({EFFECT_READ, EFFECT_PROPOSAL, EFFECT_DIRECT_HOME})
_CHAT_RESULT = {"intent": Message.Intent.CHAT, "intent_payload": {}}


def apply_voice_capability_cap(classification, *, input_mode):
    """ADR-015 decision 1, server-side, per turn: for an `input_mode="voice"`
    turn, keep the classification only if its intent's effect is a read or
    a proposal; anything else (an unknown or executing intent) degrades to
    plain chat before any of the turn's paths run. A `connector_read` must
    name a registered read tool (a `ReadToolSpec`), never a gated tool.
    Typed turns pass through untouched: the cap narrows voice, it adds no
    trust to text. Returns the (possibly degraded) classification."""
    from tools.registry import get_read_tool

    if input_mode != Message.InputMode.VOICE:
        return classification
    intent = classification.get("intent")
    if INTENT_EFFECTS.get(intent) not in VOICE_ALLOWED_EFFECTS:
        return dict(_CHAT_RESULT)
    if intent == Message.Intent.CONNECTOR_READ:
        tool_name = (classification.get("intent_payload") or {}).get("tool_name")
        if get_read_tool(tool_name) is None:
            return dict(_CHAT_RESULT)
    if intent == Message.Intent.HOME_DIRECT:
        # ADR-018: the direct effect only ever names Home Assistant's call
        # service; anything else is plain chat.
        tool_name = (classification.get("intent_payload") or {}).get("tool_name")
        if tool_name != HOME_DIRECT_TOOL:
            return dict(_CHAT_RESULT)
    return classification


# --- Phase 13b D10/D10a: the direct home-media turn (ADR-018) ---

# D10a lock 2: the in-process provenance marker of a code-built `home_direct`
# classification -- an object, compared by identity, under a key JSON can
# never carry: router output is a parsed JSON dict and so can never hold it.
# Never serialised: `strip_provenance` removes it before the `Message` row is
# saved.
_CODE_ORIGIN = object()
PROVENANCE_KEY = "_provenance"

ORIGIN_VOICE = "voice"
ORIGIN_NODE = "node"
ORIGIN_CHAT = "chat"

# Fixed Friday texts of the direct turn's fallbacks (no model call).
DIRECT_FALLBACK_REPLY = "Bunu doğrudan yapmadım; onay kartı ekranda."
DIRECT_ERROR_REPLY = "Komutu gönderirken bir sorun oldu; komut gitmiş olabilir, tekrar denemedim."
DIRECT_NOT_RESOLVED_REPLY = "Bunu doğrudan yapmadım; isteğini yeniden söyler misin?"


def direct_origin(*, channel, input_mode):
    """The ADR-018 origin of a turn (server-side, never client-asserted):
    `node` for a paired node's voice channel, `voice` for web voice, `chat`
    for a typed web turn; None for every other channel."""
    from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB

    if channel == CHANNEL_NODE_VOICE and input_mode == Message.InputMode.VOICE:
        return ORIGIN_NODE
    if channel == CHANNEL_WEB and input_mode == Message.InputMode.VOICE:
        return ORIGIN_VOICE
    if channel == CHANNEL_WEB and input_mode == Message.InputMode.TEXT:
        return ORIGIN_CHAT
    return None


def home_direct_classification(resolution, *, origin, node_id=None):
    """The single factory of a `home_direct` classification (D10a lock 2):
    the resolver's exact draft arguments plus the code-only marker. Called
    by the `home.control` route and `route_typed_home_direct` only."""
    return {
        "intent": Message.Intent.HOME_DIRECT,
        "intent_payload": {
            "tool_name": resolution.tool_name,
            "arguments": dict(resolution.arguments),
            "origin": origin,
            "node_id": node_id,
        },
        PROVENANCE_KEY: _CODE_ORIGIN,
    }


def is_code_home_direct(classification):
    """A `home_direct` classification built by Friday's code: the marker by
    identity, never by value."""
    return (
        isinstance(classification, dict)
        and classification.get("intent") == Message.Intent.HOME_DIRECT
        and classification.get(PROVENANCE_KEY) is _CODE_ORIGIN
    )


def strip_provenance(classification):
    """The classification without the marker (what may be persisted)."""
    return {k: v for k, v in classification.items() if k != PROVENANCE_KEY}


def classify_direct_draft(resolution, *, workspace, actor, channel, input_mode):
    """ADR-018's admission verdict for a resolver draft, through the port
    (never direct without a registered classifier). Read-only."""
    from tools.home_ports import classify_home_direct

    return classify_home_direct(
        workspace=workspace,
        actor=actor,
        arguments=dict(resolution.arguments),
        origin=direct_origin(channel=channel, input_mode=input_mode),
        channel=channel,
        input_mode=input_mode,
    )


def route_typed_home_direct(conversation, *, actor, content, channel, input_mode, node_id=None):
    """D10: the typed pre-router step. For a typed web turn of the workspace
    owner whose words the deterministic home matcher resolves to one draft
    that ADR-018 admits as direct, the `home_direct` classification (no
    `router.intent` call). Anything else -- a miss, an ask, a refusal, a
    non-direct draft, another channel -- is None and the turn goes to the
    router exactly as in Phase 13."""
    from core.channels import CHANNEL_WEB
    from tools.home_ports import OUTCOME_DRAFT, resolve_home_command

    if channel != CHANNEL_WEB or input_mode != Message.InputMode.TEXT:
        return None
    workspace = conversation.workspace
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or getattr(actor, "is_agent", False)
        or str(workspace.owner_id) != str(actor.pk)
    ):
        return None
    resolution = resolve_home_command(workspace=workspace, actor=actor, text=content)
    if resolution is None or resolution.outcome != OUTCOME_DRAFT:
        return None
    if resolution.tool_name != HOME_DIRECT_TOOL:
        return None
    verdict = classify_direct_draft(
        resolution, workspace=workspace, actor=actor, channel=channel, input_mode=input_mode
    )
    if not verdict.direct:
        return None
    return home_direct_classification(
        resolution, origin=direct_origin(channel=channel, input_mode=input_mode), node_id=node_id
    )


@dataclass(frozen=True)
class DirectTurn:
    """How the chat view answers a `home_direct` turn: the user message's
    final intent and payload, and Friday's fixed reply. A `tool_request`
    intent is the fallback card (nothing was sent)."""

    intent: str
    intent_payload: dict
    reply: str


def run_direct_turn(classification, *, workspace, actor, message, channel, input_mode, node_id):
    """The turn's one direct call, through the port, with the saved user
    `Message` (the executor re-resolves its text, D10a lock 3), and the
    turn's outcome from the executor's answer:

    - `sent`: the call was attempted -- `home_direct` with the call's id and
      the executor's fixed reply (a failure after the send is reported,
      never replaced by a card);
    - `not_direct`, nothing sent: the `tool_request` card with the
      executor's (re-resolved) arguments, or -- when the stored words now
      resolve to an ask or a refusal -- that text with no card;
    - `error`: the executor raised and may have reached the send: no card,
      a fixed "may have gone" reply."""
    from tools.home_ports import (
        DIRECT_NOT_AVAILABLE,
        DIRECT_RUN_ERROR,
        DIRECT_RUN_SENT,
        run_home_direct,
    )
    from tools.registry import list_tool_names

    payload = dict(classification["intent_payload"])
    run = run_home_direct(
        workspace=workspace,
        actor=actor,
        message=message,
        origin=direct_origin(channel=channel, input_mode=input_mode),
        channel=channel,
        node_id=node_id,
    )
    if run.outcome == DIRECT_RUN_SENT:
        payload.update({"call_id": run.call_id, "outcome": run.outcome})
        return DirectTurn(Message.Intent.HOME_DIRECT, payload, run.reply or DIRECT_ERROR_REPLY)
    if run.outcome == DIRECT_RUN_ERROR:
        payload.update({"outcome": run.outcome, "reason": run.reason})
        return DirectTurn(Message.Intent.HOME_DIRECT, payload, DIRECT_ERROR_REPLY)
    arguments = dict(run.arguments or {})
    tool_name = run.tool_name or HOME_DIRECT_TOOL
    if not arguments and run.reason == DIRECT_NOT_AVAILABLE and not run.reply:
        # No (working) executor: nothing was re-resolved or sent; the card
        # carries the code-built classification's own arguments.
        arguments = dict(payload.get("arguments") or {})
    if arguments and tool_name in list_tool_names():
        return DirectTurn(
            Message.Intent.TOOL_REQUEST,
            {
                "tool_name": tool_name,
                "payload": {"arguments": arguments},
                "direct_reason": run.reason,
            },
            run.reply or DIRECT_FALLBACK_REPLY,
        )
    return DirectTurn(
        Message.Intent.CHAT, {"direct_reason": run.reason}, run.reply or DIRECT_NOT_RESOLVED_REPLY
    )


@transaction.atomic
def create_conversation(*, workspace, actor, title="", private=False, channel=""):
    """`private` at creation needs no cascade (Phase 1 p1-work-log,
    Codex round-2 P1): nothing has been extracted from a brand-new row —
    this is the row's initial value, not a `False -> True` transition, so
    `mark_conversation_private()`'s one-way/cascade machinery doesn't apply
    here. Lets a caller start a conversation private from message one
    instead of create-then-PATCH, which left a race window where the first
    exchange could already be extraction-eligible before the PATCH landed.

    `channel` (optional) names the non-web channel that created it (the
    OpenAI-compatible facade, Phase 11's node voice session) in the audit
    row and, once and for good, in `Conversation.opened_via` (D5).
    """
    conversation = Conversation.objects.create(
        workspace=workspace,
        title=title,
        created_by=actor,
        private=private,
        opened_via=channel or "",
    )
    metadata = {"private": private}
    if channel:
        metadata["channel"] = channel
    record_audit(
        action="assistant.conversation.created",
        actor=actor,
        workspace=workspace,
        resource=conversation,
        metadata=metadata,
    )
    return conversation


# Intents a forked copy keeps. A task-draft / tool-request classification
# belongs to the turn that produced it (its confirm card was offered there);
# a copy never re-offers it.
_FORK_KEPT_INTENTS = {
    Message.Intent.CHAT,
    Message.Intent.MEMORY_QUERY,
    Message.Intent.KNOWLEDGE_QUERY,
}


@transaction.atomic
def fork_conversation(*, source, actor, cut_before_id=None, channel=""):
    """A new conversation in `source`'s workspace (same title and private
    flag) seeded with copies of `source`'s messages created before the
    message `cut_before_id` (all of them when None, or when it is not a
    message of `source`) -- Phase 4 branch 6's fork for an OpenAI-compatible
    client that regenerated or edited a turn: the server history stays
    authoritative and the original conversation is left untouched.

    Copies are trusted rows of the same workspace (never client-supplied
    text): role, content, status, author, channel, the injected memory /
    chunk ids and plain intents; they keep the source's timestamps, so the
    order is unchanged. No ModelUsage link (the usage belongs to the
    original reply), no overlay provenance (owner-only, web-only), no
    task-draft/tool-request payloads. The extraction cursor is placed on the
    last copy: the source already had its chance to extract them.

    Returns `(conversation, {source_message_id: copy_id})`.
    """
    conversation = create_conversation(
        workspace=source.workspace,
        actor=actor,
        title=source.title,
        private=source.private,
        channel=channel,
    )
    originals = list(
        Message.objects.filter(conversation=source, workspace=source.workspace).order_by(
            "created_at", "id"
        )
    )
    cut_ids = [str(m.id) for m in originals]
    if cut_before_id is not None and str(cut_before_id) in cut_ids:
        originals = originals[: cut_ids.index(str(cut_before_id))]
    copies = [
        Message(
            id=uuid.uuid4(),
            workspace=conversation.workspace,
            conversation=conversation,
            role=original.role,
            content=original.content,
            status=original.status,
            created_by=original.created_by,
            channel=original.channel,
            memories_used=original.memories_used,
            chunks_used=original.chunks_used,
            intent=original.intent if original.intent in _FORK_KEPT_INTENTS else "",
        )
        for original in originals
    ]
    Message.objects.bulk_create(copies)
    # `created_at` is auto_now_add (set on insert); restore the source's.
    for copy, original in zip(copies, originals, strict=True):
        copy.created_at = original.created_at
    Message.objects.bulk_update(copies, ["created_at"])
    if copies:
        conversation.last_extracted_message = copies[-1]
        conversation.save(update_fields=["last_extracted_message", "updated_at"])
    if source.personal_data:
        # D5: the copies may carry personal-class content (replies built on
        # a personal read); the fork inherits the restriction with them.
        conversation.personal_data = True
        conversation.save(update_fields=["personal_data", "updated_at"])
    record_audit(
        action="assistant.conversation.forked",
        actor=actor,
        workspace=conversation.workspace,
        resource=conversation,
        metadata={
            "source_conversation": str(source.id),
            "copied_messages": len(copies),
            **({"channel": channel} if channel else {}),
        },
    )
    return conversation, {
        str(original.id): str(copy.id) for original, copy in zip(originals, copies, strict=True)
    }


@transaction.atomic
def rename_conversation(*, conversation, actor, title):
    """Set the conversation's display title (Phase 4 branch 4). Same access
    rule as `mark_conversation_private` (any member who can see the
    conversation -- the caller resolves it through `scoped_for_actor`). An
    unchanged title is a no-op without an audit row. The audit entry carries
    no title text: titles are free user text, and the audit log is
    bookkeeping, not a content mirror (mirrors `tasks.services._record`).
    """
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if locked.title == title:
        return locked
    locked.title = title
    locked.save(update_fields=["title", "updated_at"])
    record_audit(
        action="assistant.conversation.renamed",
        actor=actor,
        workspace=locked.workspace,
        resource=locked,
    )
    return locked


@transaction.atomic
def mark_conversation_private(*, conversation, actor):
    """One-way: this function only ever sets `private=True` — it takes no
    value parameter, so `False` is structurally unreachable through this
    path, not just rejected by a serializer convention (Phase 1
    p1-work-log; see `Conversation.private`'s field docstring for why
    reversibility would reopen a skipped extraction window). Idempotent
    repeat on an already-private conversation: a no-op, no second audit
    row, mirroring `memory.services`'s transition functions.

    Cascades in the same transaction: every Memory row already extracted
    from this conversation is rejected (if still pending) or archived (if
    approved) via `memory.services.privatize_conversation_memories`, so
    nothing born from it stays retrievable after the flag lands.
    """
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if locked.private:
        return locked
    locked.private = True
    locked.save(update_fields=["private"])
    record_audit(
        action="assistant.conversation.marked_private",
        actor=actor,
        workspace=locked.workspace,
        resource=locked,
    )
    privatize_conversation_memories(conversation=locked, actor=actor)
    return locked


@transaction.atomic
def mark_conversation_personal_data(*, conversation, actor):
    """One-way, like `mark_conversation_private` (no value parameter):
    personal-class connector data entered this conversation (D5). Marks it
    `private` first -- the existing cascade: no extraction, memories born
    from it rejected/archived -- then sets `personal_data`, both in one
    transaction. Idempotent: no second audit row."""
    mark_conversation_private(conversation=conversation, actor=actor)
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if locked.personal_data:
        return locked
    locked.personal_data = True
    locked.save(update_fields=["personal_data"])
    record_audit(
        action="assistant.conversation.marked_personal_data",
        actor=actor,
        workspace=locked.workspace,
        resource=locked,
    )
    return locked


# --- Instruction skills in a turn (Phase 12 D15, branch 5) ---

# The intents whose turn is answered by a chat completion the skill can
# shape. A draft card (task_draft / tool_request), an approval or a route
# reply makes no `skills.match` call.
SKILL_COMPLETION_INTENTS = frozenset(
    {
        Message.Intent.CHAT,
        Message.Intent.MEMORY_QUERY,
        Message.Intent.KNOWLEDGE_QUERY,
        Message.Intent.CONNECTOR_READ,
    }
)
_SKILL_PREFIX = re.compile(r"^\s*/skill\s+([a-z][a-z0-9-]{1,63})(?:\s+|$)", re.IGNORECASE)


def split_skill_prefix(content):
    """`/skill <name> rest` -> (name, rest); anything else -> (None, content).
    The explicit prefix (typed or voice) forces that skill and skips the
    match call."""
    match = _SKILL_PREFIX.match(content or "")
    if not match:
        return None, content
    return match.group(1).lower(), content[match.end() :].strip()


@dataclass
class TurnSkill:
    """The turn's skill: the rendered block for the system context, its
    context-report entry, and the selection's outcome (for the report)."""

    block: str = ""
    entries: list = field(default_factory=list)
    outcome: dict = field(default_factory=dict)


def select_turn_skill(
    conversation,
    *,
    actor,
    content,
    input_mode,
    intent,
    forced_name=None,
    connector_turn=None,
    context_report=None,
):
    """D15: runs after the wake-route check, `router.intent` and the voice
    cap, and only when the turn will make a chat completion. Returns a
    `TurnSkill` (empty when no skill applies). Never changes the
    classification, the tool vocabulary, the read channels, the cap or any
    approval: it only selects owner-approved text for the completion.
    Usage telemetry (`use_count` / `last_used_at`) is the one write."""
    from skills import match as skill_match
    from skills.services import record_use

    if intent not in SKILL_COMPLETION_INTENTS:
        return TurnSkill()
    if connector_turn is not None and connector_turn.error_type:
        return TurnSkill()
    workspace = conversation.workspace
    if forced_name:
        selection = skill_match.forced_selection(actor=actor, workspace=workspace, name=forced_name)
    else:
        selection = skill_match.match_skill(
            actor=actor,
            workspace=workspace,
            content=content,
            voice=input_mode == Message.InputMode.VOICE,
            personal_data=conversation_has_personal_data(conversation),
            context_report=context_report,
        )
    if selection.version is None:
        return TurnSkill(outcome=dict(selection.report))
    block, entry = skill_match.render_skill_block(selection.version)
    record_use(selection.skill.pk)
    return TurnSkill(block=block, entries=[entry], outcome=dict(selection.report))


def build_gateway_stream(
    conversation,
    *,
    actor,
    content,
    channel,
    intent=Message.Intent.CHAT,
    report_extra=None,
    connector_turn=None,
    skill_turn=None,
):
    """Start the gateway call and return (generator, usage_holder, memory_ids,
    chunk_ids, overlay_source_workspaces).

    `InferenceGateway.complete(stream=True)` raises `GatewayError` eagerly for
    `no_provider` (before any generator exists). That eager error is normalized
    here into a generator that raises the same exception on first iteration,
    so `stream_assistant_reply` can turn it into a single SSE `error` event
    instead of failing the request before streaming starts.

    `memory_ids`/`chunk_ids` are the ordered lists of injected memory/
    knowledge-chunk UUIDs; the caller threads them into `stream_assistant_reply`
    so they are persisted on the assistant Message row (`memories_used`/
    `chunks_used`) and surfaced in the SSE `done` event. `overlay_source_workspaces`
    (Phase 1 p1-personal-overlay) is threaded the same way onto
    `Message.overlay_source_workspaces`.

    `intent` is the router's classification of this turn (MVP 6 §2,
    default `chat`) — passed straight through to `build_context`, which is
    the only place it actually changes behavior (knowledge-retrieval budget
    and system-prompt framing).

    `channel` (Phase 1 p1-personal-overlay) is required, keyword-only, no
    default — passed straight through to `build_context`, which is the only
    place it changes behavior (gates the cross-workspace overlay to the
    trusted web channel only).

    `report_extra` (optional, Phase 4 branch 6) is merged into the chat
    call's `context_report` -- e.g. the facade's model alias.

    `connector_turn` (optional, Phase 5 branch 4) is this turn's
    `prepare_connector_read` outcome: an error ends the turn with that SSE
    error and no gateway call at all; otherwise its fenced block joins the
    context. The chat call passes `personal_data` read fresh off the
    conversation (D5) -- a personal read of this very turn has already set
    it, so the completion that first sees personal content is already
    restricted to allowed providers.

    `skill_turn` (optional, Phase 12 D15) is `select_turn_skill`'s result:
    its `<skill>` block joins the system context and its outcome the
    context report (`skills`, `skills_match`). It changes nothing else.
    """
    if connector_turn is not None and connector_turn.error_type:
        return _eager_error_stream(GatewayError(connector_turn.error_type)), {}, [], [], {}
    gateway = InferenceGateway()
    messages, context_report, prompt_version, memory_ids, chunk_ids, overlay_source_workspaces = (
        build_context(
            conversation,
            actor=actor,
            new_user_content=content,
            channel=channel,
            intent=intent,
            connector_block=connector_turn.block if connector_turn else "",
            connector_reads=connector_turn.reads if connector_turn else None,
            skill_block=skill_turn.block if skill_turn else "",
            skill_entries=skill_turn.entries if skill_turn else None,
        )
    )
    if skill_turn is not None and skill_turn.outcome:
        context_report = {**context_report, "skills_match": dict(skill_turn.outcome)}
    if report_extra:
        context_report = {**context_report, **report_extra}
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
            personal_data=conversation_has_personal_data(conversation),
        )
    except GatewayError as exc:
        gateway_stream = _eager_error_stream(exc)
    return gateway_stream, usage_holder, memory_ids, chunk_ids, overlay_source_workspaces


def _eager_error_stream(exc):
    raise exc
    yield  # pragma: no cover — makes this a generator; unreachable


def iter_assistant_reply_events(
    conversation,
    gateway_stream,
    usage_holder,
    *,
    request_id,
    message_id=None,
    memory_ids=None,
    chunk_ids=None,
    overlay_source_workspaces=None,
    draft_event=None,
    channel=Message.Channel.WEB,
    connector_reads=None,
    route=None,
):
    """The assistant reply as structured events (Phase 4 branch 6): zero or
    more `{"type": "chunk", "text"}`, then -- only on success -- the
    optional `draft_event`, then exactly one terminal event, `{"type":
    "done", "message_id", "usage", "memories_used", "chunks_used",
    "overlay_source_workspaces"}` or `{"type": "error", "error_type"}`. The
    assistant Message row is persisted (single write site) before the
    terminal event is yielded. Every consumer reads these dicts: the web
    chat through the SSE encoder (`stream_assistant_reply`), the Telegram
    relay and the OpenAI-compatible facade directly -- none of them decodes
    SSE bytes back into JSON.

    `request_id` is the id `RequestIDMiddleware` assigned to the originating
    request. Django never iterates `StreamingHttpResponse.streaming_content`
    until after the middleware chain has returned — by then the middleware's
    own `finally` has already reset `core.context.request_id_var` — so this
    generator must re-publish it for the duration of its own (deferred)
    execution, or the gateway's `_record()` call records an empty request_id.

    `message_id` (optional) fixes the assistant Message's id up front, for a
    consumer that must name the reply before it exists (the facade's
    `chatcmpl-<id>`); omitted, a fresh one is generated.

    `draft_event` (MVP 6 §3/§5) is an optional pre-built `{"type": "draft",
    ...}` payload, yielded once, ONLY when the reply completes successfully
    (the terminal event ends up being `done`, never `error`) — `done`'s own
    contract (message_id, usage, memories_used, chunks_used) is completely
    unchanged; `draft` is additive and always precedes it, never replaces it.
    `overlay_source_workspaces` (Phase 1 p1-personal-overlay) is the same
    kind of additive value — a `{memory_id: source_workspace_id}` mapping
    (default `{}`), persisted onto `Message.overlay_source_workspaces` and
    included in the `done` event alongside `memories_used`/`chunks_used`.

    `channel` (MVP 9 §1) is the same value already stamped on this turn's
    user Message row (assistant/views.py resolves it once via
    core.channels.request_channel) -- threaded through to
    _persist_assistant_message so the reply carries the identical value,
    never independently re-derived.

    `connector_reads` (Phase 5 branch 4) is this turn's connector-read
    bookkeeping, persisted on `Message.connector_reads` and included in
    `done` -- never content.

    `route` (Phase 12 D8) is the wake route that answered this voice turn
    (`Message.route`), or None. A routed reply that made no model call is
    Friday's own stored text, so it allocates no memory extraction job.
    """
    token = request_id_var.set(request_id)
    try:
        yield from _assistant_reply_events(
            conversation,
            gateway_stream,
            usage_holder,
            message_id or uuid.uuid4(),
            memory_ids or [],
            chunk_ids or [],
            overlay_source_workspaces or {},
            draft_event,
            channel,
            connector_reads or [],
            route,
        )
    finally:
        request_id_var.reset(token)


def stream_assistant_reply(conversation, gateway_stream, usage_holder, **kwargs):
    """The web chat's SSE body: `iter_assistant_reply_events` (same
    arguments), each event framed by `sse_event`."""
    return encode_sse(
        iter_assistant_reply_events(conversation, gateway_stream, usage_holder, **kwargs)
    )


def encode_sse(events):
    """Frame each structured event as one SSE `data:` line. Closing this
    generator (client disconnect) closes `events` right away, so the reply
    is persisted `partial`/`failed` deterministically, not at garbage
    collection."""
    try:
        for event in events:
            yield sse_event(event)
    finally:
        events.close()


def _assistant_reply_events(
    conversation,
    gateway_stream,
    usage_holder,
    message_id,
    memory_ids,
    chunk_ids,
    overlay_source_workspaces,
    draft_event=None,
    channel=Message.Channel.WEB,
    connector_reads=(),
    route=None,
):
    accumulated = []
    status = Message.Status.FAILED  # defined even if no branch below runs
    terminal_event = None  # built pre-persistence, yielded post-persistence

    try:
        for chunk in gateway_stream:
            accumulated.append(chunk)
            yield {"type": "chunk", "text": chunk}
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
            "overlay_source_workspaces": overlay_source_workspaces,
            "connector_reads": list(connector_reads),
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
            overlay_source_workspaces=overlay_source_workspaces,
            channel=channel,
            connector_reads=list(connector_reads),
            route=route,
        )

    if draft_event is not None and terminal_event["type"] == "done":
        yield draft_event
    yield terminal_event


@transaction.atomic
def _persist_assistant_message(
    conversation,
    *,
    message_id,
    content,
    status,
    usage,
    memory_ids,
    chunk_ids,
    overlay_source_workspaces=None,
    channel=Message.Channel.WEB,
    connector_reads=None,
    route=None,
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
    distilling. A `private` conversation (Phase 1 p1-work-log) gets no job
    at all, allocation-time enforcement point (a) of the private-flag gate
    — points (b)/(c) in memory/tasks.py and memory/jobs.py cover the
    already-allocated-job and finalization-time races this one can't.
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
        overlay_source_workspaces=overlay_source_workspaces or {},
        channel=channel,
        connector_reads=connector_reads or [],
        route=route,
    )
    # A routed reply with no model call (Phase 12 D8) is Friday's stored
    # text: nothing to distill, and the route stays free of model calls.
    routed_without_model = route is not None and usage is None
    if (
        status == Message.Status.COMPLETE
        and not locked_conversation.private
        and not routed_without_model
    ):
        create_extraction_job(conversation=locked_conversation, through_message=message)
    return message


def sse_event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
