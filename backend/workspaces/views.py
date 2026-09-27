from django.db import transaction
from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.channels import request_channel
from core.permissions import IsWebChannel
from workspaces.models import Workspace, WorkspaceMember
from workspaces.serializers import (
    FirmWorkspaceCreateSerializer,
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
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        # Re-fetch for output: `update_profile` returns a lock-query instance
        # without the `role` annotation the serializer needs.
        workspace = self._get_workspace(request, workspace_id)
        return Response(WorkspaceDetailSerializer(workspace).data)
