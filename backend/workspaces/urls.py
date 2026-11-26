from django.urls import path

from workspaces.views import (
    RoomDetailView,
    RoomListView,
    WorkspaceDetailView,
    WorkspaceListView,
)

urlpatterns = [
    # config/urls.py mounts this module twice: bare "api/v1/workspaces" (no
    # trailing slash) for this exact list route, and "api/v1/workspaces/"
    # (trailing slash) so the detail route below gets its "/" separator,
    # giving "api/v1/workspaces/<uuid>".
    path("", WorkspaceListView.as_view(), name="workspace-list"),
    path("<uuid:workspace_id>", WorkspaceDetailView.as_view(), name="workspace-detail"),
    # Phase 13 D2: the personal workspace's rooms.
    path("<uuid:workspace_id>/rooms", RoomListView.as_view(), name="workspace-rooms"),
    path(
        "<uuid:workspace_id>/rooms/<uuid:room_id>",
        RoomDetailView.as_view(),
        name="workspace-room-detail",
    ),
]
