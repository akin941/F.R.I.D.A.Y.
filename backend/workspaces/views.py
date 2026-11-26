from django.db import transaction
from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsWebChannel
from workspaces import rooms
from workspaces.models import (
    LABEL_ALIAS_MAX_CHARS,
    LABEL_ALIAS_MIN_CHARS,
    MAX_LABEL_ALIASES,
    ROOM_NAME_MAX_CHARS,
    Room,
    Workspace,
    WorkspaceMember,
)
from workspaces.serializers import (
    FirmWorkspaceCreateSerializer,
    RoomSerializer,
    RoomWriteSerializer,
    WorkspaceDetailSerializer,
    WorkspaceProfileUpdateSerializer,
    WorkspaceSerializer,
)
from workspaces.services import (
    WorkspacePermissionDenied,
    create_firm_workspace,
    rename_workspace,
    update_profile,
)


def _role_subquery(request):
    return WorkspaceMember.objects.filter(workspace=OuterRef("pk"), user=request.user).values(
        "role"
    )[:1]


def _annotated(request):
    return Workspace.objects.for_actor(request.user).annotate(
        role=Subquery(_role_subquery(request))
    )


class WorkspaceListView(APIView):
    """`GET` lists the actor's workspaces; `POST` creates a firm (company)
    workspace -- owner-only, web-only (Phase 4 branch 2)."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        workspaces = _annotated(request).order_by("created_at")
        return Response(WorkspaceSerializer(workspaces, many=True).data)

    def post(self, request):
        serializer = FirmWorkspaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            workspace = create_firm_workspace(
                actor=request.user,
                name=serializer.validated_data["name"],
                profile=serializer.validated_data.get("profile"),
                channel=request_channel(request),
            )
        except WorkspacePermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        workspace = _annotated(request).get(pk=workspace.pk)
        return Response(WorkspaceDetailSerializer(workspace).data, status=status.HTTP_201_CREATED)


class WorkspaceDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def _get_workspace(self, request, workspace_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak),
        # mirroring tasks.views.TaskDetailView / agents.views.AgentDetailView.
        return (
            Workspace.objects.for_actor(request.user, workspace=workspace_id)
            .annotate(role=Subquery(_role_subquery(request)))
            .first()
        )

    def get(self, request, workspace_id):
        workspace = self._get_workspace(request, workspace_id)
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(WorkspaceDetailSerializer(workspace).data)

    def patch(self, request, workspace_id):
        workspace = self._get_workspace(request, workspace_id)
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = WorkspaceProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                if "name" in serializer.validated_data:
                    rename_workspace(
                        workspace=workspace,
                        actor=request.user,
                        name=serializer.validated_data["name"],
                    )
                if "profile" in serializer.validated_data:
                    update_profile(
                        workspace=workspace,
                        actor=request.user,
                        profile=serializer.validated_data["profile"],
                    )
        except WorkspacePermissionDenied as exc:
            return service_error_response(exc, status.HTTP_403_FORBIDDEN)
        # Re-fetch for output: `update_profile` returns a lock-query instance
        # without the `role` annotation the serializer needs.
        workspace = self._get_workspace(request, workspace_id)
        return Response(WorkspaceDetailSerializer(workspace).data)


# --- rooms (Phase 13 D2) ---


def _personal_workspace(request, workspace_id):
    """The actor's PERSONAL workspace `workspace_id` through `for_actor()`,
    or None: another user's workspace and a firm both answer 404 (a firm
    has no "salon")."""
    return (
        Workspace.objects.for_actor(request.user, workspace=workspace_id)
        .filter(type=Workspace.Type.PERSONAL)
        .first()
    )


def _room_refusal(exc):
    if isinstance(exc, rooms.RoomsPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    return service_error_response(exc, exc.http_status)


class RoomListView(APIView):
    """`GET /api/v1/workspaces/<id>/rooms` lists the personal workspace's
    rooms; `POST` creates one. Owner, human, web channel only (the service
    re-checks `require_owner_web`); a firm or another user's workspace is
    404."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request, workspace_id):
        workspace = _personal_workspace(request, workspace_id)
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rooms.require_rooms_owner_web(
                actor=request.user, workspace=workspace, channel=request_channel(request)
            )
        except rooms.RoomsPermissionDenied as exc:
            return _room_refusal(exc)
        rows = rooms.list_rooms(actor=request.user, workspace=workspace)
        return Response(
            {
                "workspace": str(workspace.pk),
                "limits": {
                    "aliases": MAX_LABEL_ALIASES,
                    "alias_min_chars": LABEL_ALIAS_MIN_CHARS,
                    "alias_max_chars": LABEL_ALIAS_MAX_CHARS,
                    "name_max_chars": ROOM_NAME_MAX_CHARS,
                },
                "results": RoomSerializer(rows, many=True).data,
            }
        )

    def post(self, request, workspace_id):
        workspace = _personal_workspace(request, workspace_id)
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = RoomWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            room = rooms.create_room(
                actor=request.user,
                workspace=workspace,
                channel=request_channel(request),
                name=data["name"],
                aliases=data.get("aliases"),
                sort_order=data.get("sort_order"),
            )
        except (rooms.LabelError, rooms.RoomsPermissionDenied) as exc:
            return _room_refusal(exc)
        return Response(RoomSerializer(room).data, status=status.HTTP_201_CREATED)


class RoomDetailView(APIView):
    """`PATCH` / `DELETE /api/v1/workspaces/<id>/rooms/<room_id>`. A room
    in use by a node is `409 room.in_use`."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def _room(self, request, workspace_id, room_id):
        workspace = _personal_workspace(request, workspace_id)
        if workspace is None:
            return None
        return Room.objects.for_actor(request.user, workspace=workspace).filter(pk=room_id).first()

    def patch(self, request, workspace_id, room_id):
        room = self._room(request, workspace_id, room_id)
        if room is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = RoomWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            room = rooms.update_room(
                actor=request.user,
                room=room,
                channel=request_channel(request),
                name=data.get("name", rooms.UNSET),
                aliases=data.get("aliases", rooms.UNSET),
                sort_order=data.get("sort_order", rooms.UNSET),
            )
        except (rooms.LabelError, rooms.RoomsPermissionDenied) as exc:
            return _room_refusal(exc)
        return Response(RoomSerializer(room).data)

    def delete(self, request, workspace_id, room_id):
        room = self._room(request, workspace_id, room_id)
        if room is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rooms.delete_room(actor=request.user, room=room, channel=request_channel(request))
        except (rooms.LabelError, rooms.RoomsPermissionDenied) as exc:
            return _room_refusal(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)
