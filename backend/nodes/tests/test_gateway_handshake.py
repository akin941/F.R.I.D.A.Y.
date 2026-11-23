"""The node socket's handshake, `hello`, heartbeat, fan-out and limits
(Phase 9 brief D3, D6; branch 4 tests). The WS role x endpoint matrix runs
against the five Phase 4 principals' credentials plus PATs, a JWT and node
secrets in every unusable state."""

import json
import secrets
import time
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import devices
from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, TrustedDevice
from nodes import bus, presence
from nodes.keys import signing_keyring
from nodes.models import CapabilitySnapshot, Node
from nodes.protocol.messages import MAX_FRAME_BYTES, catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.tests.gateway_harness import V1, V2, reference_node
from nodes.tests.gateway_helpers import direct_node, other_user, personal_of

pytestmark = pytest.mark.django_db(transaction=True)


def _hello(version, catalogue, frame_id="3f1c2b4a-5d6e-4f70-8a9b-0c1d2e3f4a5b"):
    return {
        "v": version,
        "type": "hello",
        "id": frame_id,
        "node_version": "test 1",
        "os_version": "test os",
        "catalogue": catalogue,
        "catalogue_sha256": catalogue_sha256(catalogue),
    }


def _heartbeat(version, seq=1):
    return {
        "v": version,
        "type": "heartbeat",
        "id": f"4a1c2b3d-5e6f-4a7b-8c9d-0e1f2a3b4c{seq:02d}",
        "seq": seq,
        "uptime_seconds": 1,
        "spool_count": 2,
        "spool_bytes": 300,
    }


# --- the WS role x endpoint matrix (section c.6) -------------------------


def _browser_secret(owner):
    raw = "jdv_" + secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=owner,
        name="Browser",
        secret_hash=hash_token(raw),
        enrolled_via=TrustedDevice.EnrolledVia.values[0],
    )
    return raw, device


def _pat(owner, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=personal_of(owner),
        user=owner,
        name=kind,
        kind=kind,
        token_hash=hash_token(raw),
    )
    return raw


def _credential(kind, owner, paired):
    """The bearer value for one principal of the matrix."""
    if kind == "anonymous":
        return None
    if kind == "browser-secret":
        return _browser_secret(owner)[0]
    if kind in ("cli-pat", "openai-pat", "hook-pat"):
        return _pat(owner, kind.removesuffix("-pat"))
    if kind == "jwt":
        _, device = _browser_secret(owner)
        return devices.issue_session_tokens(device)["access"]
    node, secret, _ = paired()
    if kind == "revoked-node":
        devices.revoke_device(device=node.device, actor=owner)
    elif kind == "expired-node":
        TrustedDevice.objects.filter(pk=node.device_id).update(
            secret_issued_at=timezone.now() - timedelta(days=91)
        )
    elif kind == "idle-node":
        TrustedDevice.objects.filter(pk=node.device_id).update(
            last_seen_at=timezone.now() - timedelta(days=31)
        )
    elif kind == "kill-switch":
        devices.engage_node_kill_switch(owner=owner, via="test")
    elif kind == "previous-secret-after-grace":
        devices.rotate_node_secret(raw_secret=secret)
        TrustedDevice.objects.filter(pk=node.device_id).update(
            previous_secret_valid_until=timezone.now() - timedelta(seconds=1)
        )
    return secret


@pytest.mark.parametrize(
    "kind",
    [
        "anonymous",
        "browser-secret",
        "cli-pat",
        "openai-pat",
        "hook-pat",
        "jwt",
        "revoked-node",
        "expired-node",
        "idle-node",
        "kill-switch",
        "previous-secret-after-grace",
    ],
)
def test_every_non_node_or_unusable_credential_is_closed_4401(gateway, owner, paired, kind):
    app, harness = gateway
    link = harness.connect(secret=_credential(kind, owner, paired))
    assert link.wait_closed()
    assert link.close_code == 4401
    assert not link.server_frames  # nothing was ever sent
    assert app.connections == {}


def test_a_non_owner_users_node_is_closed_4401(gateway, owner, cleanup_nodes):
    app, harness = gateway
    node, secret = direct_node(other_user())
    cleanup_nodes.append(str(node.pk))
    link = harness.connect(secret=secret)
    assert link.wait_closed() and link.close_code == 4401
    assert not bus.is_present(node.pk)


def test_an_origin_header_is_closed_4403(gateway, paired):
    app, harness = gateway
    node, secret, _ = paired()
    link = harness.connect(secret=secret, origin="https://evil.example")
    assert link.wait_closed() and link.close_code == 4403
    assert not link.server_frames


@pytest.mark.parametrize("offer", [(), ("friday-node.v3",), ("chat",)])
def test_a_wrong_subprotocol_is_closed_4400(gateway, paired, offer):
    app, harness = gateway
    node, secret, _ = paired()
    link = harness.connect(secret=secret, subprotocols=offer)
    assert link.wait_closed() and link.close_code == 4400
    assert link.subprotocol is None


