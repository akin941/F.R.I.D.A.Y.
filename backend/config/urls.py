from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.settings import api_settings

from accounts.authentication import PersonalAccessTokenAuthentication
from config.activity import ActivitySummaryView
from deploys.urls import build_urlpatterns as deploy_urlpatterns
from hud.urls import build_urlpatterns as hud_urlpatterns
from voice.urls import build_urlpatterns as voice_urlpatterns


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
    # Phase 5 branch 6: `deploys` may not import `accounts` (ADR-010), so the
    # PAT authenticator (which must precede the device session, see
    # accounts/authentication.py) is injected here, in the composition root.
    path(
        "api/v1/",
        include(
            deploy_urlpatterns(
                authentication_classes=[
                    PersonalAccessTokenAuthentication,
                    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
                ]
            )
        ),
    ),
    # Phase 6 branch 2: same injection for `voice` (D11: it may not import
    # `accounts`); the PAT authenticator is there only so a PAT gets the
    # views' deliberate 403 -- both endpoints are web-session only.
    path(
        "api/v1/",
        include(
            voice_urlpatterns(
                authentication_classes=[
                    PersonalAccessTokenAuthentication,
                    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
                ]
            )
        ),
    ),
    # Phase 6 branch 5: the HUD event stream, same injection (`hud` may not
    # import `accounts`); web-session owner only.
    path(
        "api/v1/",
        include(
            hud_urlpatterns(
                authentication_classes=[
                    PersonalAccessTokenAuthentication,
                    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
                ]
            )
        ),
    ),
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
