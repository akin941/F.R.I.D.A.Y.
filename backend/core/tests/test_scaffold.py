"""Core infrastructure smoke tests — no database required."""

from django.test import Client

from core.exceptions import JarvisError, WorkspaceScopingError


def test_healthz_returns_ok_with_request_id():
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_incoming_request_id_is_preserved():
    response = Client().get("/healthz", headers={"X-Request-ID": "test-id-123"})
    assert response.headers["X-Request-ID"] == "test-id-123"


def test_oversized_request_id_is_replaced():
    # AuditLog.request_id / ModelUsage.request_id are CharField(max_length=64);
    # an oversized client-supplied header must never reach those inserts.
    oversized = "x" * 65
    response = Client().get("/healthz", headers={"X-Request-ID": oversized})
    assert response.headers["X-Request-ID"] != oversized
    assert len(response.headers["X-Request-ID"]) <= 64


# for_actor behaviour (incl. anonymous rejection) is covered with real models in
# workspaces/tests/test_scoping.py — core tests must not import kernel apps.


def test_exception_hierarchy():
    assert issubclass(WorkspaceScopingError, JarvisError)
