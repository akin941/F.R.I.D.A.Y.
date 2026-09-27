"""Device-bound web sessions end to end (Phase 4 branch 2): /auth/enroll,
the device session mint, DeviceJWTAuthentication's per-request device check,
refresh on a revoked/idle device, LAN gating through the BFF headers, and
role x endpoint matrices for every device-trust surface against the five
principals (owner web session, owner CLI PAT, non-owner user, agent user,
anonymous)."""

import secrets
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.devices import (
    DEVICE_CLAIM,
    enroll_device,
    issue_pairing_code,
    issue_session_tokens,
    rotate_remote_key,
)
from accounts.models import (
    DevicePairingCode,
    PersonalAccessToken,
    RemoteAccessKey,
    TrustedDevice,
    User,
)
from accounts.services import register_user
from audit.models import AuditLog
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BFF_KEY = "test-bff-shared-secret"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def _pin(owner):
    return issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.PIN,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
    )[0]


def _strong(owner):
    return issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
    )[0]


def _device(owner, name="Mac"):
    device, secret = enroll_device(raw_code=_pin(owner), device_name=name, client_ip="10.0.0.2")
    return device, secret


def bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def via_bff(ip=None, key=BFF_KEY):
    client = APIClient()
    headers = {}
    if ip is not None:
        headers["HTTP_X_JARVIS_CLIENT_IP"] = ip
    if key is not None:
        headers["HTTP_X_JARVIS_BFF_KEY"] = key
    client.credentials(**headers)
    return client


# --- /auth/enroll ---


