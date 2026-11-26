from django.urls import path

from connectors.views import (
    ConnectorBindingDetailView,
    ConnectorBindingListView,
    ConnectorRecentReadsView,
    HomeEntityCandidatesView,
    HomeEntityDetailView,
    HomeEntityListView,
)

urlpatterns = [
    path("connectors/bindings", ConnectorBindingListView.as_view(), name="connector-binding-list"),
    path("connectors/reads", ConnectorRecentReadsView.as_view(), name="connector-recent-reads"),
    path(
        "connectors/bindings/<str:connector>",
        ConnectorBindingDetailView.as_view(),
        name="connector-binding-detail",
    ),
    # Phase 13 D11: the owner's Home Assistant entity allowlist.
    path(
        "connectors/home-assistant/entities",
        HomeEntityListView.as_view(),
        name="ha-entity-list",
    ),
    path(
        "connectors/home-assistant/entities/<uuid:entity_pk>",
        HomeEntityDetailView.as_view(),
        name="ha-entity-detail",
    ),
    path(
        "connectors/home-assistant/candidates",
        HomeEntityCandidatesView.as_view(),
        name="ha-entity-candidates",
    ),
]