def test_no_hello_within_the_timeout_is_closed_4408(make_app, real_revocation, paired):
    app, harness = make_app(hello_timeout=0.3)
    node, secret, _ = paired()
    link = harness.connect(secret=secret)
    assert link.wait_closed() and link.close_code == 4408
    assert link.accepted
    assert not bus.is_present(node.pk)


def test_redis_down_refuses_the_handshake_1013(gateway, paired, settings):
    app, harness = gateway
    node, secret, _ = paired()
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    link = harness.connect(secret=secret)
    assert link.wait_closed() and link.close_code == 1013


@pytest.mark.parametrize("key", ["", "not-base64!"])
def test_a_missing_or_invalid_signing_key_refuses_the_handshake_1013(
    gateway, paired, settings, key
):
    app, harness = gateway
    node, secret, _ = paired()
    settings.NODE_SIGNING_KEY = key
    link = harness.connect(secret=secret)
    assert link.wait_closed() and link.close_code == 1013
    assert not bus.is_present(node.pk)


def test_the_highest_common_version_is_negotiated(link_node):
    link, node, reference = link_node(subprotocols=(V2, V1))
    assert link.subprotocol == V2 and reference.version == 2
    link1, node1, reference1 = link_node(subprotocols=(V1,), name="Old")
    assert link1.subprotocol == V1 and reference1.version == 1
    assert bus.present_version(node.pk) == 2 and bus.present_version(node1.pk) == 1


# --- hello ----------------------------------------------------------------


def test_hello_writes_a_snapshot_only_when_the_hash_changes(gateway, paired):
    app, harness = gateway
    node, secret, keyring = paired()
    link = harness.connect(secret=secret, node=reference_node(node, keyring), subprotocols=(V1,))
    assert link.frames_of("hello.ack")
    assert CapabilitySnapshot.objects.filter(node=node).count() == 1
    link.drop()
    assert link.wait_done()
    again = harness.connect(secret=secret, node=reference_node(node, keyring), subprotocols=(V1,))
    assert again.frames_of("hello.ack")
    assert CapabilitySnapshot.objects.filter(node=node).count() == 1
    narrower = [e for e in default_catalogue() if e["name"] != "windows.list"]
    third = harness.connect(
        secret=secret, node=reference_node(node, keyring, catalogue=narrower), subprotocols=(V1,)
    )
    assert "windows.list" not in third.frames_of("hello.ack")[0]["accepted"]
    assert CapabilitySnapshot.objects.filter(node=node).count() == 2
    node.refresh_from_db()
    assert node.node_version.startswith("friday-node-reference")
    assert node.last_heartbeat_at is not None


def test_unknown_mismatched_and_non_read_entries_are_not_invocable(gateway, paired):
    app, harness = gateway
    node, secret, _ = paired()
    catalogue = [entry for entry in default_catalogue() if entry["class"] == "read"]
    wider = json.loads(json.dumps(catalogue[0]))
    wider["args_schema"]["properties"]["extra"] = {"type": "string", "maxLength": 8}
    unknown = dict(catalogue[1], name="foo.bar")
    newer = dict(catalogue[2], version=catalogue[2]["version"] + 1)
    gate = next(entry for entry in default_catalogue() if entry["class"] == "gate")
    hostile = [wider, unknown, newer, gate] + catalogue[3:]
    link = harness.connect(secret=secret, subprotocols=(V1,))
    link.send_frame(_hello(1, hostile))
    assert link.wait_for(lambda: link.frames_of("hello.ack"))
    accepted = set(link.frames_of("hello.ack")[0]["accepted"])
    snapshot = CapabilitySnapshot.objects.get(node=node)
    verdicts = {entry["name"]: entry for entry in snapshot.entries}
    for name in (wider["name"], "foo.bar", newer["name"], gate["name"]):
        assert not verdicts[name]["invocable"] and verdicts[name]["reason"], name
        assert name not in accepted
    assert verdicts[wider["name"]]["reason"] == "args_schema_mismatch"
    assert verdicts["foo.bar"]["reason"] == "unknown_capability"
    assert verdicts[newer["name"]]["reason"] == "version_mismatch"
    assert verdicts[gate["name"]]["reason"] == "protocol_v1"
    assert accepted == {entry["name"] for entry in catalogue[3:]}


def test_a_first_frame_that_is_not_hello_closes_protocol_error(gateway, paired):
    app, harness = gateway
    node, secret, _ = paired()
    link = harness.connect(secret=secret, subprotocols=(V1,))
    link.send_frame(_heartbeat(1))
    assert link.wait_closed() and link.close_code == 4400
    assert link.frames_of("bye")[0]["reason"] == "protocol_error"
    assert not bus.is_present(node.pk)


