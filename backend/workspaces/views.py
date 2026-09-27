from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspaces.models import Workspace, WorkspaceMember
from workspaces.serializers import (
    WorkspaceDetailSerializer,
    WorkspaceProfileUpdateSerializer,
    WorkspaceSerializer,
)
from workspaces.services import WorkspacePermissionDenied, update_profile


def _role_subquery(request):
    return WorkspaceMember.objects.filter(workspace=OuterRef("pk"), user=request.user).values(
        "role"
    )[:1]


class WorkspaceListView(ListAPIView):
    serializer_class = WorkspaceSerializer

    def get_queryset(self):
        return (
            Workspace.objects.for_actor(self.request.user)
            .annotate(role=Subquery(_role_subquery(self.request)))
            .order_by("created_at")
        )


class WorkspaceDetailView(APIView):
    permission_classes = [IsAuthenticated]

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
