"""Phase 12 D8: `/api/v1/assistant/wake-routes` (mounted by `config.urls`)."""

from django.urls import path

from assistant.wake_views import WakeAliasCreateView, WakeAliasDetailView, WakeRouteListView

urlpatterns = [
    path("wake-routes", WakeRouteListView.as_view(), name="assistant-wake-routes"),
    path("wake-routes/aliases", WakeAliasCreateView.as_view(), name="assistant-wake-aliases"),
    path(
        "wake-routes/aliases/<uuid:alias_id>",
        WakeAliasDetailView.as_view(),
        name="assistant-wake-alias-detail",
    ),
]
