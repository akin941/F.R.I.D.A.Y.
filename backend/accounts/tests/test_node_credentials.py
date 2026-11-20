"""Phase 9 spike S9.0 (server half): `kind=node` device trust.

Covers the six spike pass criteria of the Phase 9 brief D1:

1. pair with both code kinds, and the cross-kind refusals hold;
2. rotate without re-pairing, including grace expiry and first-use cut-over;
3. revoke one node, or all devices, and the next authentication fails;
4. kill switch on, then off;
5. a node secret is refused by every other surface
   (config/tests/test_node_credential_surfaces.py);
6. a browser device or PAT is refused by the node surfaces (the role x
   endpoint matrix below).

No real Redis is touched for the fan-out assertions: `core.revocation._client`
is replaced by a recorder.
"""

import io
import json
import secrets
import threading
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import (
    DevicePairingCode,
    NodeKillSwitch,
    PersonalAccessToken,
    TrustedDevice,
    User,
)
from accounts.services import register_user
from audit.models import AuditLog
from core import revocation
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
LAN_IP = "10.0.0.7"
ROTATE_URL = "/api/v1/auth/nodes/credential/rotate"
KILL_URL = "/api/v1/auth/nodes/kill-switch"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


class FakeRedis:
    """Records `publish` calls; optionally fails like an unreachable Redis."""

    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    def publish(self, channel, payload):
        if self.fail:
            raise ConnectionError("redis down")
        self.published.append((channel, json.loads(payload)))

    def close(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(revocation, "_client", lambda: fake)
    return fake


def node_code(owner, kind=DevicePairingCode.Kind.PIN):
    return devices.issue_pairing_code(
        owner=owner,
        kind=kind,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )[0]


def browser_code(owner, kind=DevicePairingCode.Kind.PIN):
    return devices.issue_pairing_code(
        owner=owner, kind=kind, issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND
    )[0]


def pair_node(owner, name="MacBook"):
    return devices.enroll_node_device(raw_code=node_code(owner), node_name=name, client_ip=LAN_IP)


def bearer(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def audits(action):
    return AuditLog.objects.filter(action=action)


# --- 1. pairing, both code kinds, cross-kind refusals ---


@pytest.mark.parametrize("kind", [DevicePairingCode.Kind.PIN, DevicePairingCode.Kind.STRONG])
def test_a_node_code_pairs_a_node(owner, kind):
    device, secret = devices.enroll_node_device(
        raw_code=node_code(owner, kind), node_name="MacBook", client_ip=LAN_IP
    )
    assert secret.startswith("jnd_") and len(secret) > 40
    assert device.kind == TrustedDevice.Kind.NODE and device.is_node
    assert device.secret_hash == hash_token(secret)
    assert device.secret_issued_at is not None
    assert device.user == owner
    row = audits("accounts.node.enrolled").get()
    assert row.metadata["kind"] == "node"
    code = DevicePairingCode.objects.get()
    assert code.device_kind == "node" and code.consumed_by_device == device


def test_a_browser_code_is_refused_for_a_node_and_is_not_consumed(owner):
    raw = browser_code(owner, DevicePairingCode.Kind.STRONG)
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
    assert not TrustedDevice.objects.exists()
    assert DevicePairingCode.objects.get().consumed_at is None
    assert audits("accounts.device.enroll_failed").get().metadata["reason"] == ("wrong_device_kind")
    # ... and it still enrolls the browser it was issued for.
    device, secret = devices.enroll_device(raw_code=raw, device_name="Safari")
    assert device.kind == TrustedDevice.Kind.BROWSER and secret.startswith("jdv_")


def test_a_node_code_is_refused_for_a_browser(owner):
    raw = node_code(owner, DevicePairingCode.Kind.STRONG)
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=raw, device_name="Safari")
    assert not TrustedDevice.objects.exists()
    assert DevicePairingCode.objects.get().consumed_at is None
    failure = audits("accounts.device.enroll_failed").get()
    assert failure.metadata["reason"] == "wrong_device_kind"
    assert failure.metadata["device_kind"] == "browser"


def test_the_node_enroll_endpoint_does_not_exist_on_the_browser_path(owner):
    raw = node_code(owner, DevicePairingCode.Kind.STRONG)
    response = APIClient().post(
        "/api/v1/auth/enroll", {"code": raw, "device_name": "x"}, format="json"
    )
    assert response.status_code == 400
    assert not TrustedDevice.objects.exists()


def test_the_remote_key_never_pairs_a_node(owner):
    raw_key, _ = devices.rotate_remote_key(owner=owner, via="management_command")
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_node_device(raw_code=raw_key, node_name="Mac", client_ip=LAN_IP)
    assert not TrustedDevice.objects.exists()
    assert audits("accounts.device.enroll_failed").get().metadata["reason"] == (
        "remote_key_for_node"
    )


def test_node_pin_lan_gating_is_unchanged(owner, settings):
    settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP = False
    raw = node_code(owner)
    with pytest.raises(devices.PinNetworkRefused):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip="203.0.113.9")
    with pytest.raises(devices.PinNetworkRefused):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=None)
    # Refused before any lookup: nothing was burned.
    assert DevicePairingCode.objects.get().attempts == 0
    # A strong node code works from anywhere.
    device, _ = devices.enroll_node_device(
        raw_code=node_code(owner, DevicePairingCode.Kind.STRONG),
        node_name="Mac",
        client_ip="203.0.113.9",
    )
    assert device.is_node


