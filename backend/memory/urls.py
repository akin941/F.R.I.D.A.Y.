from django.urls import path

from memory.ingestion.views import (
    IngestionSourceActionView,
    IngestionSourceDetailView,
    IngestionSourceListView,
    MappingRuleDetailView,
    MappingRuleListView,
    ScrubTermDetailView,
    ScrubTermListView,
)
from memory.views import (
    CoreBlockDetailView,
    CoreBlockListView,
    CoreBlockRollbackView,
    CoreBlockVersionListView,
    MemoryDetailView,
    MemoryExportView,
    MemoryListCreateView,
    MemoryTransitionView,
)

urlpatterns = [
    path("memories", MemoryListCreateView.as_view(), name="memory-list-create"),
    path("memories/export", MemoryExportView.as_view(), name="memory-export"),
    path("memories/<uuid:memory_id>", MemoryDetailView.as_view(), name="memory-detail"),
    path(
        "memories/<uuid:memory_id>/<str:action>",
        MemoryTransitionView.as_view(),
        name="memory-transition",
    ),
    # Core memory blocks (Phase 8A D7): owner, web session only.
    path("memory/core-blocks", CoreBlockListView.as_view(), name="core-block-list"),
    path(
        "memory/core-blocks/<slug:label>",
        CoreBlockDetailView.as_view(),
        name="core-block-detail",
    ),
    path(
        "memory/core-blocks/<slug:label>/versions",
        CoreBlockVersionListView.as_view(),
        name="core-block-versions",
    ),
    path(
        "memory/core-blocks/<slug:label>/rollback",
        CoreBlockRollbackView.as_view(),
        name="core-block-rollback",
    ),
    # Phase 8A branch 1 (ADR-012): the owner's ingestion controls and the
    # D11 audit view. Owner, web channel only.
    path(
        "memory/ingestion/sources",
        IngestionSourceListView.as_view(),
        name="memory-ingestion-sources",
    ),
    path(
        "memory/ingestion/sources/<uuid:source_id>",
        IngestionSourceDetailView.as_view(),
        name="memory-ingestion-source-detail",
    ),
    path(
        "memory/ingestion/sources/<uuid:source_id>/rules",
        MappingRuleListView.as_view(),
        name="memory-ingestion-rules",
    ),
    path(
        "memory/ingestion/sources/<uuid:source_id>/<str:action>",
        IngestionSourceActionView.as_view(),
        name="memory-ingestion-source-action",
    ),
    path(
        "memory/ingestion/rules/<uuid:rule_id>",
        MappingRuleDetailView.as_view(),
        name="memory-ingestion-rule-detail",
    ),
    path(
        "memory/ingestion/scrub-terms",
        ScrubTermListView.as_view(),
        name="memory-ingestion-scrub-terms",
    ),
    path(
        "memory/ingestion/scrub-terms/<uuid:term_id>",
        ScrubTermDetailView.as_view(),
        name="memory-ingestion-scrub-term-detail",
    ),
]
