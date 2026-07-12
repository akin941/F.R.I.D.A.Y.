from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    """Liveness probe used by Docker healthchecks and (later) monitoring."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/workspaces", include("workspaces.urls")),
    path("api/v1/ai/", include("assistant.urls")),
    path("api/v1/", include("memory.urls")),
    path("api/v1/", include("knowledge.urls")),
    path("api/v1/", include("tasks.urls")),
    path("api/v1/", include("approvals.urls")),
    path("api/v1/", include("agents.urls")),
    # TODO(MVP 1, next branches): audit admin wiring.
]