def test_node_pin_attempts_burn_as_before(owner):
    raw = node_code(owner)
    for _ in range(DevicePairingCode.MAX_ATTEMPTS):
        with pytest.raises(devices.InvalidPairingCode):
            devices.enroll_node_device(raw_code="00000000", node_name="Mac", client_ip=LAN_IP)
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
    assert not TrustedDevice.objects.exists()


def test_a_node_code_expires(owner):
    raw = node_code(owner)
    DevicePairingCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)


@pytest.mark.django_db(transaction=True)
def test_a_node_code_is_single_use_under_concurrency(owner):
    raw = node_code(owner, DevicePairingCode.Kind.STRONG)
    barrier = threading.Barrier(2)
    results = []

    def attempt():
        try:
            barrier.wait()
            devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
            results.append("ok")
        except devices.InvalidPairingCode:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused"]
    assert TrustedDevice.objects.filter(kind=TrustedDevice.Kind.NODE).count() == 1


def test_only_the_owner_can_issue_a_node_code(owner):
    other = register_user(email="member@test.dev", password=PASSWORD)
    with pytest.raises(devices.NotOwner):
        node_code(other)


def test_issue_pairing_code_command_node_flag(owner):
    out = io.StringIO()
    call_command("issue_pairing_code", "--node", "--strong", stdout=out)
    assert "Node pairing code:" in out.getvalue()
    code = DevicePairingCode.objects.get()
    assert code.device_kind == "node" and code.kind == "strong"
    out = io.StringIO()
    call_command("issue_pairing_code", "--node", "--plain", stdout=out)
    raw = out.getvalue().strip()
    device, _ = devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
    assert device.is_node
    issued = audits("accounts.pairing_code.issued").order_by("created_at")
    assert [row.metadata["device_kind"] for row in issued] == ["node", "node"]


def test_web_session_issues_a_node_code(owner, web_client):
    response = web_client(owner).post(
        "/api/v1/auth/pairing-codes", {"kind": "strong", "device_kind": "node"}, format="json"
    )
    assert response.status_code == 201, response.content
    assert response.json()["device_kind"] == "node"
    device, _ = devices.enroll_node_device(
        raw_code=response.json()["code"], node_name="Mac", client_ip=LAN_IP
    )
    assert device.is_node


# --- the node never holds a web session ---


def test_a_node_secret_is_refused_by_start_device_session(owner):
    _, secret = pair_node(owner)
    with pytest.raises(devices.DeviceRejected):
        devices.start_device_session(raw_secret=secret)
    response = APIClient().post(
        "/api/v1/auth/devices/session", {"device_secret": secret}, format="json"
    )
    assert response.status_code == 401


def test_a_node_row_behind_a_browser_prefix_still_gets_no_session(owner):
    """Defense in depth: even a `jdv_`-shaped secret whose row is a node
    (impossible through the services) mints nothing."""
    device, _ = pair_node(owner)
    fake = "jdv_" + secrets.token_urlsafe(32)
    TrustedDevice.objects.filter(pk=device.pk).update(secret_hash=hash_token(fake))
    with pytest.raises(devices.DeviceRejected):
        devices.start_device_session(raw_secret=fake)


