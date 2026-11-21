"""`POST /api/v1/nodes/pair` (Phase 9 brief D4): success once, the code
refusals, the kill switch, the fail-closed throttle, the missing signing
key, no logging of the response, role x endpoint -- and the branch's done
criterion: a node paired through the endpoint verifies a signed `invoke`
with the returned key, and the same envelope fails with any other key."""

import base64
import logging
import time

import pytest
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts import devices
from accounts.models import DevicePairingCode, NodeKillSwitch, TrustedDevice
from audit.models import AuditLog
from nodes.keys import signing_keyring
from nodes.models import Node
from nodes.protocol import SignatureError, validate, verify_envelope
from nodes.protocol.signing import SigningKey, load_public_key_b64
from nodes.services import sign_read_invoke
from nodes.tests.conftest import PRINCIPALS, node_code, principal_client
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PAIR_URL = "/api/v1/nodes/pair"
BFF_KEY = "test-bff-shared-secret"


def pair(client=None, code="", **extra):
    body = {"code": code, "node_name": "MacBook Pro", "protocol_versions": [1], **extra}
    return (client or APIClient()).post(PAIR_URL, body, format="json")


def from_ip(ip):
    client = APIClient()
    client.credentials(HTTP_X_FRIDAY_CLIENT_IP=ip, HTTP_X_FRIDAY_BFF_KEY=BFF_KEY)
    return client


class _DownCache:
    def get(self, *args, **kwargs):
        raise ConnectionError("redis down")

    set = get


# --- success ---


def test_a_node_code_pairs_and_the_secret_and_keys_are_returned_once(owner, personal):
    code = node_code(owner)
    response = pair(code=code)
    assert response.status_code == 201, response.content
    body = response.json()
    assert set(body) == {
        "node_id",
        "node_secret",
        "server_signing_keys",
        "protocol_version",
        "gateway_path",
        "rotate_after_days",
    }
    ring = signing_keyring()
    assert body["node_secret"].startswith("jnd_")
    assert body["server_signing_keys"] == [ring.active.public_entry()]
    assert body["protocol_version"] == 1
    assert body["gateway_path"] == "/ws/node/v1"
    assert body["rotate_after_days"] == 30

    node = Node.objects.get(pk=body["node_id"])
    assert node.device_id == node.pk  # node_id == the TrustedDevice id
    assert node.device.kind == TrustedDevice.Kind.NODE
    assert node.owner == owner and node.workspace == personal
    assert node.pinned_key_id == ring.key_id
    assert node.name == "MacBook Pro"
    # The secret is stored only as a hash.
    assert node.device.secret_hash != body["node_secret"]
    assert devices.authenticate_node_secret(raw_secret=body["node_secret"]).device == node.device
    assert AuditLog.objects.filter(action="nodes.node.paired", resource_id=str(node.pk)).exists()
    assert AuditLog.objects.filter(action="accounts.node.enrolled").count() == 1

    # Exactly once: the code is single use, the secret is never shown again.
    again = pair(code=code)
    assert again.status_code == 400 and again.json()["code"] == "invalid_code"
    assert Node.objects.count() == 1


def test_the_done_criterion_signed_invoke_verifies_only_with_the_returned_key(owner):
    response = pair(code=node_code(owner))
    assert response.status_code == 201
    body = response.json()
    (entry,) = body["server_signing_keys"]
    pinned = {entry["key_id"]: load_public_key_b64(entry["public_key_b64"])}

    node = Node.objects.get(pk=body["node_id"])
    envelope = sign_read_invoke(
        node=node,
        workspace_id=node.workspace_id,
        capability="git.status",
        args={"repo_path": "~/work/acme"},
        trace_id="trace-1",
    )
    validate(envelope, "server_to_node")
    assert envelope["node_id"] == body["node_id"]
    assert 0 < envelope["expires_at"] - envelope["issued_at"] <= 30
    assert abs(envelope["issued_at"] - time.time()) < 5
    verify_envelope(envelope, pinned)

    other = SigningKey.generate()
    wrong_keys = (
        {entry["key_id"]: other.public_key_bytes},
        {other.key_id: other.public_key_bytes},
    )
    for wrong in wrong_keys:
        with pytest.raises(SignatureError):
            verify_envelope(envelope, wrong)


def test_a_blank_name_gets_the_default(owner):
    response = pair(code=node_code(owner), node_name="")
    assert response.status_code == 201
    assert Node.objects.get().name == "Mac düğümü"


# --- refusals ---


def test_a_browser_code_is_refused_and_not_consumed(owner):
    code = node_code(owner, device_kind=TrustedDevice.Kind.BROWSER)
    response = pair(code=code)
    assert response.status_code == 400 and response.json()["code"] == "invalid_code"
    assert not Node.objects.exists()
    # Still a live browser code.
    device, _ = devices.enroll_device(raw_code=code, device_name="Safari")
    assert device.kind == TrustedDevice.Kind.BROWSER


