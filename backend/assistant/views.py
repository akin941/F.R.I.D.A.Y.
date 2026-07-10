import uuid

from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

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
from workspaces.models import Workspace


class ConversationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace_id = request.query_params.get("workspace")
        if not workspace_id:
            return Response(
                {"detail": "workspace query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            return Response(
                {"detail": "workspace must be a valid UUID."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        conversations = Conversation.objects.for_actor(
            request.user, workspace=workspace_uuid
        ).order_by("-created_at", "-id")
        return Response(ConversationSerializer(conversations, many=True).data)

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
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
        return Conversation.objects.for_actor(request.user).filter(id=conversation_id).first()

    def get(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        messages = (
            Message.objects.for_actor(request.user, workspace=conversation.workspace)
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
            ),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