def test_a_forged_jwt_naming_a_node_device_is_refused(owner):
    device, _ = pair_node(owner)
    # A JWT pair minted directly for the node's row (forged `did`).
    tokens = devices.issue_session_tokens(device)
    response = bearer(tokens["access"]).get("/api/v1/auth/me")
    assert response.status_code == 401
    response = APIClient().post(
        "/api/v1/auth/refresh", {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 401


def test_a_node_device_is_listed_with_its_kind(owner, web_client):
    pair_node(owner)
    body = web_client(owner).get("/api/v1/auth/devices").json()
    assert [d["kind"] for d in body] == ["node"]
    assert body[0]["credential_expires_at"] is not None
    assert "secret_hash" not in body[0] and "previous_secret_hash" not in body[0]


# --- the authenticator ---


def test_authenticate_node_secret_accepts_a_fresh_node(owner):
    device, secret = pair_node(owner)
    credential = devices.authenticate_node_secret(raw_secret=secret)
    assert credential.device == device and credential.via_previous_secret is False


@pytest.mark.parametrize(
    "raw",
    ["", "jdv_" + "x" * 43, TOKEN_PREFIX + "x" * 43, "jnd_" + "x" * 43],
    ids=["empty", "browser-prefix", "pat-prefix", "unknown"],
)
def test_authenticate_node_secret_refuses_other_shapes(owner, raw):
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=raw)
    failure = audits("accounts.node.auth_failed").get()
    # Never a fragment of a non-node credential in the audit log.
    assert failure.metadata["secret_prefix"] == ("jnd_xxxx" if raw.startswith("jnd_") else "")


def test_node_auth_failures_are_flood_capped(owner):
    for _ in range(25):
        with pytest.raises(devices.NodeCredentialRejected):
            devices.authenticate_node_secret(raw_secret="jnd_" + secrets.token_urlsafe(32))
    from accounts.authentication import AUTH_FAILURE_AUDIT_LIMIT

    assert audits("accounts.node.auth_failed").count() == AUTH_FAILURE_AUDIT_LIMIT


@pytest.mark.parametrize("change", ["inactive", "not_owner", "agent"])
def test_a_node_of_a_non_owner_is_refused(owner, change):
    _, secret = pair_node(owner)
    if change == "inactive":
        User.objects.filter(pk=owner.pk).update(is_active=False)
    elif change == "not_owner":
        User.objects.filter(pk=owner.pk).update(is_owner=False)
    else:
        User.objects.filter(pk=owner.pk).update(is_agent=True)
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret)
    assert excinfo.value.reason == "inactive_owner"


# --- age cap and idle expiry (frozen clock) ---


def test_the_age_cap_refuses_an_unrotated_node(owner, settings):
    settings.NODE_CREDENTIAL_MAX_AGE_DAYS = 90
    settings.NODE_IDLE_EXPIRY_DAYS = 365
    device, secret = pair_node(owner)
    issued = device.secret_issued_at
    just_before = issued + timedelta(days=90) - timedelta(seconds=1)
    assert devices.authenticate_node_secret(raw_secret=secret, now=just_before)
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret, now=issued + timedelta(days=90))
    assert excinfo.value.reason == "credential_expired"
    device.refresh_from_db()
    assert devices.node_device_refusal(device, now=issued + timedelta(days=91)) == (
        "credential_expired"
    )


def test_the_age_cap_counts_from_the_last_rotation(owner, settings):
    settings.NODE_CREDENTIAL_MAX_AGE_DAYS = 90
    settings.NODE_IDLE_EXPIRY_DAYS = 365
    device, secret = pair_node(owner)
    later = device.secret_issued_at + timedelta(days=80)
    _, new_secret = devices.rotate_node_secret(raw_secret=secret, now=later)
    assert devices.authenticate_node_secret(raw_secret=new_secret, now=later + timedelta(days=89))
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=new_secret, now=later + timedelta(days=90))


def test_node_idle_expiry_uses_the_node_window(owner, settings):
    settings.NODE_IDLE_EXPIRY_DAYS = 30
    settings.DEVICE_IDLE_EXPIRY_DAYS = 90
    device, secret = pair_node(owner)
    seen = device.last_seen_at
    assert devices.authenticate_node_secret(
        raw_secret=secret, now=seen + timedelta(days=29, hours=23)
    )
    # The successful connection above refreshed last_seen.
    device.refresh_from_db()
    assert device.last_seen_at > seen
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(
            raw_secret=secret, now=device.last_seen_at + timedelta(days=30)
        )
    assert excinfo.value.reason == "idle_expired"


