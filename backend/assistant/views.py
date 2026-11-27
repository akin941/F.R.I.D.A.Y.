import uuid

from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from accounts.authentication import (
    NodeVoiceTokenAuthentication,
    PersonalAccessTokenAuthentication,
)
from assistant.followups import create_followup
from assistant.models import Conversation, Message
from assistant.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    ConversationUpdateSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from assistant.services import (
    NODE_ACTION_TOOL,
    NODE_ACTION_WHERE_TO_ACT,
    apply_voice_capability_cap,
    build_gateway_stream,
    classify_intent,
    create_conversation,
    is_code_home_direct,
    mark_conversation_private,
    prepare_connector_read,
    rename_conversation,
    route_typed_home_direct,
    run_direct_turn,
    select_turn_skill,
    split_skill_prefix,
    stream_assistant_reply,
    strip_provenance,
)
from assistant.wake_routes import HOME_ROUTE_NAME, route_voice_turn, static_reply_stream
from core.channels import (
    CHANNEL_NODE_VOICE,
    CHANNEL_WEB,
    is_low_trust,
    request_channel,
    scoped_for_actor,
)
from core.throttling import ScopedRateThrottle
from core.tracing import request_trace_id, trace_report
from knowledge.models import KnowledgeChunk
from tools.registry import ToolCallContext
from workspaces.models import Workspace

# Allowlisted for PAT auth (MVP 9 §1/§2). PersonalAccessTokenAuthentication
# MUST precede JWTAuthentication: DRF's authentication loop stops at the
# first authenticator that raises AuthenticationFailed, and
# JWTAuthentication raises InvalidToken for any non-JWT-shaped string
# (including a jpat_... token) -- reordering this would silently reject
# every PAT request (see accounts/authentication.py's own docstring).
_PAT_ALLOWLISTED_AUTHENTICATION_CLASSES = [
    PersonalAccessTokenAuthentication,
    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
]
# Phase 11 D4: the chat turn alone swaps the base PAT authenticator for
# `NodeVoiceTokenAuthentication` (`cli` + `node_voice`): a `cli` PAT gets
# exactly today's result, a `node_voice` token authenticates as
# CHANNEL_NODE_VOICE, `openai`/`hook` keep their 403. Every other view keeps
# the shared list above, so a voice token is a 403 `token_kind_forbidden`
# there.
_CHAT_TURN_AUTHENTICATION_CLASSES = [
    NodeVoiceTokenAuthentication,
    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
]


def with_origin_channel(conversations):
    """Annotate each conversation with `origin_channel`: the channel of its
    first message (Phase 4 branch 4's channel badge -- a conversation started
    from the CLI, Telegram or a facade client). One correlated subquery, so
    the list stays a single query."""
    first_message = Message.objects.filter(conversation=OuterRef("pk")).order_by("created_at", "id")
    return conversations.annotate(origin_channel=Subquery(first_message.values("channel")[:1]))


def _get_conversation(request, conversation_id):
    # By-id lookup, no workspace parameter at all -- scoped_for_actor still
    # forces a channel-bound request to its bound workspace, so a PAT bound
    # to workspace A can never reach a conversation in workspace B by id
    # (round-2 P1 fix). Shared by every conversation-detail view (Phase 1
    # p1-work-log's ConversationDetailView reuses this rather than
    # duplicating it a second time).
    return with_origin_channel(
        scoped_for_actor(request, Conversation.objects).filter(id=conversation_id)
    ).first()


class ConversationListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request):
        workspace_id = request.query_params.get("workspace")
        if not workspace_id:
            # A CLI/PAT request has no workspace to supply -- it's implied
            # by the bound credential (core.channels.scoped_for_actor,
            # below). Only a CHANNEL_WEB request must supply one.
            if not is_low_trust(request):
                return Response(
                    {"detail": "workspace query parameter is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            workspace_uuid = None
        else:
            try:
                workspace_uuid = uuid.UUID(workspace_id)
            except ValueError:
                return Response(
                    {"detail": "workspace must be a valid UUID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        conversations = with_origin_channel(
            scoped_for_actor(request, Conversation.objects, workspace=workspace_uuid)
        ).order_by("-created_at", "-id")
        return Response(ConversationSerializer(conversations, many=True).data)

    def post(self, request):
        data = request.data
        if is_low_trust(request):
            # Resolve the bound workspace before serializer validation --
            # ConversationCreateSerializer declares `workspace` as required,
            # enforced before any view code runs. `.copy()` first: DRF
            # exposes `request.data` as an immutable QueryDict for
            # form-encoded requests, so mutating it in place would 500 on
            # anything but a JSON body (Codex review, branch 1 round 1).
            # Only inject into an actual mapping -- a syntactically valid
            # but non-object body (e.g. a JSON list) has no `.copy()`/`[]`
            # assignment to make sense of; leave it untouched and let the
            # serializer's own validation reject it with its normal 400
            # (Codex review, branch 1 round 2).
            if isinstance(data, dict):
                data = data.copy()
                data["workspace"] = request.auth.bound_workspace_id
        serializer = ConversationCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        workspace = scoped_for_actor(
            request, Workspace.objects, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        conversation = create_conversation(
            workspace=workspace,
            actor=request.user,
            title=serializer.validated_data.get("title", ""),
            private=serializer.validated_data.get("private", False),
        )
        return Response(ConversationSerializer(conversation).data, status=status.HTTP_201_CREATED)


class ConversationDetailView(APIView):
    """Retrieve + update (Phase 1 p1-work-log's one-way private flag —
    closes MASTER_PLAN §O's private-conversation-flag half — and Phase 4
    branch 4's rename). `title` and `private` are the only writable fields;
    both writes happen in one transaction, so a combined PATCH either
    applies fully or not at all.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request, conversation_id):
        conversation = _get_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ConversationSerializer(conversation).data)

    def patch(self, request, conversation_id):
        conversation = _get_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ConversationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            if "title" in serializer.validated_data:
                rename_conversation(
                    conversation=conversation,
                    actor=request.user,
                    title=serializer.validated_data["title"],
                )
            if serializer.validated_data.get("private"):
                mark_conversation_private(conversation=conversation, actor=request.user)
        return Response(ConversationSerializer(_get_conversation(request, conversation.id)).data)


def _get_chat_conversation(request, conversation_id):
    """`_get_conversation` plus the node voice rule (Phase 11 D4): over
    CHANNEL_NODE_VOICE only a conversation opened via `node_voice` exists --
    any other one (web, CLI, facade, a legacy row with `opened_via=""`) is
    the same 404 as another workspace's, so a leaked 15-minute voice token
    can read or append to nothing but what a live session opened."""
    conversation = _get_conversation(request, conversation_id)
    if (
        conversation is not None
        and request_channel(request) == CHANNEL_NODE_VOICE
        and conversation.opened_via != Message.Channel.NODE_VOICE
    ):
        return None
    return conversation


def _origin_node_id(request, channel):
    """The node a `node_voice` turn was spoken to: the voice token's
    `node_device_id` (a node's id is its device id). None for every other
    channel -- a web turn says nothing about which PC the browser runs on."""
    if channel != CHANNEL_NODE_VOICE:
        return None
    device_id = getattr(request.auth, "node_device_id", None)
    return str(device_id) if device_id is not None else None


class ConversationMessageListView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _CHAT_TURN_AUTHENTICATION_CLASSES
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat"

    def perform_content_negotiation(self, request, force=False):
        # The success response is a hand-built StreamingHttpResponse
        # (Content-Type: text/event-stream set directly below) — it never
        # passes through a DRF renderer at all. A standards-compliant SSE
        # client sending `Accept: text/event-stream` would otherwise get a
        # 406 from DRF's default negotiation before this view ever runs, since
        # no registered renderer advertises that media type. Every DRF
        # `Response` this view actually returns (400/404 errors, GET's message
        # list) is still plain JSON regardless of what the client asked for.
        return (JSONRenderer(), "application/json")

    def get_throttles(self):
        # Only the message-send (POST) path drives provider cost; listing is free.
        if self.request.method != "POST":
            return []
        return super().get_throttles()

    def get(self, request, conversation_id):
        conversation = _get_chat_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        messages = list(
            scoped_for_actor(request, Message.objects, workspace=conversation.workspace)
            .filter(conversation=conversation)
            .annotate(confirmed_task_ref=F("confirmed_task__id"))
            .order_by("created_at", "id")
        )
        data = MessageSerializer(messages, many=True).data
        chunk_documents = self._chunk_documents(request, conversation, messages)
        for message in data:
            # {chunk_id: document_id} for this reply's `chunks_used` (Phase 4
            # branch 4's "Kaynaklar" deep links into /knowledge). Resolved
            # through the same scope as the conversation; a chunk whose
            # document is gone is simply absent (rendered without a link).
            message["chunk_documents"] = {
                chunk_id: chunk_documents[chunk_id]
                for chunk_id in message["chunks_used"]
                if chunk_id in chunk_documents
            }
        if is_low_trust(request) or request.user.id != conversation.workspace.owner_id:
            # overlay_source_workspaces (p1-personal-overlay) only ever
            # makes sense for the workspace OWNER: the overlay itself only
            # ever fires for actor == workspace.owner, so this field is
            # inherently owner-only content -- anyone else reading it is a
            # leak, not a display gap. Redacted for:
            #  - a low-trust PAT/CLI/Telegram request (round-1 P1): a
            #    message row can carry provenance from an EARLIER
            #    web-channel turn that named a workspace outside this
            #    channel's own binding, even for its own in-scope
            #    conversation -- true even when the PAT's underlying user
            #    IS the owner, since a low-trust credential is deliberately
            #    narrower-trust than the owner's own web session.
            #  - any web/JWT reader who is not the workspace owner
            #    (round-3 P2): the round-2 gate only stops a NEW overlay
            #    from firing once a personal workspace gains a second
            #    member -- it does nothing for provenance ALREADY
            #    persisted on earlier rows, which a fellow member could
            #    otherwise read straight off this same conversation over a
            #    normal, fully-trusted web request.
            for message in data:
                message["overlay_source_workspaces"] = {}
        return Response(data)

    @staticmethod
    def _chunk_documents(request, conversation, messages):
        chunk_ids = set()
        for message in messages:
            for chunk_id in message.chunks_used or []:
                try:
                    chunk_ids.add(uuid.UUID(str(chunk_id)))
                except ValueError:
                    continue
        if not chunk_ids:
            return {}
        rows = (
            scoped_for_actor(request, KnowledgeChunk.objects, workspace=conversation.workspace)
            .filter(id__in=chunk_ids)
            .values_list("id", "document_id")
        )
        return {str(chunk_id): str(document_id) for chunk_id, document_id in rows}

    def post(self, request, conversation_id):
        conversation = _get_chat_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]
        # Resolved once, threaded through to both this turn's user message
        # and its assistant reply (MVP 9 §1) -- never independently
        # re-derived for the reply, so the two rows can never drift apart.
        channel = request_channel(request)
        # Phase 6 D9: provenance only, client-asserted; a voice turn exists
        # only on the trusted web session, every other channel is `text` --
        # except the node voice channel (Phase 11 D4), which is ALWAYS
        # `voice` whatever the body says, so the ADR-015 cap always applies.
        input_mode = serializer.validated_data["input_mode"]
        if channel == CHANNEL_NODE_VOICE:
            input_mode = Message.InputMode.VOICE
        elif channel != CHANNEL_WEB:
            input_mode = Message.InputMode.TEXT
        # Phase 6 A5: the client's per-turn trace id (ignored when malformed)
        # and the input mode join every ModelUsage row of this turn.
        turn_report = trace_report(request_trace_id(request))
        if input_mode == Message.InputMode.VOICE:
            turn_report["input_mode"] = input_mode

        # Classify before building the reply context (MVP 6 §1) — a wholly
        # new step inserted ahead of the existing build_gateway_stream/persist
        # pair below, whose relative order is unchanged: build_gateway_stream
        # still runs before the user Message row exists, so build_context's
        # history query (which excludes the message being sent) is never at
        # risk of double-counting it.
        # Phase 12 D8: a voice turn whose whole utterance is a wake-route
        # phrase (or an owner alias) is answered by the route -- no
        # `router.intent` call; a miss (and every typed turn) falls through
        # to the normal router.
        origin_node_id = _origin_node_id(request, channel)
        routed = route_voice_turn(
            conversation,
            actor=request.user,
            content=content,
            channel=channel,
            input_mode=input_mode,
            origin_node_id=origin_node_id,
        )
        # Phase 13b D10 (ADR-018): a typed web turn the home matcher resolves
        # to one draft that ADR-018 admits as direct is `home_direct` -- no
        # `router.intent` call. Anything else (None) goes to the router.
        typed_direct = None
        if routed is None:
            typed_direct = route_typed_home_direct(
                conversation,
                actor=request.user,
                content=content,
                channel=channel,
                input_mode=input_mode,
                node_id=origin_node_id,
            )
        # Phase 12 D15: a `skill.<name>` route or an explicit `/skill <name>`
        # prefix names the turn's skill (no `skills.match` call). The prefix
        # is stripped from what the router and the completion see; the
        # stored message keeps what the owner said.
        forced_skill = routed.skill_name if routed is not None else None
        prefixed_skill, turn_content = split_skill_prefix(content)
        if forced_skill is None and prefixed_skill is not None:
            forced_skill = prefixed_skill
        else:
            turn_content = content
        if not turn_content.strip():
            turn_content = content
        # Phase 13 D6: what the "which PC" resolver decides by -- the
        # owner's own words of this turn and, for a `node_voice` turn, the
        # node the owner spoke to (the token's device). Never connector,
        # transcript or node text.
        call_context = ToolCallContext(
            source_text=turn_content,
            origin_node_id=origin_node_id,
            input_mode=input_mode,
        )
        if routed is not None and routed.classification is not None:
            classification = dict(routed.classification)
        elif typed_direct is not None:
            classification = typed_direct
        else:
            classification = classify_intent(
                conversation,
                actor=request.user,
                content=turn_content,
                client_local_date=serializer.validated_data["client_local_date"],
                channel=channel,
                context_report=turn_report or None,
                call_context=call_context,
            )
        target_reply = classification.pop("reply_text", None)
        # ADR-015 decision 1 (Phase 6 A6): a voice turn reads and proposes
        # only -- capped here, before any of the turn's paths run.
        classification = apply_voice_capability_cap(classification, input_mode=input_mode)
        # Phase 13b D10a lock 2: a `home_direct` classification runs only
        # with the code-only provenance marker (by identity); a router
        # result, a JSON round trip or any hand-built dict without it is
        # plain chat. The marker is never persisted.
        home_direct = classification.get("intent") == Message.Intent.HOME_DIRECT
        if home_direct and not is_code_home_direct(classification):
            classification = {"intent": Message.Intent.CHAT, "intent_payload": {}}
            home_direct = False
        classification = strip_provenance(classification)

        # Phase 5 branch 4 (brief D4/D5): at most one connector read per
        # turn, run and fenced here -- and, for a personal read, the
        # conversation's `personal_data` flag committed -- before the chat
        # completion below is started.
        connector_turn = None
        if classification["intent"] == Message.Intent.CONNECTOR_READ:
            connector_turn = prepare_connector_read(
                conversation,
                actor=request.user,
                # Phase 12 D8: a `tool.*` route extracts the arguments from
                # the words after the owner's alias.
                content=(
                    routed.args_text if routed is not None and routed.args_text else turn_content
                ),
                channel=channel,
                tool_name=classification["intent_payload"]["tool_name"],
                context_report=turn_report or None,
                call_context=call_context,
            )
            classification["intent_payload"] = {
                "tool_name": connector_turn.tool_name,
                "outcome": connector_turn.outcome,
            }

        async_started = connector_turn is not None and connector_turn.async_handle is not None
        if target_reply is None and connector_turn is not None and not async_started:
            target_reply = connector_turn.reply_text or None
        if home_direct:
            # Phase 13b D10: the direct call runs below, once the user
            # message is saved (the executor re-resolves its text); the
            # reply is Friday's fixed template -- no gateway call.
            gateway_stream, usage_holder = None, {}
            memory_ids, chunk_ids, overlay_source_workspaces = [], [], {}
        elif routed is not None and routed.reply_text is not None:
            # The route's stored text is the reply: no gateway call, no
            # ModelUsage (there is no call), no context assembly.
            gateway_stream, usage_holder = static_reply_stream(routed.reply_text), {}
            memory_ids, chunk_ids, overlay_source_workspaces = [], [], {}
        elif target_reply is not None:
            # Phase 13 D6: "which PC?" (or a refusal naming the PC) -- the
            # reply is Friday's fixed text: no chat completion, no node call.
            gateway_stream, usage_holder = static_reply_stream(target_reply), {}
            memory_ids, chunk_ids, overlay_source_workspaces = [], [], {}
        elif async_started:
            # Phase 12 D11: an async read was started -- the reply is
            # Friday's template, no chat completion this turn; the result
            # arrives later as one follow-up message.
            gateway_stream, usage_holder = static_reply_stream(connector_turn.reply_text), {}
            memory_ids, chunk_ids, overlay_source_workspaces = [], [], {}
        else:
            # Phase 12 D15: after the route, `router.intent` and the cap;
            # only for a turn answered by a chat completion. It selects
            # owner-approved text only -- the classification above is final.
            skill_turn = select_turn_skill(
                conversation,
                actor=request.user,
                content=turn_content,
                input_mode=input_mode,
                intent=classification["intent"],
                forced_name=forced_skill,
                connector_turn=connector_turn,
                context_report=turn_report or None,
            )
            gateway_stream, usage_holder, memory_ids, chunk_ids, overlay_source_workspaces = (
                build_gateway_stream(
                    conversation,
                    actor=request.user,
                    content=turn_content,
                    channel=channel,
                    intent=classification["intent"],
                    connector_turn=connector_turn,
                    report_extra=turn_report or None,
                    skill_turn=skill_turn,
                )
            )
        route_name = routed.route if routed is not None else None
        if typed_direct is not None and home_direct:
            # The typed home step answered: recorded as the home route (a
            # reply with no model call, so no memory extraction job).
            route_name = HOME_ROUTE_NAME
        # Phase 10 D13: a node action draft carries its source message's id
        # (the trust check's evidence anchor), so the row's id is fixed
        # first and written into the frozen intent payload.
        user_message_id = uuid.uuid4()
        if (
            classification["intent"] == Message.Intent.TOOL_REQUEST
            and classification["intent_payload"].get("tool_name") == NODE_ACTION_TOOL
        ):
            classification["intent_payload"]["payload"]["source_message_id"] = str(user_message_id)
        user_message = Message.objects.create(
            id=user_message_id,
            workspace=conversation.workspace,
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
            status=Message.Status.COMPLETE,
            created_by=request.user,
            intent=classification["intent"],
            intent_payload=classification["intent_payload"],
            channel=channel,
            input_mode=input_mode,
            route=route_name,
        )
        if home_direct:
            # ADR-018: the one direct call (D10 steps 1-6 in the executor,
            # which re-resolves this saved message's text and re-runs the
            # classifier first). Its outcome finalises this message's intent:
            # `home_direct` (attempted), or the gate-class card (nothing sent).
            direct_turn = run_direct_turn(
                classification,
                workspace=conversation.workspace,
                actor=request.user,
                message=user_message,
                channel=channel,
                input_mode=input_mode,
                node_id=origin_node_id,
            )
            classification = {
                "intent": direct_turn.intent,
                "intent_payload": direct_turn.intent_payload,
            }
            user_message.intent = direct_turn.intent
            user_message.intent_payload = direct_turn.intent_payload
            user_message.save(update_fields=["intent", "intent_payload", "updated_at"])
            gateway_stream = static_reply_stream(direct_turn.reply)
        if async_started:
            create_followup(
                conversation=conversation,
                source_message=user_message,
                actor=request.user,
                channel=channel,
                handle=connector_turn.async_handle,
                tool_label=connector_turn.tool_label,
                source=connector_turn.source,
                data_class=connector_turn.data_class,
            )

        # `draft` SSE event (MVP 6 §3/§4/§5): non-terminal, emitted only for
        # a task_draft/tool_request turn with a usable payload -- `done`'s
        # own contract is completely unchanged. `source_message_id` is this
        # already-persisted user message's id, which the frontend must send
        # back verbatim on confirm (as `draft_message_id` for tasks; tool
        # requests carry no such reference -- confirming one calls
        # approvals.services.create_request directly, which has no
        # provenance field to verify against).
        draft_event = None
        if classification["intent"] == Message.Intent.TASK_DRAFT:
            draft_event = {
                "type": "draft",
                "kind": "task",
                "source_message_id": str(user_message.id),
                "title": classification["intent_payload"]["title"],
                "due_date": classification["intent_payload"]["due_date"],
            }
        elif classification["intent"] == Message.Intent.TOOL_REQUEST:
            draft_event = {
                "type": "draft",
                "kind": "tool",
                "source_message_id": str(user_message.id),
                "tool_name": classification["intent_payload"]["tool_name"],
                "payload": classification["intent_payload"]["payload"],
            }
            direct_reason = classification["intent_payload"].get("direct_reason")
            if direct_reason:
                # Phase 13b D7: why this home card is not direct (a closed
                # code; the card shows its text).
                draft_event["direct_reason"] = direct_reason
            if classification["intent_payload"]["tool_name"] == NODE_ACTION_TOOL:
                # D8/D13: a confirm-class card calls the node actions
                # endpoint, a gate-class card `POST /approvals`; either way
                # the owner acts on screen, then on the Mac.
                draft_event["action_class"] = classification["intent_payload"]["action_class"]
                draft_event["where_to_act"] = NODE_ACTION_WHERE_TO_ACT

        response = StreamingHttpResponse(
            stream_assistant_reply(
                conversation,
                gateway_stream,
                usage_holder,
                request_id=request.request_id,
                memory_ids=memory_ids,
                chunk_ids=chunk_ids,
                overlay_source_workspaces=overlay_source_workspaces,
                draft_event=draft_event,
                channel=channel,
                connector_reads=connector_turn.reads if connector_turn else None,
                route=route_name,
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
