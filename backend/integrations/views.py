"""Telegram link management (MVP 9 §4) -- web/JWT-authenticated only, the
same as minting a PersonalAccessToken (accounts/views.py): both mint or
manage a credential a low-trust channel could otherwise use to reach in and
widen its own trust, so neither is ever allowlisted for PAT auth. The
webhook view (below) is the one deliberate exception -- Telegram itself is
not a Jarvis user and holds no JWT, so it authenticates via a shared
secret header instead (see TelegramWebhookView)."""

import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsWebChannel
from integrations.models import LinkedTelegramChat, TelegramLinkCode
from integrations.serializers import TelegramLinkCodeCreateSerializer
from integrations.tasks import process_telegram_update
from workspaces.models import Workspace

# Short-lived on purpose: a code pasted into Telegram within a few minutes
# of being generated, never a standing credential.
LINK_CODE_TTL_MINUTES = 10


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error).
    Mirrors every other app's identical helper (agents.views, etc.)."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        return None, Response(
            {"detail": "workspace query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        workspace_uuid = uuid.UUID(workspace_id)
    except ValueError:
        return None, Response(
            {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
        )
    workspace = Workspace.objects.for_actor(request.user, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


class TelegramLinkCodeCreateView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def post(self, request):
        serializer = TelegramLinkCodeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        code = secrets.token_urlsafe(16)
        expires_at = timezone.now() + timedelta(minutes=LINK_CODE_TTL_MINUTES)
        TelegramLinkCode.objects.create(
            workspace=workspace, user=request.user, code=code, expires_at=expires_at
        )
        return Response({"code": code, "expires_at": expires_at}, status=status.HTTP_201_CREATED)


class TelegramLinkStatusView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        # At most one row per (user, workspace) -- the model's own
        # UniqueConstraint (MVP 9 §4).
        link = LinkedTelegramChat.objects.filter(user=request.user, workspace=workspace).first()
        if link is None:
            return Response({"linked": False, "chat_id": None, "linked_at": None})
        return Response({"linked": True, "chat_id": link.chat_id, "linked_at": link.created_at})


class TelegramUnlinkView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def delete(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        # Hard delete is fine here (unlike a PersonalAccessToken's
        # soft-revoke): the row carries no audit value once gone -- the
        # Conversation/Message rows it pointed at persist independently and
        # remain the actual audit trail of what was said.
        LinkedTelegramChat.objects.filter(user=request.user, workspace=workspace).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TelegramWebhookView(APIView):
    """Telegram is not a Jarvis user and holds no JWT -- authenticity is
    verified via the `X-Telegram-Bot-Api-Secret-Token` header instead
    (Telegram's own documented webhook-authenticity mechanism, set via
    `manage.py register_telegram_webhook`). Rejects with 403 before
    touching the database or enqueuing anything if the header doesn't
    match. On a verified request, this view does the minimum possible
    work -- enqueue the raw update and return 200 immediately -- the
    actual work (a possibly slow LLM gateway call) must never run inside
    this response cycle, or Telegram's at-least-once redelivery would
    double-process a slow reply."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        configured = settings.TELEGRAM_WEBHOOK_SECRET
        # An unconfigured secret must never "match" an empty/missing
        # header -- fail closed, not fail open. hmac.compare_digest for a
        # timing-safe comparison of the two. Also require a bot token: the
        # documented setup registers the webhook only once both are set, so
        # a missing token means this deployment isn't actually configured
        # for Telegram yet -- accepting traffic anyway would let a /link
        # update consume a code and create a link before the reply attempt
        # fails against an invalid Bot API URL (Codex review, branch 3
        # round 4).
        if (
            not configured
            or not settings.TELEGRAM_BOT_TOKEN
            or not hmac.compare_digest(configured, provided)
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        process_telegram_update.delay(request.data)
        return Response(status=status.HTTP_200_OK)