def test_browser_idle_expiry_is_unchanged(owner, settings):
    settings.NODE_IDLE_EXPIRY_DAYS = 1
    settings.DEVICE_IDLE_EXPIRY_DAYS = 90
    device, _ = devices.enroll_device(raw_code=browser_code(owner), device_name="Safari")
    assert device.is_usable(now=timezone.now() + timedelta(days=89))
    assert device.credential_expires_at() is None


# --- 2. rotation without re-pairing ---


def test_rotation_returns_a_new_secret_once_and_is_audited(owner):
    device, secret = pair_node(owner)
    rotated, new_secret = devices.rotate_node_secret(raw_secret=secret, client_ip=LAN_IP)
    assert new_secret.startswith("jnd_") and new_secret != secret
    assert rotated.pk == device.pk
    device.refresh_from_db()
    assert device.secret_hash == hash_token(new_secret)
    assert device.previous_secret_hash == hash_token(secret)
    row = audits("accounts.node.rotated").get()
    assert row.resource_id == str(device.pk)
    assert new_secret not in json.dumps(row.metadata) and secret not in json.dumps(row.metadata)


def test_old_secret_valid_within_the_grace(owner, settings):
    settings.NODE_ROTATION_GRACE_SECONDS = 600
    _, secret = pair_node(owner)
    now = timezone.now()
    devices.rotate_node_secret(raw_secret=secret, now=now)
    credential = devices.authenticate_node_secret(
        raw_secret=secret, now=now + timedelta(seconds=599)
    )
    assert credential.via_previous_secret is True


def test_old_secret_invalid_after_the_grace(owner, settings):
    settings.NODE_ROTATION_GRACE_SECONDS = 600
    _, secret = pair_node(owner)
    now = timezone.now()
    _, new_secret = devices.rotate_node_secret(raw_secret=secret, now=now)
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret, now=now + timedelta(seconds=600))
    assert excinfo.value.reason == "previous_expired"
    assert devices.authenticate_node_secret(raw_secret=new_secret, now=now + timedelta(seconds=601))


def test_old_secret_invalid_after_first_use_of_the_new_one(owner):
    device, secret = pair_node(owner)
    _, new_secret = devices.rotate_node_secret(raw_secret=secret)
    # Still in the grace, but the node has switched over.
    assert devices.authenticate_node_secret(raw_secret=new_secret)
    device.refresh_from_db()
    assert device.previous_secret_hash == "" and device.previous_secret_valid_until is None
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret)
    assert excinfo.value.reason == "unknown"


def test_rotation_with_the_previous_secret_is_refused(owner):
    device, secret = pair_node(owner)
    _, new_secret = devices.rotate_node_secret(raw_secret=secret)
    with pytest.raises(devices.NodeRotationRefused):
        devices.rotate_node_secret(raw_secret=secret)
    device.refresh_from_db()
    # Nothing chained: the current secret is still the first rotation's.
    assert device.secret_hash == hash_token(new_secret)
    assert audits("accounts.node.rotated").count() == 1
    assert audits("accounts.node.rotation_refused").get().metadata["reason"] == ("previous_secret")


def test_rotation_with_the_new_secret_chains_normally(owner):
    _, secret = pair_node(owner)
    _, second = devices.rotate_node_secret(raw_secret=secret)
    _, third = devices.rotate_node_secret(raw_secret=second)
    assert devices.authenticate_node_secret(raw_secret=third)
    for old in (secret, second):
        with pytest.raises(devices.NodeCredentialRejected):
            devices.authenticate_node_secret(raw_secret=old)


@pytest.mark.django_db(transaction=True)
def test_concurrent_rotations_yield_one_successor(owner):
    _, secret = pair_node(owner)
    barrier = threading.Barrier(2)
    results = []

    def attempt():
        try:
            barrier.wait()
            results.append(devices.rotate_node_secret(raw_secret=secret)[1])
        except (devices.NodeRotationRefused, devices.NodeCredentialRejected):
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("refused") == 1
    assert AuditLog.objects.filter(action="accounts.node.rotated").count() == 1


