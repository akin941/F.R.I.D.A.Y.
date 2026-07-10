import pytest
from django.contrib.admin.sites import site as admin_site
from django.db import DatabaseError, transaction
from django.test import Client
from rest_framework.test import APIClient

from accounts.services import register_user
from audit.models import AuditLog
from audit.services import record_audit
from core.context import get_request_id
from workspaces.services import ensure_personal_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def user():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


# --- record_audit basics ---


def test_record_audit_creates_entry(user):
    entry = record_audit(action="test.event", actor=user, metadata={"k": "v"})
    assert AuditLog.objects.filter(pk=entry.pk).exists()
    assert entry.action == "test.event"
    assert entry.actor == user
    assert entry.actor_type == AuditLog.ActorType.HUMAN
    assert entry.metadata == {"k": "v"}


def test_record_audit_extracts_resource(user):
    entry = record_audit(action="test.resource", resource=user)
    assert entry.resource_type == "accounts.user"
    assert entry.resource_id == str(user.pk)


def test_record_audit_rejects_secret_metadata(user):
    with pytest.raises(ValueError):
        record_audit(action="test.bad", metadata={"password": "x"})
    with pytest.raises(ValueError):
        record_audit(action="test.bad", metadata={"TOKEN": "x"})


# --- request id correlation ---


def test_request_id_recorded_during_request(settings):
    settings.SIGNUP_ENABLED = True
    APIClient().post(
        "/api/v1/auth/signup",
        {"email": "rid@test.dev", "password": PASSWORD, "name": "R"},
        headers={"X-Request-ID": "rid-fixed-123"},
    )
    entry = AuditLog.objects.get(action="user.signup")
    assert entry.request_id == "rid-fixed-123"


def test_request_id_does_not_leak_between_requests():
    Client().get("/healthz", headers={"X-Request-ID": "leak-check"})
    # After the request finished, the contextvar must be reset.
    assert get_request_id() is None
    entry = record_audit(action="test.after_request")
    assert entry.request_id == ""


# --- immutability (DB trigger) ---


def test_auditlog_update_is_rejected(user):
    record_audit(action="test.immutable", actor=user)
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            AuditLog.objects.all().update(action="tampered")


def test_auditlog_delete_is_rejected(user):
    record_audit(action="test.immutable", actor=user)
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            AuditLog.objects.all().delete()


# --- wired events ---


def test_signup_records_audit_events(settings):
    settings.SIGNUP_ENABLED = True
    APIClient().post(
        "/api/v1/auth/signup",
        {"email": "new@test.dev", "password": PASSWORD, "name": "New"},
    )
    signup_entry = AuditLog.objects.get(action="user.signup")
    ws_entry = AuditLog.objects.get(action="workspace.created")
    assert signup_entry.actor.email == "new@test.dev"
    assert signup_entry.workspace_id == ws_entry.workspace_id
    assert ws_entry.resource_type == "workspaces.workspace"


def test_ensure_personal_workspace_idempotent_audit(user):
    assert AuditLog.objects.filter(action="workspace.created").count() == 1
    ensure_personal_workspace(user)  # second call — no new workspace
    assert AuditLog.objects.filter(action="workspace.created").count() == 1


def test_login_failed_records_audit(user):
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "alice@test.dev", "password": "wrong"}
    )
    assert response.status_code == 401
    entry = AuditLog.objects.get(action="auth.login_failed")
    assert entry.actor is None
    assert entry.metadata["email"] == "alice@test.dev"


def test_login_failed_metadata_has_no_password(user):
    APIClient().post("/api/v1/auth/login", {"email": "alice@test.dev", "password": "wrong"})
    entry = AuditLog.objects.get(action="auth.login_failed")
    flat = str(entry.metadata).lower()
    assert "wrong" not in flat
    assert "password" not in entry.metadata


def test_successful_login_not_broken(user):
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "alice@test.dev", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert "access" in response.data


# --- admin is read-only ---


def test_admin_is_readonly(rf, user):
    model_admin = admin_site._registry[AuditLog]
    request = rf.get("/admin/audit/auditlog/")
    request.user = user
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
