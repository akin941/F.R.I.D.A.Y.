from django.urls import path

from nodes.views import NodeActionDetailView, NodeActionListView, NodePairView

urlpatterns = [
    path("nodes/pair", NodePairView.as_view(), name="node-pair"),
    # Phase 10 D8: confirm-class actions and execution status.
    path("nodes/<uuid:node_id>/actions", NodeActionListView.as_view(), name="node-actions"),
    path(
        "nodes/<uuid:node_id>/actions/<uuid:execution_id>",
        NodeActionDetailView.as_view(),
        name="node-action-detail",
    ),
]
