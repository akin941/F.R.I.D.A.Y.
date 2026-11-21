"""HUD routes. No module-level `urlpatterns`: the composition root
(`config/urls.py`) calls `build_urlpatterns` with the authentication
classes, because `hud` may not import `accounts` while a PAT must reach the
view to be refused with a 403 (the voice/deploys pattern)."""

from django.urls import path

from hud.views import HudEventsView, HudTerminalStreamView


def build_urlpatterns(*, authentication_classes):
    auth = {"authentication_classes": list(authentication_classes)}
    return [
        path("hud/events", HudEventsView.as_view(**auth), name="hud-events"),
        # Phase 10 D12: one node action execution's scrubbed output.
        path(
            "hud/terminal/<uuid:execution_id>/stream",
            HudTerminalStreamView.as_view(**auth),
            name="hud-terminal-stream",
        ),
    ]
