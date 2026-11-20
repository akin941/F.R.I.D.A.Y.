"""Deploy routes. No module-level `urlpatterns`: the composition root
(`config/urls.py`) calls `build_urlpatterns` with the authentication
classes, because `deploys` may not import `accounts` (ADR-010 contracts)
while its CLI surfaces need the PAT authenticator ahead of the device
session (see `deploys/views.py`)."""

from django.urls import path

from deploys.views import (
    DeployRequestCreateView,
    DeployRunCancelView,
    DeployRunDetailView,
    DeployRunListView,
    DeployTargetDetailView,
    DeployTargetHeadView,
    DeployTargetListCreateView,
    DeployTargetStatusView,
)


def build_urlpatterns(*, authentication_classes):
    auth = {"authentication_classes": list(authentication_classes)}
    return [
        path(
            "deploys/targets",
            DeployTargetListCreateView.as_view(**auth),
            name="deploy-target-list",
        ),
        path(
            "deploys/targets/<uuid:target_id>",
            DeployTargetDetailView.as_view(**auth),
            name="deploy-target-detail",
        ),
        path(
            "deploys/targets/<uuid:target_id>/status",
            DeployTargetStatusView.as_view(**auth),
            name="deploy-target-status",
        ),
        path(
            "deploys/targets/<uuid:target_id>/head",
            DeployTargetHeadView.as_view(**auth),
            name="deploy-target-head",
        ),
        path("deploys/runs", DeployRunListView.as_view(**auth), name="deploy-run-list"),
        path(
            "deploys/runs/<uuid:run_id>",
            DeployRunDetailView.as_view(**auth),
            name="deploy-run-detail",
        ),
        path(
            "deploys/runs/<uuid:run_id>/cancel",
            DeployRunCancelView.as_view(**auth),
            name="deploy-run-cancel",
        ),
        path(
            "deploys/requests",
            DeployRequestCreateView.as_view(**auth),
            name="deploy-request-create",
        ),
    ]
