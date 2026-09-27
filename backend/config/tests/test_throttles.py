"""Phase 4 branch 1 throttle scopes, one 429 per scope: `login` and
`refresh` (anonymous token endpoints -- effectively global, Django only
sees the BFF's address) and `cost` (cost-bearing creates; listing stays
free). Rates are patched to tiny values; a rate of 0 throttles the very
first request, before any view (or LLM/job) code runs."""

import pytest
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.services import register_user
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def rates(monkeypatch, **overrides):
    # THROTTLE_RATES is a class attribute snapshotted from settings at
    # import time (see assistant/tests/test_views.py).
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {**ScopedRateThrottle.THROTTLE_RATES, **overrides}
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_configured_scopes_exist(settings):
    configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert {"chat", "login", "refresh", "cost"} <= set(configured)


def test_login_is_throttled(owner, monkeypatch):
    rates(monkeypatch, login="2/min")
    client = APIClient()
    body = {"email": owner.email, "password": "wrong-password"}
    assert client.post("/api/v1/auth/login", body, format="json").status_code == 401
    assert client.post("/api/v1/auth/login", body, format="json").status_code == 401
    # The right password does not bypass the throttle either.
    good = {"email": owner.email, "password": PASSWORD}
    assert client.post("/api/v1/auth/login", good, format="json").status_code == 429


def test_refresh_is_throttled(monkeypatch):
    rates(monkeypatch, refresh="1/min")
    client = APIClient()
    assert client.post("/api/v1/auth/refresh", {"refresh": "x"}, format="json").status_code == 401
    assert client.post("/api/v1/auth/refresh", {"refresh": "x"}, format="json").status_code == 429


def test_logout_shares_the_refresh_scope(monkeypatch):
    rates(monkeypatch, refresh="0/min")
    response = APIClient().post("/api/v1/auth/logout", {"refresh": "x"}, format="json")
    assert response.status_code == 429


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents",
        "/api/v1/harness/jobs",
        "/api/v1/harness/implementation-jobs",
        "/api/v1/knowledge/documents/pasted-text",
        "/api/v1/knowledge/documents/url",
    ],
)
def test_cost_scope_throttles_cost_bearing_creates(owner, workspace, monkeypatch, path):
    rates(monkeypatch, cost="0/min")
    response = client_for(owner).post(path, {"workspace": str(workspace.id)}, format="json")
    assert response.status_code == 429


def test_cost_scope_throttles_file_upload_create(owner, workspace, monkeypatch):
    rates(monkeypatch, cost="0/min")
    response = client_for(owner).post(
        "/api/v1/knowledge/documents/file-upload", {"workspace": str(workspace.id)}
    )
    assert response.status_code == 429


@pytest.mark.parametrize(
    "path",
    ["/api/v1/agents", "/api/v1/harness/jobs", "/api/v1/harness/implementation-jobs"],
)
def test_cost_scope_never_throttles_listing(owner, workspace, monkeypatch, path):
    rates(monkeypatch, cost="0/min")
    response = client_for(owner).get(path, {"workspace": str(workspace.id)})
    assert response.status_code != 429


def test_cost_scope_counts_per_user(owner, workspace, monkeypatch):
    rates(monkeypatch, cost="1/min")
    other = register_user(email="other@test.dev", password=PASSWORD, name="Other")
    other_ws = Workspace.objects.get(owner=other)
    body = {"workspace": str(workspace.id), "url": "not a url"}
    assert client_for(owner).post("/api/v1/knowledge/documents/url", body).status_code == 400
    assert client_for(owner).post("/api/v1/knowledge/documents/url", body).status_code == 429
    other_body = {"workspace": str(other_ws.id), "url": "not a url"}
    assert client_for(other).post("/api/v1/knowledge/documents/url", other_body).status_code == 400
