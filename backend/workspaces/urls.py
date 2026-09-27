from django.urls import path

from workspaces.views import WorkspaceDetailView, WorkspaceListView

urlpatterns = [
    # config/urls.py mounts this module twice: bare "api/v1/workspaces" (no
    # trailing slash) for this exact list route, and "api/v1/workspaces/"
    # (trailing slash) so the detail route below gets its "/" separator,
    # giving "api/v1/workspaces/<uuid>".
    path("", WorkspaceListView.as_view(), name="workspace-list"),
    path("<uuid:workspace_id>", WorkspaceDetailView.as_view(), name="workspace-detail"),
]