# --- heartbeat --------------------------------------------------------------


def test_a_heartbeat_refreshes_presence_and_is_acked(link_node):
    link, node, reference = link_node(subprotocols=(V1,))
    key = bus.presence_key(node.pk)
    bus._run(lambda c: c.expire(key, 5))
    link.node_call(lambda n: n.heartbeat())
    assert link.wait_for(lambda: link.frames_of("heartbeat.ack"))
    assert link.frames_of("heartbeat.ack")[0]["seq"] == 1
    assert bus._run(lambda c: c.ttl(key)) > 40
    assert presence.is_online(node)


def test_three_missed_heartbeats_close_the_socket_and_drop_presence(
    make_app, real_revocation, paired
):
    app, harness = make_app(idle_timeout=0.4)
    node, secret, keyring = paired()
    link = harness.connect(secret=secret, node=reference_node(node, keyring))
    assert link.frames_of("hello.ack")
    assert bus.is_present(node.pk)
    assert link.wait_closed() and link.close_code == 4408
    assert link.wait_done()
    assert not bus.is_present(node.pk)


def test_presence_expires_after_three_heartbeat_intervals():
    """The presence key's own TTL (no gateway): 45 s = 3 x 15 s."""
    assert bus.PRESENCE_TTL_SECONDS == 45


def test_revocation_is_detected_at_the_next_heartbeat_without_pubsub(
    make_app, real_revocation, owner, paired
):
    app, harness = make_app(pubsub=False)
    node, secret, keyring = paired()
    link = harness.connect(secret=secret, node=reference_node(node, keyring))
    assert link.frames_of("hello.ack")
    devices.revoke_device(device=node.device, actor=owner)
    time.sleep(0.3)
    assert link.close_code is None  # no pub/sub: still open
    link.node_call(lambda n: n.heartbeat())
    assert link.wait_closed() and link.close_code == 1000
    assert link.frames_of("bye")[-1]["reason"] == "revoked"
    reference = link.node
    assert not reference.reconnect_allowed  # bye{revoked}: no reconnect


@pytest.mark.parametrize(
    ("change", "reason"),
    [("kill_switch", "kill_switch"), ("age", "credential_expired"), ("owner", "revoked")],
)
def test_the_heartbeat_recheck_covers_kill_switch_age_and_owner(
    make_app, real_revocation, owner, paired, change, reason
):
    app, harness = make_app(pubsub=False)
    node, secret, keyring = paired()
    link = harness.connect(secret=secret, node=reference_node(node, keyring))
    assert link.frames_of("hello.ack")
    if change == "kill_switch":
        devices.engage_node_kill_switch(owner=owner, via="test")
    elif change == "age":
        TrustedDevice.objects.filter(pk=node.device_id).update(
            secret_issued_at=timezone.now() - timedelta(days=91)
        )
    else:
        type(owner).objects.filter(pk=owner.pk).update(is_active=False)
    link.node_call(lambda n: n.heartbeat())
    assert link.wait_closed()
    assert link.frames_of("bye")[-1]["reason"] == reason


def test_last_heartbeat_at_is_written_at_most_once_a_minute(paired):
    node, _, _ = paired()
    now = timezone.now()
    assert presence.record_heartbeat(node, spool_count=1, spool_bytes=10, now=now)
    assert not presence.record_heartbeat(node, spool_count=2, now=now + timedelta(seconds=59))
    assert presence.record_heartbeat(node, spool_count=3, now=now + timedelta(seconds=60))
    node.refresh_from_db()
    assert node.spool_count == 3


# --- kill switch and revocation fan-out -------------------------------------


def test_revoke_device_closes_the_socket_within_two_seconds(gateway, owner, link_node):
    app, harness = gateway
    link, node, reference = link_node()
    assert app.subscriber_ready.wait(5)
    started = time.monotonic()
    devices.revoke_device(device=Node.objects.get(pk=node.pk).device, actor=owner)
    assert link.wait_closed(timeout=2.0)
    assert link.closed_at - started < 2.0
    assert link.frames_of("bye")[-1]["reason"] == "revoked"
    assert link.wait_done()
    assert not bus.is_present(node.pk)


def test_revoke_all_devices_closes_node_sockets(gateway, owner, link_node):
    app, harness = gateway
    link, node, reference = link_node()
    assert app.subscriber_ready.wait(5)
    devices.revoke_all_devices(user=owner)
    assert link.wait_closed(timeout=2.0)
    assert link.frames_of("bye")[-1]["reason"] == "revoked"


