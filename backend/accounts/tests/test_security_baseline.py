"""Phase 4 branch 1 -- owner account and token hardening: refresh-token
blacklist + logout, PAT expiry and failure audit, `create_dev_pat` audit,
the password validators, and the token-flush beat task. Sessions come from
a trusted device (Phase 4 branch 2)."""

import io
import uuid
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import authentication
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.devices import enroll_device, issue_pairing_code, issue_session_tokens
from accounts.models import DevicePairingCode, PersonalAccessToken, User
from accounts.services import register_user
from accounts.tasks import flush_expired_tokens
from audit.models import AuditLog
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def bearer_client(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def login(user):
    """A trusted device's session for the owner (there is no password login
    since Phase 4 branch 2)."""
    raw, _ = issue_pairing_code(
        owner=user,
        kind=DevicePairingCode.Kind.PIN,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
    )
    device, _ = enroll_device(raw_code=raw, device_name="Mac", client_ip="10.0.0.2")
    return issue_session_tokens(device)


def mint(web_client, user, workspace, **extra):
    response = web_client(user).post(
        "/api/v1/auth/tokens",
        {"workspace": str(workspace.id), "name": "cli", **extra},
        format="json",
    )
    return response


# --- refresh blacklist + logout ---


def test_rotated_refresh_token_is_blacklisted(owner):
    tokens = login(owner)
    first = APIClient().post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert first.status_code == 200
    assert first.data["refresh"] != tokens["refresh"]

    replay = APIClient().post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert replay.status_code == 401


def test_logout_blacklists_the_refresh_token(owner):
    tokens = login(owner)
    response = APIClient().post(
        "/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 204
    assert BlacklistedToken.objects.filter(token__token=tokens["refresh"]).exists()

    refresh = APIClient().post(
        "/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 401

    log = AuditLog.objects.get(action="auth.logout")
    assert log.actor_id == owner.id
    assert "refresh" not in log.metadata


def test_logout_with_the_same_users_access_token_is_accepted(owner):
    tokens = login(owner)
    response = bearer_client(tokens["access"]).post(
        "/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 204


def test_logout_refuses_another_users_refresh_token(owner):
    other = register_user(email="other@test.dev", password=PASSWORD, name="Other")
    other_refresh = str(RefreshToken.for_user(other))
    response = bearer_client(login(owner)["access"]).post(
        "/api/v1/auth/logout", {"refresh": other_refresh}, format="json"
    )
    assert response.status_code == 403
    assert not BlacklistedToken.objects.exists()


@pytest.mark.parametrize("refresh", ["not-a-jwt", ""])
def test_logout_with_an_invalid_refresh_token(owner, refresh):
    response = APIClient().post("/api/v1/auth/logout", {"refresh": refresh}, format="json")
    assert response.status_code in (400, 401)
    assert not BlacklistedToken.objects.exists()


def test_logout_twice_is_refused_the_second_time(owner):
    tokens = login(owner)
    client = APIClient()
    assert client.post("/api/v1/auth/logout", tokens, format="json").status_code == 204
    assert client.post("/api/v1/auth/logout", tokens, format="json").status_code == 401


def test_logout_is_a_web_channel_endpoint_pat_is_refused(owner, workspace, web_client):
    raw = mint(web_client, owner, workspace).data["token"]
    tokens = login(owner)
    response = bearer_client(raw).post(
        "/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 401
    assert not BlacklistedToken.objects.exists()


def test_flush_expired_tokens_task_removes_expired_outstanding_tokens(owner):
    tokens = login(owner)
    OutstandingToken.objects.update(expires_at=timezone.now() - timedelta(days=1))
    assert OutstandingToken.objects.exists()
    flush_expired_tokens()
    assert not OutstandingToken.objects.filter(token=tokens["refresh"]).exists()


# --- password validators ---


@pytest.mark.parametrize(
    "password",
    ["short1!", "password1234", "123456789012", "owner@test.dev1"],
    ids=["too-short", "common", "numeric", "similar-to-email"],
)
def test_bootstrap_owner_rejects_a_weak_password(monkeypatch, password):
    monkeypatch.setenv("JARVIS_OWNER_PASSWORD", password)
    with pytest.raises(CommandError, match="Password rejected"):
        call_command("bootstrap_owner", "--email", "owner@test.dev", "--name", "Owner")
    assert not User.objects.exists()


# --- PAT expiry ---


def test_mint_defaults_to_the_configured_ttl(owner, workspace, settings, web_client):
    settings.PAT_DEFAULT_TTL_DAYS = 90
    before = timezone.now()
    response = mint(web_client, owner, workspace)
    assert response.status_code == 201
    token = PersonalAccessToken.objects.get(id=response.data["id"])
    assert before + timedelta(days=90) <= token.expires_at <= timezone.now() + timedelta(days=90)
    assert response.data["expires_at"]


def test_mint_accepts_a_chosen_ttl(owner, workspace, web_client):
    response = mint(web_client, owner, workspace, expires_in_days=7)
    assert response.status_code == 201
    token = PersonalAccessToken.objects.get(id=response.data["id"])
    assert token.expires_at - timezone.now() < timedelta(days=7, minutes=1)
    assert token.expires_at - timezone.now() > timedelta(days=6)


@pytest.mark.parametrize("days", [0, 366])
def test_mint_rejects_a_ttl_outside_the_allowed_range(owner, workspace, settings, days, web_client):
    settings.PAT_MAX_TTL_DAYS = 365
    response = mint(web_client, owner, workspace, expires_in_days=days)
    assert response.status_code == 400
    assert "expires_in_days" in response.data
    assert not PersonalAccessToken.objects.exists()


def test_mint_audit_row_records_the_web_path(owner, workspace, web_client):
    mint(web_client, owner, workspace)
    log = AuditLog.objects.get(action="accounts.pat.created")
    assert log.metadata["via"] == "web"
    assert "expires_at" in log.metadata


def _expired_token(owner, workspace, web_client):
    raw = mint(web_client, owner, workspace).data["token"]
    PersonalAccessToken.objects.filter(token_hash=hash_token(raw)).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    return raw


_ALLOWLISTED = [
    ("get", "/api/v1/ai/conversations?workspace={ws}"),
    ("post", "/api/v1/ai/conversations"),
    ("get", "/api/v1/ai/conversations/{id}"),
    ("patch", "/api/v1/ai/conversations/{id}"),
    ("get", "/api/v1/ai/conversations/{id}/messages"),
    ("post", "/api/v1/ai/conversations/{id}/messages"),
    ("get", "/api/v1/agents?workspace={ws}"),
    ("post", "/api/v1/agents"),
    ("get", "/api/v1/agents/{id}"),
    ("post", "/api/v1/agents/{id}/cancel"),
    ("get", "/api/v1/approvals/requests"),
    ("post", "/api/v1/approvals/requests"),
    ("post", "/api/v1/approvals/requests/{id}/approve"),
]


@pytest.mark.parametrize(("method", "path"), _ALLOWLISTED)
def test_expired_pat_is_rejected_on_every_allowlisted_view(
    owner, workspace, method, path, web_client
):
    raw = _expired_token(owner, workspace, web_client)
    url = path.format(ws=workspace.id, id=uuid.uuid4())
    response = getattr(bearer_client(raw), method)(url, {}, format="json")
    assert response.status_code == 401


def test_unexpired_pat_still_authenticates(owner, workspace, web_client):
    raw = mint(web_client, owner, workspace).data["token"]
    response = bearer_client(raw).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 200


@pytest.mark.parametrize("reason", ["unknown", "revoked", "expired", "inactive_user"])
def test_pat_auth_failures_are_audited_with_prefix_only(owner, workspace, reason, web_client):
    raw = mint(web_client, owner, workspace).data["token"]
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw))
    if reason == "unknown":
        raw = TOKEN_PREFIX + "Z" * 43
    elif reason == "revoked":
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
    elif reason == "expired":
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=["expires_at"])
    else:
        owner.is_active = False
        owner.save(update_fields=["is_active"])

    response = bearer_client(raw).get(f"/api/v1/ai/conversations?workspace={workspace.id}")
    assert response.status_code == 401

    log = AuditLog.objects.get(action="accounts.pat.auth_failed")
    assert log.metadata["reason"] == reason
    assert log.metadata["token_prefix"] == raw[: len(TOKEN_PREFIX) + 4]
    assert raw not in str(log.metadata)
    assert log.actor_type == AuditLog.ActorType.SYSTEM


def test_pat_auth_failure_audit_is_rate_limited(owner, workspace, monkeypatch):
    monkeypatch.setattr(authentication, "AUTH_FAILURE_AUDIT_LIMIT", 3)
    for i in range(6):
        response = bearer_client(f"{TOKEN_PREFIX}garbage{i}").get(
            f"/api/v1/ai/conversations?workspace={workspace.id}"
        )
        assert response.status_code == 401
    assert AuditLog.objects.filter(action="accounts.pat.auth_failed").count() == 3


# --- create_dev_pat audit ---


def test_create_dev_pat_writes_an_audit_row(owner, workspace, settings):
    settings.DEBUG = True
    out = io.StringIO()
    call_command("create_dev_pat", owner.email, workspace.name, stdout=out)
    raw = out.getvalue().strip()
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw))

    log = AuditLog.objects.get(action="accounts.pat.created")
    assert log.metadata["via"] == "management_command"
    assert log.actor_id == owner.id
    assert log.resource_id == str(token.id)
    assert token.expires_at > timezone.now() + timedelta(days=89)
