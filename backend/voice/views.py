"""Voice API (Phase 6 branch 2, brief D1/D2/D9, ADR-011).

Both endpoints are the owner's trusted-device web session only
(`IsWebChannel` + `IsOwner`). Authentication classes are injected by the
composition root (`config/urls.py` -> `voice.urls.build_urlpatterns`):
`voice` may not import `accounts` (D11), yet a PAT must reach these views
to be refused with a deliberate 403 (`channel_forbidden`, or
`token_kind_forbidden` for an `openai` token) instead of an incidental 401
-- the deploys/approvals pattern. The facade and Telegram never reach them.

The conversation is looked up through `scoped_for_actor` (`for_actor()`):
another workspace's conversation is a 404 before any audio is read or any
model is called.
"""

from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.http import HttpResponse
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.models import Conversation
from core.channels import scoped_for_actor
from core.permissions import IsOwner, IsWebChannel
from core.throttling import ScopedRateThrottle
from core.tracing import request_trace_id
from core.uploads import MaxSizeUploadHandler
from voice.serializers import SpeechCreateSerializer, TranscriptionCreateSerializer
from voice.services import (
    UploadTooLarge,
    VoiceServiceError,
    max_request_bytes,
    max_upload_bytes,
    synthesize_sentence,
    transcribe_utterance,
)


def _error(exc):
    return Response({"detail": str(exc), "code": exc.code}, status=exc.status)


def _conversation(request, conversation_id):
    return scoped_for_actor(request, Conversation.objects).filter(id=conversation_id).first()


def _content_length(request):
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return 0


class _VoiceView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]
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
    threshold. Over the cap -> 413 before parsing completes."""

    parser_classes = [MultiPartParser]
    throttle_scope = "voice_stt"

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
        audio = serializer.validated_data["audio"].read()
        try:
            body = transcribe_utterance(
                conversation=conversation,
                actor=request.user,
                audio=audio,
                trace_id=request_trace_id(request),
            )
        except VoiceServiceError as exc:
            return _error(exc)
        finally:
            del audio
        response = Response(body, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response


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
            )
        except VoiceServiceError as exc:
            return _error(exc)
        response = HttpResponse(wav, content_type="audio/wav")
        response["Cache-Control"] = "no-store"
        response["Content-Length"] = str(len(wav))
        return response