def test_rotate_endpoint_returns_the_secret_once(owner, settings):
    settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS = 30
    device, secret = pair_node(owner)
    response = bearer(secret).post(ROTATE_URL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["node_id"] == str(device.pk)
    assert body["node_secret"].startswith("jnd_")
    assert body["rotate_after_days"] == 30
    assert body["previous_valid_until"] and body["credential_expires_at"]
    # The new secret works on the same endpoint; the old one is then dead.
    again = bearer(body["node_secret"]).post(ROTATE_URL)
    assert again.status_code == 200
    assert bearer(secret).post(ROTATE_URL).status_code == 401


def test_rotate_endpoint_refuses_the_previous_secret_with_403(owner):
    _, secret = pair_node(owner)
    assert bearer(secret).post(ROTATE_URL).status_code == 200
    response = bearer(secret).post(ROTATE_URL)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


# --- 3. revocation ---


def test_revoking_one_node_fails_its_next_authentication(owner, fake_redis):
    device, secret = pair_node(owner)
    other, other_secret = pair_node(owner, name="Mac mini")
    devices.revoke_device(device=device, actor=owner)
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret)
    assert excinfo.value.reason == "revoked"
    assert devices.authenticate_node_secret(raw_secret=other_secret).device == other
    assert bearer(secret).post(ROTATE_URL).status_code == 401


def test_the_revoke_endpoint_revokes_a_node(owner, web_client, fake_redis):
    device, secret = pair_node(owner)
    response = web_client(owner).post(f"/api/v1/auth/devices/{device.pk}/revoke")
    assert response.status_code == 200
    assert response.json()["kind"] == "node" and response.json()["revoked_at"]
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=secret)


def test_revoke_all_devices_covers_nodes(owner, fake_redis):
    browser, _ = devices.enroll_device(raw_code=browser_code(owner), device_name="Safari")
    node_a, secret_a = pair_node(owner)
    node_b, secret_b = pair_node(owner, name="Mac mini")
    assert devices.revoke_all_devices(user=owner) == 3
    for secret in (secret_a, secret_b):
        with pytest.raises(devices.NodeCredentialRejected):
            devices.authenticate_node_secret(raw_secret=secret)
    assert audits("accounts.device.revoked_all").get().metadata["count"] == 3


def test_revoke_all_devices_command_counts_nodes(owner, fake_redis):
    pair_node(owner)
    out = io.StringIO()
    call_command("revoke_all_devices", stdout=out)
    assert "Revoked 1 device(s)." in out.getvalue()


def test_revocation_is_published_once_per_device_after_commit(
    owner, fake_redis, django_capture_on_commit_callbacks
):
    node_a, _ = pair_node(owner)
    node_b, _ = pair_node(owner, name="Mac mini")
    with django_capture_on_commit_callbacks(execute=True):
        devices.revoke_device(device=node_a, actor=owner)
    with django_capture_on_commit_callbacks(execute=True):
        devices.revoke_device(device=node_a, actor=owner)  # idempotent: no re-publish
    with django_capture_on_commit_callbacks(execute=True):
        devices.revoke_all_devices(user=owner)
    assert fake_redis.published == [
        (revocation.REVOCATION_CHANNEL, {"device_id": str(node_a.pk), "user_id": str(owner.pk)}),
        (revocation.REVOCATION_CHANNEL, {"device_id": str(node_b.pk), "user_id": str(owner.pk)}),
    ]


def test_a_redis_error_never_fails_the_revoke(
    owner, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(revocation, "_client", lambda: FakeRedis(fail=True))
    device, secret = pair_node(owner)
    with django_capture_on_commit_callbacks(execute=True):
        devices.revoke_device(device=device, actor=owner)
    device.refresh_from_db()
    assert device.revoked_at is not None
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=secret)


def test_an_unreachable_redis_url_never_raises(settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    assert revocation.publish_device_revoked("d", "u") is False


def test_publish_refuses_a_payload_without_a_user():
    assert revocation.publish_device_revoked("some-device", "") is False
    assert revocation.publish_kill_switch_engaged(None) is False


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [
        ('{"device_id": "abc", "user_id": "u1"}', ("abc", "u1")),
        ('{"device_id": "*", "user_id": "u1"}', ("*", "u1")),
        ('{"device_id": "*"}', None),
        ('{"device_id": "*", "user_id": ""}', None),
        ('{"device_id": 5, "user_id": "u1"}', None),
        ("[1, 2]", None),
        ("not json", None),
        (None, None),
    ],
)
def test_the_subscriber_parser_fails_safe(raw, parsed):
    assert revocation.parse_revocation(raw) == parsed


