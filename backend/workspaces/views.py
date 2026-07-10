from django.db.models import OuterRef, Subquery
from rest_framework.generics import ListAPIView

from workspaces.models import Workspace, WorkspaceMember
from workspaces.serializers import WorkspaceSerializer


class WorkspaceListView(ListAPIView):
    serializer_class = WorkspaceSerializer

    def get_queryset(self):
        role = WorkspaceMember.objects.filter(
            workspace=OuterRef("pk"), user=self.request.user
        ).values("role")[:1]
        return (
            Workspace.objects.for_actor(self.request.user)
            .annotate(role=Subquery(role))
            .order_by("created_at")
        )
