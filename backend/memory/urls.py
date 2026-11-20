from django.urls import path

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
]