# --- 4. kill switch ---


def test_kill_switch_on_then_off(owner, fake_redis, django_capture_on_commit_callbacks):
    device, secret = pair_node(owner)
    with django_capture_on_commit_callbacks(execute=True):
        devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    with pytest.raises(devices.NodeCredentialRejected) as excinfo:
        devices.authenticate_node_secret(raw_secret=secret)
    assert excinfo.value.reason == "kill_switch"
    assert bearer(secret).post(ROTATE_URL).status_code == 401
    # Pairing a new node is refused (issue and enroll), nothing consumed.
    with pytest.raises(devices.NodeKillSwitchEngaged):
        node_code(owner)
    assert fake_redis.published == [
        (revocation.REVOCATION_CHANNEL, {"device_id": "*", "user_id": str(owner.pk)})
    ]

    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    # Restored without re-pairing: the same secret works again.
    assert devices.authenticate_node_secret(raw_secret=secret).device == device
    assert audits("accounts.node.kill_switch.engaged").count() == 1
    assert audits("accounts.node.kill_switch.released").count() == 1


def test_kill_switch_refuses_enrolling_with_a_code_issued_before(owner):
    raw = node_code(owner, DevicePairingCode.Kind.STRONG)
    devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    with pytest.raises(devices.NodeKillSwitchEngaged):
        devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
    assert DevicePairingCode.objects.get().consumed_at is None
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    device, _ = devices.enroll_node_device(raw_code=raw, node_name="Mac", client_ip=LAN_IP)
    assert device.is_node


def test_kill_switch_leaves_browsers_alone(owner):
    browser, browser_secret = devices.enroll_device(
        raw_code=browser_code(owner), device_name="Safari"
    )
    devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    device, _ = devices.start_device_session(raw_secret=browser_secret)
    assert device == browser
    # Browser pairing still works too.
    assert devices.enroll_device(raw_code=browser_code(owner), device_name="Firefox")


def test_one_owners_kill_switch_leaves_another_owners_node_alone(
    owner, fake_redis, django_capture_on_commit_callbacks
):
    _, owner_secret = pair_node(owner)
    # Owner B: the single-owner constraint allows one `is_owner` at a time,
    # so B's node row is created directly and B becomes the owner below.
    other = register_user(email="b@test.dev", password=PASSWORD)
    owner_b_node = TrustedDevice.objects.create(
        user=other,
        name="B's Mac",
        kind=TrustedDevice.Kind.NODE,
        secret_hash=hash_token("jnd_owner_b_secret"),
        secret_issued_at=timezone.now(),
        enrolled_via=TrustedDevice.EnrolledVia.STRONG_CODE,
        last_seen_at=timezone.now(),
    )
    with django_capture_on_commit_callbacks(execute=True):
        devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    # The wildcard is scoped by user_id: only owner A's sockets match.
    assert fake_redis.published == [
        (revocation.REVOCATION_CHANNEL, {"device_id": "*", "user_id": str(owner.pk)})
    ]
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=owner_secret)
    # B's kill switch is released: B's node is refused only for not being
    # the owner -- never for A's kill switch.
    owner_b_node.refresh_from_db()
    assert not devices.node_kill_switch_engaged(other)
    User.objects.filter(pk=owner.pk).update(is_owner=False)
    User.objects.filter(pk=other.pk).update(is_owner=True)
    credential = devices.authenticate_node_secret(raw_secret="jnd_owner_b_secret")
    assert credential.device == owner_b_node


def test_kill_switch_is_idempotent(owner, fake_redis, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
        devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    assert audits("accounts.node.kill_switch.engaged").count() == 1
    assert len(fake_redis.published) == 2  # re-engaging re-announces
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.WEB)
    assert audits("accounts.node.kill_switch.released").count() == 1


def test_only_the_owner_can_use_the_kill_switch(owner):
    other = register_user(email="member@test.dev", password=PASSWORD)
    with pytest.raises(devices.NotOwner):
        devices.engage_node_kill_switch(owner=other, via=NodeKillSwitch.Via.WEB)


