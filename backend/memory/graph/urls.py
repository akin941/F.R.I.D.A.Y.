"""Routes of the owner graph API, mounted by `memory/urls.py` under
`memory/graph/`."""

from django.urls import path

from memory.graph.views import (
    EntityDetailView,
    EntityFactsView,
    EntityListView,
    EntityRestoreView,
    FactActionView,
    FactDetailView,
    FactListCreateView,
)

urlpatterns = [
    path("memory/graph/entities", EntityListView.as_view(), name="memory-graph-entities"),
    path(
        "memory/graph/entities/<uuid:entity_id>",
        EntityDetailView.as_view(),
        name="memory-graph-entity-detail",
    ),
    path(
        "memory/graph/entities/<uuid:entity_id>/facts",
        EntityFactsView.as_view(),
        name="memory-graph-entity-facts",
    ),
    path(
        "memory/graph/entities/<uuid:entity_id>/restore",
        EntityRestoreView.as_view(),
        name="memory-graph-entity-restore",
    ),
    path("memory/graph/facts", FactListCreateView.as_view(), name="memory-graph-facts"),
    path(
        "memory/graph/facts/<uuid:fact_id>",
        FactDetailView.as_view(),
        name="memory-graph-fact-detail",
    ),
    path(
        "memory/graph/facts/<uuid:fact_id>/<str:action>",
        FactActionView.as_view(),
        name="memory-graph-fact-action",
    ),
]