def test_a_second_connection_replaces_the_first(gateway, paired):
    app, harness = gateway
    node, secret, keyring = paired()
    first = harness.connect(secret=secret, node=reference_node(node, keyring))
    second = harness.connect(secret=secret, node=reference_node(node, keyring))
    assert first.wait_closed()
    assert first.frames_of("bye")[-1]["reason"] == "replaced"
    assert first.wait_done()
    assert second.close_code is None and second.frames_of("hello.ack")
    assert app.connections[str(node.pk)].session.node_id == str(node.pk)
    assert len(app.connections) == 1
    # The replaced socket's teardown did not clear the live one's presence.
    assert bus.is_present(node.pk)


def test_one_owners_kill_switch_closes_only_that_owners_sockets(
    gateway, owner, paired, cleanup_nodes
):
    """Owner A and owner B each have a connected node (B's created directly
    while B held the owner role). A's kill switch closes A's socket only; a
    `"*"` payload without a user closes nothing."""
    app, harness = gateway
    other = other_user()
    type(owner).objects.filter(pk=owner.pk).update(is_owner=False)
    type(owner).objects.filter(pk=other.pk).update(is_owner=True)
    node_b, secret_b = direct_node(other, name="B")
    cleanup_nodes.append(str(node_b.pk))
    keyring = signing_keyring()
    link_b = harness.connect(secret=secret_b, node=reference_node(node_b, keyring))
    assert link_b.frames_of("hello.ack")
    assert app.subscriber_ready.wait(5)
    type(owner).objects.filter(pk=other.pk).update(is_owner=False)
    type(owner).objects.filter(pk=owner.pk).update(is_owner=True)
    owner.refresh_from_db()
    node_a, secret_a, _ = paired("A")
    link_a = harness.connect(secret=secret_a, node=reference_node(node_a, keyring))
    assert link_a.frames_of("hello.ack")

    from core import revocation

    client = revocation._client()
    try:
        client.publish(revocation.REVOCATION_CHANNEL, json.dumps({"device_id": "*"}))
        client.publish(revocation.REVOCATION_CHANNEL, "not json")
        client.publish(revocation.REVOCATION_CHANNEL, json.dumps({"device_id": "*", "user_id": ""}))
    finally:
        client.close()
    assert link_a.wait_for(lambda: app.counters["revocation_unparsed"] >= 3)
    assert link_a.close_code is None and link_b.close_code is None

    devices.engage_node_kill_switch(owner=owner, via="test")
    assert link_a.wait_closed(timeout=2.0)
    assert link_a.frames_of("bye")[-1]["reason"] == "kill_switch"
    time.sleep(0.3)
    assert link_b.close_code is None
    assert str(node_b.pk) in app.connections


# --- limits -------------------------------------------------------------------


def test_an_oversize_frame_closes_1009(link_node):
    link, node, reference = link_node(subprotocols=(V1,))
    link.send_text('{"pad":"' + "x" * (MAX_FRAME_BYTES + 1) + '"}')
    assert link.wait_closed() and link.close_code == 1009


def test_more_than_twenty_frames_a_second_closes_4429(link_node):
    link, node, reference = link_node(subprotocols=(V1,))
    for seq in range(25):
        link.send_frame(_heartbeat(1, seq))
    assert link.wait_closed() and link.close_code == 4429


def test_three_malformed_frames_close_protocol_error(link_node):
    link, node, reference = link_node(subprotocols=(V1,))
    for text in ("{not json", '{"v":1,"type":"policy.set"}', '{"v":1,"type":"heartbeat"}'):
        link.send_text(text)
    assert link.wait_closed() and link.close_code == 4400
    assert link.frames_of("bye")[-1]["reason"] == "protocol_error"


def test_an_event_is_acked_dropped_and_stored_nowhere(link_node):
    """With no sink registered (D10; the package conftest clears the
    composition root's registration) an `event` gets an `event.ack` with
    counts only, `dropped`."""
    link, node, reference = link_node(subprotocols=(V1,))
    frame = {
        "v": 1,
        "type": "event",
        "id": "6a1c2b3d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "kind": "claude_code.hook",
        "batch": [{"secret": "ghp_" + "a" * 36}],
        "spooled": False,
    }
    link.send_frame(frame)
    assert link.wait_for(lambda: link.frames_of("event.ack"))
    ack = link.frames_of("event.ack")[0]
    assert (ack["event_id"], ack["stored"], ack["dropped"]) == (frame["id"], 0, 1)
    assert "ghp_" not in json.dumps(ack)
    assert not link.frames_of("invoke")  # an event never leads to an invoke


def test_revoke_through_the_node_api_closes_the_socket(gateway, owner, link_node, web_client):
    app, harness = gateway
    link, node, reference = link_node()
    assert app.subscriber_ready.wait(5)
    response = web_client(owner).post(f"/api/v1/nodes/{node.pk}/revoke")
    assert response.status_code == 200
    assert link.wait_closed(timeout=2.0)
    assert link.frames_of("bye")[-1]["reason"] == "revoked"
