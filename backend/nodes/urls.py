from django.urls import path

from nodes.views import (
    NodeActionDetailView,
    NodeActionListView,
    NodeDetailView,
    NodeListView,
    NodePairView,
    NodeRevokeView,
)

urlpatterns = [
    path("nodes/pair", NodePairView.as_view(), name="node-pair"),
    # Phase 9 D11 (branch 4): the owner's node APIs.
    path("nodes", NodeListView.as_view(), name="node-list"),
    path("nodes/<uuid:node_id>", NodeDetailView.as_view(), name="node-detail"),
    path("nodes/<uuid:node_id>/revoke", NodeRevokeView.as_view(), name="node-revoke"),
    # Phase 10 D8: confirm-class actions and execution status.
    path("nodes/<uuid:node_id>/actions", NodeActionListView.as_view(), name="node-actions"),
    path(
        "nodes/<uuid:node_id>/actions/<uuid:execution_id>",
        NodeActionDetailView.as_view(),
        name="node-action-detail",
    ),
]
