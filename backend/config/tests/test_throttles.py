"""Phase 4 throttle scopes, one 429 per scope: `refresh` (anonymous
session endpoints -- effectively global, Django only sees the BFF's
address), the two-tier `/auth/enroll` throttle (branch 2: per validated
client IP + global ceiling) and `cost` (cost-bearing creates; listing
stays free). Rates are patched to tiny values; a rate of 0 throttles the very
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


def test_configured_scopes_exist(settings):
    configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert {"chat", "refresh", "cost"} <= set(configured)
    assert "login" not in configured  # no password login since Phase 4 branch 2


BFF_KEY = "test-bff-shared-secret"
ENROLL = "/api/v1/auth/enroll"
BAD_CODE = {"code": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "device_name": "x"}


def _from(ip):
    client = APIClient()
    client.credentials(HTTP_X_FRIDAY_CLIENT_IP=ip, HTTP_X_FRIDAY_BFF_KEY=BFF_KEY)
    return client


def test_enroll_per_source_limit_does_not_block_other_sources(owner, settings):
    settings.FRIDAY_BFF_SHARED_SECRET = BFF_KEY
    settings.ENROLL_RATE_PER_SOURCE = "2/10m"
    settings.ENROLL_RATE_GLOBAL = "100/h"
    attacker = _from("203.0.113.7")
    assert attacker.post(ENROLL, BAD_CODE, format="json").status_code == 400
    assert attacker.post(ENROLL, BAD_CODE, format="json").status_code == 400
    assert attacker.post(ENROLL, BAD_CODE, format="json").status_code == 429
    # The owner, from another source, still gets through.
    assert _from("192.168.1.20").post(ENROLL, BAD_CODE, format="json").status_code == 400


def test_enroll_global_ceiling_applies_without_a_validated_ip(owner, settings):
    settings.FRIDAY_BFF_SHARED_SECRET = BFF_KEY
    settings.ENROLL_RATE_PER_SOURCE = "1/10m"
    settings.ENROLL_RATE_GLOBAL = "2/h"
    anonymous = APIClient()  # no validated client IP: only the global tier
    assert anonymous.post(ENROLL, BAD_CODE, format="json").status_code == 400
    assert anonymous.post(ENROLL, BAD_CODE, format="json").status_code == 400
    assert anonymous.post(ENROLL, BAD_CODE, format="json").status_code == 429
    assert _from("192.168.1.20").post(ENROLL, BAD_CODE, format="json").status_code == 429


def test_a_spoofed_client_ip_without_the_key_gets_no_own_bucket(owner, settings):
    settings.FRIDAY_BFF_SHARED_SECRET = BFF_KEY
    settings.ENROLL_RATE_PER_SOURCE = "1/10m"
    settings.ENROLL_RATE_GLOBAL = "100/h"
    spoofed = APIClient()
    spoofed.credentials(HTTP_X_FRIDAY_CLIENT_IP="203.0.113.7")  # no key: ignored
    for _ in range(3):
        assert spoofed.post(ENROLL, BAD_CODE, format="json").status_code == 400


@pytest.mark.parametrize(
    "rate, expected",
    [("5/10m", (5, 600)), ("60/h", (60, 3600)), ("3/30s", (3, 30)), ("1/d", (1, 86400))],
)
def test_parse_rate_accepts_multi_unit_periods(rate, expected):
    from core.throttling import parse_rate

    assert parse_rate(rate) == expected


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
def test_cost_scope_throttles_cost_bearing_creates(owner, workspace, monkeypatch, path, web_client):
    rates(monkeypatch, cost="0/min")
    response = web_client(owner).post(path, {"workspace": str(workspace.id)}, format="json")
    assert response.status_code == 429


def test_cost_scope_throttles_file_upload_create(owner, workspace, monkeypatch, web_client):
    rates(monkeypatch, cost="0/min")
    response = web_client(owner).post(
        "/api/v1/knowledge/documents/file-upload", {"workspace": str(workspace.id)}
    )
    assert response.status_code == 429


@pytest.mark.parametrize(
    "path",
    ["/api/v1/agents", "/api/v1/harness/jobs", "/api/v1/harness/implementation-jobs"],
)
def test_cost_scope_never_throttles_listing(owner, workspace, monkeypatch, path, web_client):
    rates(monkeypatch, cost="0/min")
    response = web_client(owner).get(path, {"workspace": str(workspace.id)})
    assert response.status_code != 429


def test_cost_scope_counts_per_user(owner, workspace, monkeypatch, web_client):
    rates(monkeypatch, cost="1/min")
    other = register_user(email="other@test.dev", password=PASSWORD, name="Other")
    other_ws = Workspace.objects.get(owner=other)
    body = {"workspace": str(workspace.id), "url": "not a url"}
    assert web_client(owner).post("/api/v1/knowledge/documents/url", body).status_code == 400
    assert web_client(owner).post("/api/v1/knowledge/documents/url", body).status_code == 429
    other_body = {"workspace": str(other_ws.id), "url": "not a url"}
    assert web_client(other).post("/api/v1/knowledge/documents/url", other_body).status_code == 400
