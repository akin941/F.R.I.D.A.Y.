"""A fake Home Assistant REST API behind `httpx.MockTransport` (brief test
strategy: no real HA). The production bridge runs unchanged -- only its
`_http_transport` seam returns this transport. Every request is recorded
(method, path, JSON body, headers) so tests assert "exactly once" and
"never"."""

import copy
import json
from dataclasses import dataclass, field

import httpx
import pytest

from connectors.bridges import home_assistant as bridge

BASE_URL = "http://ha.test:8123"
# A token value that must never appear anywhere Friday writes.
TOKEN_CANARY = "HA-TOKEN-CANARY-eyJhbGciOiJIUzI1NiJ9"


def _state(entity_id, state, **attributes):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes,
        "last_changed": "2026-09-28T00:00:00+00:00",
        "context": {"id": "ctx", "user_id": "u"},
    }


def default_states():
    return {
        "light.salon": _state(
            "light.salon",
            "off",
            friendly_name="Salon",
            entity_picture="/api/camera_proxy/x?token=PICTURE-TOKEN",
            supported_color_modes=["color_temp"],
        ),
        "climate.salon": _state(
            "climate.salon", "heat", friendly_name="Salon klima", temperature=21.0
        ),
        "sensor.salon_temp": _state(
            "sensor.salon_temp", "22.4", friendly_name="Salon sıcaklık", unit_of_measurement="°C"
        ),
        "lock.front_door": _state("lock.front_door", "locked", friendly_name="Kapı"),
        "script.everything": _state("script.everything", "off", friendly_name="Script"),
        "media_player.tv": _state("media_player.tv", "paused", friendly_name="TV"),
    }


@dataclass
class FakeHomeAssistant:
    states: dict = field(default_factory=default_states)
    requests: list = field(default_factory=list)
    service_status: int = 200
    # Raised (an httpx exception) on the service POST, after recording it.
    post_error: object = None
    version: str = "2026.9.1"

    @property
    def posts(self):
        return [(path, body) for method, path, body, _ in self.requests if method == "POST"]

    def handler(self, request):
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body, dict(request.headers)))
        if request.method == "GET" and path == "/api/config":
            return httpx.Response(200, json={"version": self.version})
        if request.method == "GET" and path == "/api/states":
            return httpx.Response(200, json=list(copy.deepcopy(self.states).values()))
        if request.method == "GET" and path.startswith("/api/states/"):
            entity_id = path.removeprefix("/api/states/")
            if entity_id not in self.states:
                return httpx.Response(404, json={"message": "Entity not found."})
            return httpx.Response(200, json=copy.deepcopy(self.states[entity_id]))
        if request.method == "POST" and path.startswith("/api/services/"):
            if self.post_error is not None:
                raise self.post_error
            if self.service_status >= 300:
                return httpx.Response(self.service_status, json={"message": "no"})
            domain, service = path.removeprefix("/api/services/").split("/")
            entity_id = (body or {}).get("entity_id")
            if entity_id in self.states:
                target = self.states[entity_id]
                attributes = target["attributes"]
                if service == "select_source":
                    attributes["source"] = body["source"]
                elif service == "volume_mute":
                    attributes["is_volume_muted"] = body["is_volume_muted"]
                elif service == "volume_set":
                    attributes["volume_level"] = body["volume_level"]
                elif service in ("media_play", "media_pause"):
                    target["state"] = "playing" if service == "media_play" else "paused"
                elif service == "turn_on":
                    target["state"] = "on"
                    if "brightness_pct" in body:
                        target["attributes"]["brightness"] = round(body["brightness_pct"] * 2.55)
                elif service == "turn_off":
                    target["state"] = "off"
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not found"})


def salon_tv_state(**overrides):
    """The brief's TV (`media_player.salon_tv`): off, three inputs."""
    attributes = {
        "friendly_name": "Samsung QE55",
        "source": "TV",
        "source_list": ["TV", "HDMI 1", "HDMI 2"],
        "volume_level": 0.2,
        "is_volume_muted": False,
        "media_title": "SECRET-TITLE",
        "entity_picture": "/api/media_player_proxy/x?token=PICTURE-TOKEN",
        **overrides,
    }
    return _state("media_player.salon_tv", "off", **attributes)


def film_night_state():
    return _state("scene.film_gecesi", "unknown", friendly_name="Film gecesi")


def allow(workspace, entity_id, services=None, *, label=None, room=None, aliases=(), enabled=True):
    """Allowlist one entity the way the owner's editor would (the row
    directly; `connectors.home_entities` is tested on its own). `services`
    defaults to the domain's whole code maximum."""
    from connectors.models import HomeEntity
    from core.speech import phrase_key

    label = label or entity_id.split(".", 1)[1]
    return HomeEntity.objects.create(
        workspace=workspace,
        entity_id=entity_id,
        label=label,
        key=phrase_key(label),
        room=room,
        aliases=list(aliases),
        services=list(services) if services is not None else sorted(
            bridge.SERVICES[entity_id.split(".", 1)[0]]
        ),
        enabled=enabled,
    )


@pytest.fixture
def ha(monkeypatch):
    fake = FakeHomeAssistant()
    monkeypatch.setenv(bridge.BASE_URL_ENV, BASE_URL)
    monkeypatch.setenv(bridge.TOKEN_ENV, TOKEN_CANARY)
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(bridge, "_http_transport", lambda: transport)
    return fake
