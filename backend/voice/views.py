"""Voice API (Phase 6 branch 2, brief D1/D2/D9, ADR-011; Phase 11 D4/D5).

The three endpoints are the owner's trusted-device web session or, since
Phase 11, the node voice credential (`IsWebOrNodeVoiceChannel` +
`IsOwner`); `POST /voice/live/sessions` is the node voice credential only
(`IsNodeVoiceChannel` + `IsOwner`). Authentication classes are injected by the
composition root (`config/urls.py` -> `voice.urls.build_urlpatterns`):
`voice` may not import `accounts` (D11), yet a PAT must reach these views
to be refused with a deliberate 403 (`channel_forbidden`, or
`token_kind_forbidden` for an `openai` token) instead of an incidental 401
-- the deploys/approvals pattern. The facade and Telegram never reach them.

The conversation is looked up through `scoped_for_actor` (`for_actor()`):
another workspace's conversation is a 404 before any audio is read or any
model is called. Over the node voice channel, a conversation not opened via
`node_voice` is the same 404 (Phase 11 D4: the token reaches only what a
live session opened).
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.models import Conversation, Message
from assistant.services import create_conversation
from core.channels import CHANNEL_NODE_VOICE, request_channel, scoped_for_actor
from core.permissions import IsNodeVoiceChannel, IsOwner, IsWebOrNodeVoiceChannel
from core.throttling import ScopedRateThrottle
from core.tracing import request_trace_id
from core.uploads import MaxSizeUploadHandler
from voice.models import SOURCE_NODE, SOURCE_WEB
from voice.serializers import (
    ReceiptQuerySerializer,
    SpeechCreateSerializer,
    TranscriptionCreateSerializer,
)
from voice.services import (
    UploadTooLarge,
    VoiceServiceError,
    max_request_bytes,
    max_upload_bytes,
    synthesize_sentence,
    transcribe_utterance,
    usage_receipt,
)
from workspaces.models import Workspace


def _error(exc):
    return Response({"detail": str(exc), "code": exc.code}, status=exc.status)


def _conversation(request, conversation_id):
    conversation = (
        scoped_for_actor(request, Conversation.objects).filter(id=conversation_id).first()
    )
    if (
        conversation is not None
        and request_channel(request) == CHANNEL_NODE_VOICE
        and conversation.opened_via != Message.Channel.NODE_VOICE
    ):
        return None
    return conversation


def _trace_source(request):
    """Phase 11 D14: the trace's `source` comes from the channel, never
    from the body."""
    return SOURCE_NODE if request_channel(request) == CHANNEL_NODE_VOICE else SOURCE_WEB


def _content_length(request):
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return 0


class _VoiceView(APIView):
    permission_classes = [IsAuthenticated, IsWebOrNodeVoiceChannel, IsOwner]
    throttle_classes = [ScopedRateThrottle]

    def perform_content_negotiation(self, request, force=False):
        # The speech success body is raw `audio/wav`, never rendered by DRF;
        # every DRF `Response` (errors, the transcript) is JSON whatever the
        # client's Accept header says (the chat view's pattern).
        return (JSONRenderer(), "application/json")


class TranscriptionCreateView(_VoiceView):
    """`POST /api/v1/voice/transcriptions` -> `{text, audio_seconds,
    language}` (D1). The upload never touches disk (D2): only an in-memory
    handler is installed -- no `TemporaryFileUploadHandler` -- behind the
    streaming size guard, and the cap sits below Django's in-memory
    threshold. Over the cap -> 413 before parsing completes.

    Phase 13 D6: a `node_voice` request (once its conversation is found)
    marks the token's node as the owner's last active PC through
    `on_node_activity(device_id)`, a callable the composition root injects
    (`voice` may not import `nodes`); unset = nothing happens."""

    parser_classes = [MultiPartParser]
    throttle_scope = "voice_stt"
    on_node_activity = None

    def post(self, request):
        if _content_length(request) > max_request_bytes():
            return _error(UploadTooLarge())
        size_guard = MaxSizeUploadHandler(request, max_size=max_upload_bytes())
        request.upload_handlers = [size_guard, MemoryFileUploadHandler(request)]

        serializer = TranscriptionCreateSerializer(data=request.data)
        if size_guard.aborted:
            return _error(UploadTooLarge())
        serializer.is_valid(raise_exception=True)
        conversation = _conversation(request, serializer.validated_data["conversation"])
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        self._note_node_activity(request)
        audio = serializer.validated_data["audio"].read()
        try:
            body = transcribe_utterance(
                conversation=conversation,
                actor=request.user,
                audio=audio,
                trace_id=request_trace_id(request),
                source=_trace_source(request),
            )
        except VoiceServiceError as exc:
            return _error(exc)
        finally:
            del audio
        response = Response(body, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response

    def _note_node_activity(self, request):
        device_id = getattr(request.auth, "node_device_id", None)
        hook = self.on_node_activity
        if hook is None or device_id is None or request_channel(request) != CHANNEL_NODE_VOICE:
            return
        try:
            hook(device_id)
        except Exception:  # noqa: BLE001 -- bookkeeping never fails a transcription
            pass


class SpeechCreateView(_VoiceView):
    """`POST /api/v1/voice/speech` -> `audio/wav` (D1/D7), one sentence."""

    parser_classes = [JSONParser]
    throttle_scope = "voice_tts"

    def post(self, request):
        serializer = SpeechCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        conversation = _conversation(request, data["conversation"])
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            wav = synthesize_sentence(
                conversation=conversation,
                actor=request.user,
                text=data["text"],
                language=data["language"],
                trace_id=request_trace_id(request),
                timings=data.get("timings"),
                source=_trace_source(request),
            )
        except VoiceServiceError as exc:
            return _error(exc)
        response = HttpResponse(wav, content_type="audio/wav")
        response["Cache-Control"] = "no-store"
        response["Content-Length"] = str(len(wav))
        return response


class UsageReceiptView(_VoiceView):
    """`GET /api/v1/voice/receipt?conversation=<id>&trace=<uuid>` (Phase 6
    A7): the "bu ne kadar tuttu?" voice route's receipt -- a read of the
    turn's `ModelUsage` rows (by trace id) and today's workspace total.
    Same principal rules as the other voice endpoints; another workspace's
    conversation is a 404. Reads only: no model call, no write."""

    throttle_scope = "voice_receipt"

    def get(self, request):
        serializer = ReceiptQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        conversation = _conversation(request, data["conversation"])
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        trace = data.get("trace")
        body = usage_receipt(conversation=conversation, trace_id=str(trace) if trace else "")
        response = Response(body, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response


class LiveSessionCreateView(_VoiceView):
    """`POST /api/v1/voice/live/sessions` -> `{conversation_id, session_id,
    opened_at}` (Phase 11 D5). Node voice credential only. Reuses the most
    recent conversation of the bound workspace opened via `node_voice`
    whose last activity (last message, else its creation) is younger than
    VOICE_LIVE_CONVERSATION_REUSE_MINUTES -- an empty one included, never
    duplicated -- else opens one (`create_conversation(channel=
    "node_voice")`, audited by `assistant`). A conversation opened via any
    other channel is never reused. `session_id` is a fresh UUID the child
    puts on every trace of the session; no session row exists. Throttled
    (`node_voice_session`) so a misbehaving child cannot mint empty
    conversations in a loop. No model call."""

    permission_classes = [IsAuthenticated, IsNodeVoiceChannel, IsOwner]
    parser_classes = [JSONParser]
    throttle_scope = "node_voice_session"

    def post(self, request):
        now = timezone.now()
        cutoff = now - timedelta(minutes=settings.VOICE_LIVE_CONVERSATION_REUSE_MINUTES)
        workspace = scoped_for_actor(request, Workspace.objects).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            # Serializes two concurrent opens of the same workspace, so a
            # double request cannot create two live conversations. (Locked
            # by pk: `for_actor` is DISTINCT, which FOR UPDATE refuses.)
            workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
            conversation = (
                scoped_for_actor(request, Conversation.objects, workspace=workspace)
                .filter(opened_via=Message.Channel.NODE_VOICE)
                .annotate(last_activity=Coalesce(Max("messages__created_at"), "created_at"))
                .filter(last_activity__gte=cutoff)
                .order_by("-last_activity", "-id")
                .first()
            )
            if conversation is None:
                local = timezone.localtime(now)
                conversation = create_conversation(
                    workspace=workspace,
                    actor=request.user,
                    title=f"Canlı ses — {local:%Y-%m-%d %H:%M}",
                    channel=Message.Channel.NODE_VOICE,
                )
        response = Response(
            {
                "conversation_id": str(conversation.id),
                "session_id": str(uuid.uuid4()),
                "opened_at": now,
            },
            status=status.HTTP_201_CREATED,
        )
        response["Cache-Control"] = "no-store"
        return response
