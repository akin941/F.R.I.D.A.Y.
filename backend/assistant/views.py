import uuid

from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.authentication import PersonalAccessTokenAuthentication
from assistant.models import Conversation, Message
from assistant.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from assistant.services import (
    build_gateway_stream,
    classify_intent,
    create_conversation,
    stream_assistant_reply,
)
from core.channels import is_low_trust, request_channel, scoped_for_actor
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
        conversations = scoped_for_actor(
            request, Conversation.objects, workspace=workspace_uuid
        ).order_by("-created_at", "-id")
        return Response(ConversationSerializer(conversations, many=True).data)

    def post(self, request):
        if is_low_trust(request):
            # Resolve the bound workspace before serializer validation --
            # ConversationCreateSerializer declares `workspace` as required,
            # enforced before any view code runs.
            request.data["workspace"] = request.auth.bound_workspace_id
        serializer = ConversationCreateSerializer(data=request.data)
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
        )
        return Response(ConversationSerializer(conversation).data, status=status.HTTP_201_CREATED)


class ConversationMessageListView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES
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

    def _get_conversation(self, request, conversation_id):
        # By-id lookup, no workspace parameter at all -- scoped_for_actor
        # still forces a channel-bound request to its bound workspace, so a
        # PAT bound to workspace A can never reach a conversation in
        # workspace B by id (round-2 P1 fix).
        return scoped_for_actor(request, Conversation.objects).filter(id=conversation_id).first()

    def get(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        messages = (
            scoped_for_actor(request, Message.objects, workspace=conversation.workspace)
            .filter(conversation=conversation)
            .order_by("created_at", "id")
        )
        return Response(MessageSerializer(messages, many=True).data)

    def post(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]
        # Resolved once, threaded through to both this turn's user message
        # and its assistant reply (MVP 9 §1) -- never independently
        # re-derived for the reply, so the two rows can never drift apart.
        channel = request_channel(request)

        # Classify before building the reply context (MVP 6 §1) — a wholly
        # new step inserted ahead of the existing build_gateway_stream/persist
        # pair below, whose relative order is unchanged: build_gateway_stream
        # still runs before the user Message row exists, so build_context's
        # history query (which excludes the message being sent) is never at
        # risk of double-counting it.
        classification = classify_intent(
            conversation,
            actor=request.user,
            content=content,
            client_local_date=serializer.validated_data["client_local_date"],
        )

        gateway_stream, usage_holder, memory_ids, chunk_ids = build_gateway_stream(
            conversation, actor=request.user, content=content, intent=classification["intent"]
        )
        user_message = Message.objects.create(
            workspace=conversation.workspace,
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
            status=Message.Status.COMPLETE,
            created_by=request.user,
            intent=classification["intent"],
            intent_payload=classification["intent_payload"],
            channel=channel,
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

        response = StreamingHttpResponse(
            stream_assistant_reply(
                conversation,
                gateway_stream,
                usage_holder,
                request_id=request.request_id,
                memory_ids=memory_ids,
                chunk_ids=chunk_ids,
                draft_event=draft_event,
                channel=channel,
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