def test_enroll_returns_secret_and_a_device_bound_session(owner):
    response = APIClient().post(
        "/api/v1/auth/enroll",
        {"code": _pin(owner), "device_name": "MacBook", "user_agent": "Mozilla/5.0"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    device = TrustedDevice.objects.get()
    assert device.secret_hash == hash_token(body["device_secret"])
    assert device.name == "MacBook" and device.user_agent == "Mozilla/5.0"
    assert body["device"]["id"] == str(device.pk)
    assert "secret_hash" not in body["device"]
    assert RefreshToken(body["refresh"])[DEVICE_CLAIM] == str(device.pk)
    me = bearer(body["access"]).get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["is_owner"] is True


@pytest.mark.parametrize("code", ["12345678", "garbage", "jrk_unknown", "A" * 32])
def test_enroll_with_a_bad_code_is_one_generic_400(owner, code):
    response = APIClient().post(
        "/api/v1/auth/enroll", {"code": code, "device_name": "x"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_code"
    assert not TrustedDevice.objects.exists()


def test_enroll_without_an_owner_is_the_same_generic_400():
    response = APIClient().post(
        "/api/v1/auth/enroll", {"code": "12345678", "device_name": "x"}, format="json"
    )
    assert response.status_code == 400


def test_enroll_ignores_a_bearer_credential(owner):
    # Anonymous endpoint: a garbage Authorization header must not 401 it.
    response = bearer("not-a-jwt").post(
        "/api/v1/auth/enroll", {"code": _pin(owner), "device_name": "x"}, format="json"
    )
    assert response.status_code == 201


# --- LAN gating through the BFF headers ---


@pytest.fixture
def lan_gated(settings):
    settings.JARVIS_BFF_SHARED_SECRET = BFF_KEY
    settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP = False


def test_pin_from_a_validated_lan_ip_is_accepted(owner, lan_gated):
    response = via_bff("192.168.1.20").post(
        "/api/v1/auth/enroll", {"code": _pin(owner), "device_name": "x"}, format="json"
    )
    assert response.status_code == 201
    assert str(TrustedDevice.objects.get().enrolled_ip) == "192.168.1.20"


def test_pin_from_a_validated_public_ip_is_refused(owner, lan_gated):
    response = via_bff("203.0.113.7").post(
        "/api/v1/auth/enroll", {"code": _pin(owner), "device_name": "x"}, format="json"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "pin_network_refused"


@pytest.mark.parametrize("key", [None, "wrong-key"], ids=["no-key", "wrong-key"])
def test_a_spoofed_lan_ip_without_the_bff_key_is_ignored(owner, lan_gated, key):
    response = via_bff("192.168.1.20", key=key).post(
        "/api/v1/auth/enroll", {"code": _pin(owner), "device_name": "x"}, format="json"
    )
    # The header is ignored -> unknown IP -> PIN refused (LAN gating on).
    assert response.status_code == 403
    assert not TrustedDevice.objects.exists()


def test_no_shared_secret_configured_means_no_ip_is_ever_trusted(owner, settings):
    settings.JARVIS_BFF_SHARED_SECRET = ""
    settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP = False
    response = via_bff("192.168.1.20", key="").post(
        "/api/v1/auth/enroll", {"code": _pin(owner), "device_name": "x"}, format="json"
    )
    assert response.status_code == 403


def test_strong_code_is_accepted_from_anywhere(owner, lan_gated):
    for client in (via_bff("203.0.113.7"), APIClient()):
        response = client.post(
            "/api/v1/auth/enroll", {"code": _strong(owner), "device_name": "x"}, format="json"
        )
        assert response.status_code == 201


def test_remote_key_enrollment_is_surfaced_on_every_device(owner, lan_gated):
    raw_key, _ = rotate_remote_key(owner=owner, via="web")
    response = via_bff("203.0.113.7").post(
        "/api/v1/auth/enroll", {"code": raw_key, "device_name": "Otel PC"}, format="json"
    )
    assert response.status_code == 201
    other = _device(owner, "Home")[0]
    notices = bearer(issue_session_tokens(other)["access"]).get("/api/v1/auth/me").json()
    assert [n["device_name"] for n in notices["security_notices"]] == ["Otel PC"]


# --- device session mint ---


def test_device_secret_mints_a_session(owner):
    device, secret = _device(owner)
    response = APIClient().post(
        "/api/v1/auth/devices/session", {"device_secret": secret}, format="json"
    )
    assert response.status_code == 200
    assert RefreshToken(response.json()["refresh"])[DEVICE_CLAIM] == str(device.pk)


@pytest.mark.parametrize("state", ["unknown", "revoked", "idle", "not-owner"])
def test_a_rejected_device_secret_is_401(owner, settings, state):
    device, secret = _device(owner)
    if state == "unknown":
        secret = "jdv_" + secrets.token_urlsafe(32)
    elif state == "revoked":
        TrustedDevice.objects.filter(pk=device.pk).update(revoked_at=timezone.now())
    elif state == "idle":
        TrustedDevice.objects.filter(pk=device.pk).update(
            last_seen_at=timezone.now() - timedelta(days=settings.DEVICE_IDLE_EXPIRY_DAYS + 1)
        )
    else:
        User.objects.filter(pk=owner.pk).update(is_owner=False)
    response = APIClient().post(
        "/api/v1/auth/devices/session", {"device_secret": secret}, format="json"
    )
    assert response.status_code == 401
    assert response.json()["code"] == "device_rejected"


# --- DeviceJWTAuthentication ---


def test_jwt_without_a_device_claim_is_401(owner):
    legacy = str(RefreshToken.for_user(owner).access_token)
    assert bearer(legacy).get("/api/v1/auth/me").status_code == 401


def test_revoked_device_access_token_dies_on_the_next_request(owner):
    device, _ = _device(owner)
    tokens = issue_session_tokens(device)
    client = bearer(tokens["access"])
    assert client.get("/api/v1/auth/me").status_code == 200
    other = _device(owner, "Phone")[0]
    other_client = APIClient()
    other_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_session_tokens(other)['access']}")
    assert other_client.post(f"/api/v1/auth/devices/{device.pk}/revoke").status_code == 200
    # Same, still unexpired access token -- refused immediately.
    assert client.get("/api/v1/auth/me").status_code == 401
    refresh = APIClient().post(
        "/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 401


def test_idle_expired_device_can_neither_authenticate_nor_refresh(owner, settings):
    device, _ = _device(owner)
    tokens = issue_session_tokens(device)
    TrustedDevice.objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now() - timedelta(days=settings.DEVICE_IDLE_EXPIRY_DAYS + 1)
    )
    assert bearer(tokens["access"]).get("/api/v1/auth/me").status_code == 401
    refresh = APIClient().post(
        "/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 401


def test_refresh_without_a_device_claim_is_401(owner):
    legacy = str(RefreshToken.for_user(owner))
    response = APIClient().post("/api/v1/auth/refresh", {"refresh": legacy}, format="json")
    assert response.status_code == 401


def test_refresh_rotation_keeps_the_device_claim(owner):
    device, _ = _device(owner)
    tokens = issue_session_tokens(device)
    response = APIClient().post(
        "/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 200
    assert RefreshToken(response.json()["refresh"])[DEVICE_CLAIM] == str(device.pk)
    assert bearer(response.json()["access"]).get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("who", ["non-owner", "agent", "inactive"])
def test_only_the_active_human_owner_holds_a_web_session(owner, who):
    device, _ = _device(owner)
    tokens = issue_session_tokens(device)
    if who == "non-owner":
        User.objects.filter(pk=owner.pk).update(is_owner=False)
    elif who == "agent":
        User.objects.filter(pk=owner.pk).update(is_agent=True)
    else:
        User.objects.filter(pk=owner.pk).update(is_active=False)
    assert bearer(tokens["access"]).get("/api/v1/auth/me").status_code == 401


def test_a_device_of_another_user_cannot_be_named_in_a_token(owner):
    device, _ = _device(owner)
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    forged = RefreshToken.for_user(stranger)
    forged[DEVICE_CLAIM] = str(device.pk)
    assert bearer(str(forged.access_token)).get("/api/v1/auth/me").status_code == 401


def test_request_updates_last_seen_with_the_validated_ip(owner, settings):
    settings.JARVIS_BFF_SHARED_SECRET = BFF_KEY
    device, _ = _device(owner)
    TrustedDevice.objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now() - timedelta(hours=1)
    )
    client = via_bff("192.168.1.44")
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {issue_session_tokens(device)['access']}",
        HTTP_X_JARVIS_CLIENT_IP="192.168.1.44",
        HTTP_X_JARVIS_BFF_KEY=BFF_KEY,
    )
    assert client.get("/api/v1/auth/me").status_code == 200
    device.refresh_from_db()
    assert str(device.last_seen_ip) == "192.168.1.44"
    assert timezone.now() - device.last_seen_at < timedelta(minutes=1)


def test_logout_ends_the_session_but_keeps_the_device(owner):
    device, secret = _device(owner)
    tokens = issue_session_tokens(device)
    response = APIClient().post("/api/v1/auth/logout", {"refresh": tokens["refresh"]})
    assert response.status_code == 204
    device.refresh_from_db()
    assert device.revoked_at is None
    again = APIClient().post("/api/v1/auth/devices/session", {"device_secret": secret})
    assert again.status_code == 200


# --- role x endpoint: device-trust management surfaces ---


def _principal_client(kind, owner, workspace, web_client, device):
    if kind == "owner-web":
        return web_client(owner, device=device)
    if kind == "owner-pat":
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=workspace, user=owner, name="cli", token_hash=hash_token(raw)
        )
        return bearer(raw)
    if kind == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if kind == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


_EXPECTED = {
    "owner-web": None,  # per-endpoint success status
    "owner-pat": 401,  # not PAT-allowlisted: the device authenticator refuses it
    "non-owner": 403,
    "agent": 403,
    "anonymous": 401,
}

_SURFACES = [
    ("get", "/api/v1/auth/devices", None, 200),
    ("post", "/api/v1/auth/devices/{device}/revoke", None, 200),
    ("post", "/api/v1/auth/devices/current/revoke", None, 204),
    ("post", "/api/v1/auth/pairing-codes", {"kind": "pin"}, 201),
    ("get", "/api/v1/auth/remote-key", None, 200),
    ("post", "/api/v1/auth/remote-key", None, 201),
    ("post", "/api/v1/auth/remote-key/revoke", None, 200),
]


@pytest.mark.parametrize("principal", list(_EXPECTED))
@pytest.mark.parametrize(("method", "path", "body", "ok"), _SURFACES)
def test_device_trust_surfaces_role_matrix(
    owner, workspace, web_client, principal, method, path, body, ok
):
    device, _ = _device(owner)
    client = _principal_client(principal, owner, workspace, web_client, device)
    url = path.format(device=device.pk)
    response = getattr(client, method)(url, body or {}, format="json")
    expected = ok if principal == "owner-web" else _EXPECTED[principal]
    assert response.status_code == expected, response.content


def test_owner_web_session_issues_a_pin_shown_once(owner, web_client):
    device, _ = _device(owner)
    response = web_client(owner, device=device).post(
        "/api/v1/auth/pairing-codes", {}, format="json"
    )
    body = response.json()
    assert body["kind"] == "pin" and len(body["code"]) == 8
    code = DevicePairingCode.objects.get(code_hash=hash_token(body["code"]))
    assert code.issued_via == "web" and code.issued_by_device == device


def test_device_list_marks_the_current_device_and_hides_secrets(owner, web_client):
    current, _ = _device(owner, "Mac")
    _device(owner, "Phone")
    body = web_client(owner, device=current).get("/api/v1/auth/devices").json()
    assert {d["name"]: d["is_current"] for d in body} == {"Mac": True, "Phone": False}
    assert all("secret_hash" not in d for d in body)


def test_forget_this_device_revokes_the_current_one_only(owner, web_client):
    current, _ = _device(owner, "Mac")
    other, _ = _device(owner, "Phone")
    response = web_client(owner, device=current).post("/api/v1/auth/devices/current/revoke")
    assert response.status_code == 204
    current.refresh_from_db()
    other.refresh_from_db()
    assert current.revoke_reason == TrustedDevice.RevokeReason.FORGOTTEN
    assert other.revoked_at is None


def test_cannot_revoke_another_users_device(owner, web_client):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    foreign = TrustedDevice.objects.create(
        user=stranger, name="x", secret_hash="0" * 64, enrolled_via="pin"
    )
    response = web_client(owner).post(f"/api/v1/auth/devices/{foreign.pk}/revoke")
    assert response.status_code == 404


def test_remote_key_rotation_via_web_returns_the_key_once(owner, web_client):
    device, _ = _device(owner)
    client = web_client(owner, device=device)
    first = client.post("/api/v1/auth/remote-key").json()
    assert first["key"].startswith("jrk_") and first["active"] is True
    state = client.get("/api/v1/auth/remote-key").json()
    assert state["active"] is True and "key" not in state
    client.post("/api/v1/auth/remote-key")
    assert RemoteAccessKey.objects.filter(revoked_at__isnull=True).count() == 1
    client.post("/api/v1/auth/remote-key/revoke")
    assert client.get("/api/v1/auth/remote-key").json()["active"] is False
    assert AuditLog.objects.filter(action="accounts.remote_key.rotated").exists()


def test_signup_and_password_login_are_gone():
    for path in ("/api/v1/auth/signup", "/api/v1/auth/login"):
        response = APIClient().post(path, {"email": "a@b.c", "password": PASSWORD})
        assert response.status_code == 404