def test_kill_switch_endpoints(owner, web_client, fake_redis):
    device, secret = pair_node(owner)
    browser, _ = devices.enroll_device(raw_code=browser_code(owner), device_name="Safari")
    client = web_client(owner, device=browser)
    assert client.get(KILL_URL).json()["engaged"] is False
    engaged = client.post(KILL_URL)
    assert engaged.status_code == 200 and engaged.json()["engaged"] is True
    assert engaged.json()["engaged_via"] == "web"
    switch = NodeKillSwitch.objects.get(user=owner)
    assert switch.engaged_by_device == browser
    # The web session issues no node code while engaged.
    refused = client.post(
        "/api/v1/auth/pairing-codes", {"kind": "pin", "device_kind": "node"}, format="json"
    )
    assert refused.status_code == 409
    with pytest.raises(devices.NodeCredentialRejected):
        devices.authenticate_node_secret(raw_secret=secret)
    released = client.delete(KILL_URL)
    assert released.status_code == 200 and released.json()["engaged"] is False
    assert devices.authenticate_node_secret(raw_secret=secret).device == device


def test_kill_switch_command_prints_the_state(owner, fake_redis):
    out = io.StringIO()
    call_command("node_kill_switch", "status", stdout=out)
    assert "released" in out.getvalue()
    out = io.StringIO()
    call_command("node_kill_switch", "on", stdout=out)
    assert "ENGAGED" in out.getvalue()
    assert NodeKillSwitch.objects.get(user=owner).engaged_via == "management_command"
    out = io.StringIO()
    call_command("node_kill_switch", "status", stdout=out)
    assert "ENGAGED" in out.getvalue()
    out = io.StringIO()
    call_command("node_kill_switch", "off", stdout=out)
    assert "released" in out.getvalue()
    assert audits("accounts.node.kill_switch.engaged").get().metadata["via"] == (
        "management_command"
    )


def test_kill_switch_command_without_an_owner_fails(db):
    with pytest.raises(CommandError):
        call_command("node_kill_switch", "on")


# --- 6. role x endpoint: rotate and kill switch ---


def _principal(kind, owner, workspace, web_client):
    """The five Phase 4 principals plus a `hook` PAT, a node secret and a
    real browser JWT (the web session through the real authenticator)."""
    if kind == "owner-web":
        return web_client(owner)
    if kind == "owner-web-jwt":
        device, _ = devices.enroll_device(raw_code=browser_code(owner), device_name="Safari")
        return bearer(devices.issue_session_tokens(device)["access"])
    if kind in ("owner-cli", "owner-openai", "owner-hook"):
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=workspace,
            user=owner,
            name=kind,
            kind=kind.removeprefix("owner-"),
            token_hash=hash_token(raw),
        )
        return bearer(raw)
    if kind == "owner-browser-secret":
        _, secret = devices.enroll_device(raw_code=browser_code(owner), device_name="Safari")
        return bearer(secret)
    if kind == "node":
        return bearer(pair_node(owner, name="principal")[1])
    if kind == "non-owner":
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
    if kind == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


_PRINCIPALS = [
    "owner-web",
    "owner-web-jwt",
    "owner-cli",
    "owner-openai",
    "owner-hook",
    "owner-browser-secret",
    "node",
    "non-owner",
    "agent",
    "anonymous",
]


@pytest.mark.parametrize("principal", _PRINCIPALS)
def test_rotate_accepts_only_a_node_secret(owner, workspace, web_client, fake_redis, principal):
    client = _principal(principal, owner, workspace, web_client)
    response = client.post(ROTATE_URL)
    if principal == "node":
        assert response.status_code == 200, response.content
    elif principal in ("owner-web", "non-owner", "agent"):
        # A (forced) web session passes no node authenticator: IsNodeChannel.
        assert response.status_code == 403, response.content
    else:
        assert response.status_code == 401, response.content


@pytest.mark.parametrize("method", ["get", "post", "delete"])
@pytest.mark.parametrize("principal", _PRINCIPALS)
def test_kill_switch_accepts_only_the_owner_web_session(
    owner, workspace, web_client, fake_redis, principal, method
):
    client = _principal(principal, owner, workspace, web_client)
    response = getattr(client, method)(KILL_URL)
    if principal in ("owner-web", "owner-web-jwt"):
        assert response.status_code == 200, response.content
    elif principal in ("non-owner", "agent"):
        assert response.status_code == 403, response.content
    else:
        assert response.status_code == 401, response.content
    if principal not in ("owner-web", "owner-web-jwt"):
        assert not NodeKillSwitch.objects.filter(engaged_at__isnull=False).exists()
