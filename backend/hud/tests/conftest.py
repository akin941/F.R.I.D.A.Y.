import json
import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from hud import views
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
EVENTS = "/api/v1/hud/events"


@pytest.fixture(autouse=True)
def hud_settings(settings):
    settings.HUD_STREAM_INTERVAL_SECONDS = 5
    settings.HUD_STREAM_MAX_SECONDS = 15
    settings.HUD_STREAM_RETRY_MS = 3000
    settings.HUD_LIST_LIMIT = 8
    return settings


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    """No real waiting: `sleep` advances a fake monotonic clock and runs
    the test's per-tick hooks (to change rows between two ticks)."""

    class Clock:
        now = 0.0
        ticks = []

        def sleep(self, seconds):
            self.now += seconds
            if self.ticks:
                self.ticks.pop(0)()

    clock = Clock()
    clock.ticks = []
    monkeypatch.setattr(views, "sleep", clock.sleep)
    monkeypatch.setattr(views, "monotonic", lambda: clock.now)
    return clock


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


@pytest.fixture
def principal_client(owner, workspace, web_client):
    """The five Phase 4 principals (plus anonymous)."""

    def make(principal):
        if principal == "owner-web":
            return web_client(owner)
        if principal == "owner-pat-cli":
            return pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
        if principal == "owner-pat-openai":
            return pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
        if principal == "non-owner":
            return web_client(register_user(email="member@test.dev", password=PASSWORD))
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        return APIClient()

    return make


def read_stream(response):
    """The stream as a list of frames: dicts for `data:` frames, the raw
    text for `retry:` hints and comments."""
    body = b"".join(response.streaming_content).decode()
    frames = []
    for raw in body.split("\n\n"):
        if not raw:
            continue
        if raw.startswith("data: "):
            frames.append(json.loads(raw[len("data: ") :]))
        else:
            frames.append(raw)
    return frames


@pytest.fixture
def stream(web_client, owner):
    def open_stream(workspace_id, *, client=None, **params):
        query = {"workspace": str(workspace_id), **params}
        response = (client or web_client(owner)).get(EVENTS, query)
        assert response.status_code == 200, getattr(response, "data", None)
        return read_stream(response)

    return open_stream
