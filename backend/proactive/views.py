"""Owner-only, web-only surfaces of `proactive` (D3, D5, D20).

Default authentication (the trusted device's web session): a PAT, a node
secret or a node voice token is refused by authentication itself (401).
Permission classes refuse every other web principal (an agent, a member,
a non-owner: 403), and the services check the owner over the web again
(`core.owner_web`). Workspaces are resolved through `for_actor()`; a
workspace the actor is not a member of is a 404.

- `GET  /api/v1/proactive/schedules[?workspace=]`
- `PUT  /api/v1/proactive/schedules/<kind>[?workspace=]`
- `GET  /api/v1/proactive/notices[?workspace=&state=unread|all]`
- `POST /api/v1/proactive/notices/<id>/read`
- `POST /api/v1/proactive/notices/dismiss-all[?workspace=]`
- `GET | POST | DELETE /api/v1/proactive/pause[?workspace=]`
"""

import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from proactive import services
from proactive.models import ProactiveNotice, ProactiveSchedule
from proactive.serializers import NoticeSerializer, ScheduleSerializer, ScheduleWriteSerializer
from workspaces.models import Workspace

_PERMISSIONS = [IsAuthenticated, IsWebChannel, IsOwner]
NOTICE_LIST_LIMIT = 50


def _error(exc):
    if isinstance(exc, services.ProactivePermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    return service_error_response(exc, status.HTTP_400_BAD_REQUEST)


def _workspace(request):
    """The workspace named by `?workspace=` (or the body), else the
    actor's personal workspace -- always through `for_actor()`."""
    raw = request.query_params.get("workspace") or (
        request.data.get("workspace") if isinstance(request.data, dict) else None
    )
    workspaces = Workspace.objects.for_actor(request.user)
    if raw:
        try:
            uuid.UUID(str(raw))
        except ValueError:
            return None
        return workspaces.filter(pk=raw).first()
    return (
        workspaces.filter(type=Workspace.Type.PERSONAL, owner=request.user)
        .order_by("created_at")
        .first()
    )


def _not_found():
    return Response(status=status.HTTP_404_NOT_FOUND)


class ScheduleListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        rows = ProactiveSchedule.objects.for_actor(request.user, workspace=workspace).order_by(
            "kind"
        )
        return Response(
            {
                "workspace": str(workspace.pk),
                "paused": services.is_paused(workspace),
                "schedules": ScheduleSerializer(rows, many=True).data,
            }
        )


class ScheduleDetailView(APIView):
    permission_classes = _PERMISSIONS

    def put(self, request, kind):
        if kind not in ProactiveSchedule.Kind.values:
            return _not_found()
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        serializer = ScheduleWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        enabled = data.pop("enabled")
        try:
            schedule = services.save_schedule(
                actor=request.user,
                channel=request_channel(request),
                workspace=workspace,
                kind=kind,
                enabled=enabled,
                **data,
            )
        except services.ProactiveError as exc:
            return _error(exc)
        return Response(ScheduleSerializer(schedule).data)


class NoticeListView(APIView):
    permission_classes = _PERMISSIONS

    def get(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        rows = ProactiveNotice.objects.for_actor(request.user, workspace=workspace)
        state = request.query_params.get("state") or "unread"
        if state == "unread":
            rows = rows.filter(read_at__isnull=True, dismissed_at__isnull=True)
        elif state != "all":
            return Response(
                {"detail": "state is unread or all.", "code": "invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        unread = ProactiveNotice.objects.for_actor(request.user, workspace=workspace).filter(
            read_at__isnull=True, dismissed_at__isnull=True
        )
        rows = rows.order_by("-created_at")[:NOTICE_LIST_LIMIT]
        return Response(
            {
                "workspace": str(workspace.pk),
                "unread": unread.count(),
                "notices": NoticeSerializer(rows, many=True).data,
            }
        )


class NoticeReadView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request, notice_id):
        notice = ProactiveNotice.objects.for_actor(request.user).filter(pk=notice_id).first()
        if notice is None:
            return _not_found()
        try:
            services.mark_notice_read(
                actor=request.user, channel=request_channel(request), notice=notice
            )
        except services.ProactiveError as exc:
            return _error(exc)
        return Response(NoticeSerializer(notice).data)


class NoticeDismissAllView(APIView):
    permission_classes = _PERMISSIONS

    def post(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        try:
            count = services.dismiss_all_notices(
                actor=request.user, channel=request_channel(request), workspace=workspace
            )
        except services.ProactiveError as exc:
            return _error(exc)
        return Response({"dismissed": count})


class PauseView(APIView):
    """`GET` reads the state; `POST` pauses, `DELETE` resumes (both
    idempotent)."""

    permission_classes = _PERMISSIONS

    def get(self, request):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        return Response({"workspace": str(workspace.pk), "paused": services.is_paused(workspace)})

    def _set(self, request, paused):
        workspace = _workspace(request)
        if workspace is None:
            return _not_found()
        try:
            state = services.set_pause(
                actor=request.user,
                channel=request_channel(request),
                workspace=workspace,
                paused=paused,
            )
        except services.ProactiveError as exc:
            return _error(exc)
        return Response({"workspace": str(workspace.pk), "paused": state})

    def post(self, request):
        return self._set(request, True)

    def delete(self, request):
        return self._set(request, False)
