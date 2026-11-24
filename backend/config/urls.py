from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.settings import api_settings

from accounts.authentication import (
    HookTokenAuthentication,
    NodeVoiceTokenAuthentication,
    PersonalAccessTokenAuthentication,
)
from accounts.voice_live import NodeVoiceStormGuard, NodeVoiceTriggerGuard
from config.activity import ActivitySummaryView
from deploys.urls import build_urlpatterns as deploy_urlpatterns
from hud.urls import build_urlpatterns as hud_urlpatterns
from memory.urls import build_hook_urlpatterns
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
    # Phase 12 D8: wake routes and their owner aliases (owner, web).
    path("api/v1/assistant/", include("assistant.wake_urls")),
    path("api/v1/", include("memory.urls")),
    # Phase 8A branch 3 (D6): the append-only Claude Code hook-event ingest.
    # Its ONLY authenticator is the hook PAT (no device session, no other
    # PAT kind); `memory` may not import `accounts`, so it is injected here.
    path(
        "api/v1/",
        include(build_hook_urlpatterns(authentication_classes=[HookTokenAuthentication])),
    ),
    path("api/v1/", include("knowledge.urls")),
    path("api/v1/", include("tasks.urls")),
    path("api/v1/", include("approvals.urls")),
    path("api/v1/", include("agents.urls")),
    path("api/v1/", include("integrations.urls")),
    path("api/v1/", include("harness.urls")),
    path("api/v1/", include("connectors.urls")),
    # Phase 9 branch 2 (D4): anonymous node pairing. `nodes` may import
    # `accounts`, so nothing needs injecting here.
    path("api/v1/", include("nodes.urls")),
    # Phase 12 (D3, D5): schedules, notices, pause -- owner-only, web only.
    path("api/v1/", include("proactive.urls")),
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
    # `accounts`). Phase 11 D4: `NodeVoiceTokenAuthentication` replaces the
    # base PAT authenticator, so the node voice credential reaches the
    # voice routes while a `cli` PAT still authenticates as `cli` and gets
    # the views' deliberate 403 (the permission class) and `openai`/`hook`
    # tokens their `token_kind_forbidden`. Phase 11 D11/D12 (branch 5): the
    # `accounts` guards ride the same seam -- the trigger backstop on the
    # transcription and live-session routes, the storm backstop on the
    # transcription route only.
    path(
        "api/v1/",
        include(
            voice_urlpatterns(
                authentication_classes=[
                    NodeVoiceTokenAuthentication,
                    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
                ],
                node_voice_permissions=(NodeVoiceTriggerGuard,),
                transcription_permissions=(NodeVoiceStormGuard,),
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
