from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from config.activity import ActivitySummaryView


def healthz(request):
    """Liveness probe used by Docker healthchecks and (later) monitoring."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("api/v1/auth/", include("accounts.urls")),
    # Mounted twice: bare (no trailing slash) so the pre-existing list route
    # keeps matching "/api/v1/workspaces" exactly (existing tests assert
    # this precise path), and with a trailing slash so
    # "<uuid:workspace_id>" (workspaces/urls.py) gets the "/" separator it
    # needs for "/api/v1/workspaces/<uuid>" -- avoids a leading-slash route
    # pattern, which trips Django's urls.W002 check.
    path("api/v1/workspaces", include("workspaces.urls")),
    path("api/v1/workspaces/", include("workspaces.urls")),
    path("api/v1/ai/", include("assistant.urls")),
    path("api/v1/", include("memory.urls")),
    path("api/v1/", include("knowledge.urls")),
    path("api/v1/", include("tasks.urls")),
    path("api/v1/", include("approvals.urls")),
    path("api/v1/", include("agents.urls")),
    path("api/v1/", include("integrations.urls")),
    path("api/v1/", include("harness.urls")),
    path("api/v1/", include("connectors.urls")),
    # Cross-app read-only aggregate for the shell's badges (Phase 4
    # branch 3); lives in the composition root, see config/activity.py.
    path("api/v1/activity/summary", ActivitySummaryView.as_view(), name="activity-summary"),
    # No audit-log admin by design: Django admin bypasses `for_actor()`
    # workspace scoping (ADR-001; see approvals/admin.py for the same
    # argument). The audit trail is read through scoped application code.
]

# Django admin is break-glass only (Phase 4 Q3): routed when
# DJANGO_ADMIN_ENABLED (dev default true, prod default false). The owner
# password is accepted nowhere else.
if settings.DJANGO_ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