def test_an_unknown_code_is_refused(owner):
    response = pair(code="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert response.status_code == 400 and response.json()["code"] == "invalid_code"
    assert not Node.objects.exists()


def test_no_owner_is_the_same_generic_refusal(db):
    response = pair(code="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert response.status_code == 400 and response.json()["code"] == "invalid_code"


def test_a_pin_from_outside_the_pairing_networks_is_refused(owner, settings):
    settings.FRIDAY_BFF_SHARED_SECRET = BFF_KEY
    code = node_code(owner, kind=DevicePairingCode.Kind.PIN)
    response = pair(from_ip("203.0.113.7"), code=code)
    assert response.status_code == 403 and response.json()["code"] == "pin_network_refused"
    assert not Node.objects.exists()
    # From the LAN the same PIN pairs.
    assert pair(from_ip("10.0.0.7"), code=code).status_code == 201


def test_failed_pin_attempts_still_burn_through_the_endpoint(owner, settings):
    settings.FRIDAY_BFF_SHARED_SECRET = BFF_KEY
    node_code(owner, kind=DevicePairingCode.Kind.PIN)
    for _ in range(2):
        assert pair(from_ip("10.0.0.7"), code="00000000").status_code == 400
    live = DevicePairingCode.objects.get(device_kind=TrustedDevice.Kind.NODE)
    # The refusal's own writes commit (they are not rolled back with the
    # pairing transaction).
    assert live.attempts == 2
    assert AuditLog.objects.filter(action="accounts.device.enroll_failed").count() == 2


def test_kill_switch_engaged_is_refused(owner):
    code = node_code(owner)
    devices.engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    response = pair(code=code)
    assert response.status_code == 409 and response.json()["code"] == "kill_switch_engaged"
    assert not Node.objects.exists()
    devices.release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
    assert pair(code=code).status_code == 201  # the code was not consumed


def test_no_supported_protocol_version_consumes_nothing(owner):
    code = node_code(owner)
    response = pair(code=code, protocol_versions=[2, 3])
    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_protocol_version"
    assert pair(code=code, protocol_versions=[2, 1]).status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "X" * 200, "protocol_versions": [1]},
        {"code": "ABC", "protocol_versions": []},
        {"code": "ABC", "protocol_versions": ["one"]},
        {"code": "ABC", "protocol_versions": [1], "node_name": "n" * 101},
    ],
)
def test_malformed_bodies_are_400(owner, body):
    assert APIClient().post(PAIR_URL, body, format="json").status_code == 400
    assert not Node.objects.exists()


@pytest.mark.parametrize(
    "key",
    ["", "garbage", base64.b64encode(b"short").decode()],
)
def test_a_missing_or_invalid_signing_key_is_503_and_pairs_nothing(owner, settings, key):
    code = node_code(owner)
    settings.NODE_SIGNING_KEY = key
    response = pair(code=code)
    assert response.status_code == 503 and response.json()["code"] == "signing_unavailable"
    assert not Node.objects.exists()
    assert not TrustedDevice.objects.filter(kind=TrustedDevice.Kind.NODE).exists()
    assert DevicePairingCode.objects.get(device_kind="node").consumed_at is None


def test_throttle_returns_429(owner, monkeypatch):
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**ScopedRateThrottle.THROTTLE_RATES, "node_pair": "2/hour"},
    )
    assert pair(code="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567").status_code == 400
    assert pair(code="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567").status_code == 400
    third = pair(code=node_code(owner))
    assert third.status_code == 429
    assert not Node.objects.exists()


def test_throttle_backend_down_fails_closed_with_503(owner, monkeypatch):
    monkeypatch.setattr(ScopedRateThrottle, "cache", _DownCache())
    response = pair(code=node_code(owner))
    assert response.status_code == 503
    assert response.json()["code"] == "throttle_backend_unavailable"
    assert not Node.objects.exists()


def test_the_node_pair_scope_is_configured(settings):
    assert "node_pair" in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]


def test_the_response_is_never_logged(owner, caplog):
    caplog.set_level(logging.DEBUG)
    code = node_code(owner)
    response = pair(code=code)
    assert response.status_code == 201
    body = response.json()
    logged = caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert body["node_secret"] not in logged
    assert body["node_secret"][4:] not in logged
    assert code not in logged
    for entry in AuditLog.objects.all():
        assert body["node_secret"] not in str(entry.metadata)


# --- role x endpoint ---


@pytest.mark.parametrize("principal", PRINCIPALS)
def test_every_principal_is_anonymous_to_the_pairing_endpoint(
    owner, personal, web_client, principal
):
    """Anonymous by design: no credential is read, so no principal gets
    special treatment -- a bad code is refused for everyone and a valid one
    pairs a node of the OWNER, whoever presents it."""
    client = principal_client(principal, owner, personal, web_client)
    before = Node.objects.count()
    bad = pair(client, code="ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_code"
    good = pair(client, code=node_code(owner))
    assert good.status_code == 201, good.content
    node = Node.objects.get(pk=good.json()["node_id"])
    assert node.owner == owner
    assert node.workspace == Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    assert Node.objects.count() == before + 1


def test_only_post_is_routed(owner):
    client = APIClient()
    for method in ("get", "put", "patch", "delete"):
        assert getattr(client, method)(PAIR_URL).status_code == 405
