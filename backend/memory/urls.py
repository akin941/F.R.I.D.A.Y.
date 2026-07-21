from django.urls import path

from memory.views import (
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
]
