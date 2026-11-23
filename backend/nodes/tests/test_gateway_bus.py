"""The invoke bus end to end (Phase 9 brief D6, D7; branch 4 tests): the
reference node connected to `NodeSocketApp` in process, `invoke_read` in
the test thread (as a web or worker process would call it), real Redis and
Postgres."""

import json
import threading
import time

import pytest

from audit.models import AuditLog
from nodes import bus, services
from nodes.models import NodeInvocation
from nodes.tests.gateway_fixtures import (  # noqa: F401 - fixtures
    cleanup_nodes,
    gateway,
    link_node,
    make_app,
    paired,
    real_revocation,
)
from nodes.tests.gateway_harness import V1, V2, reference_node

pytestmark = pytest.mark.django_db(transaction=True)

FRONTMOST = {"app_name": "Terminal", "bundle_id": "com.apple.Terminal"}


def read(node_row, capability="apps.frontmost", args=None, **kwargs):
    return services.invoke_read(
        node=node_row,
        capability=capability,
        args={} if args is None else args,
        workspace=node_row.workspace,
        actor=node_row.owner,
        **kwargs,
    )


def in_thread(fn):
    box = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as exc:  # surfaced by `result`
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def result(timeout=20):
        thread.join(timeout)
        if "error" in box:
            raise box["error"]
        return box.get("value")

    return result


def received_invokes(reference):
    return [frame for frame in reference.sent if frame["type"] == "result"]


@pytest.mark.parametrize("subprotocols", [(V1,), (V2, V1)])
def test_the_done_criterion_round_trip(link_node, subprotocols):
    """Done when: the reference node connects, gets `hello.ack`, answers a
    signed `invoke` (on v1 and v2 sessions)."""
    link, node_row, reference = link_node(subprotocols=subprotocols)
    assert link.frames_of("hello.ack"), link.close_code
    ack = link.frames_of("hello.ack")[0]
    assert ack["node_id"] == str(node_row.pk) and ack["heartbeat_seconds"] == 15
    assert "apps.frontmost" in ack["accepted"]
    outcome = read(node_row)
    assert outcome.status == "ok", outcome
    assert outcome.output == FRONTMOST
    invokes = link.frames_of("invoke")
    assert len(invokes) == 1 and invokes[0]["v"] == reference.version
    # The result key is gone right after the caller's BLPOP (ephemeral).
    assert bus._run(lambda c: c.exists(bus.result_key(outcome.invocation_id))) == 0
    # One chain row and one AuditLog row, hashes only.
    row = NodeInvocation.objects.get()
    assert (row.status, row.refusal_code, row.capability) == ("ok", "", "apps.frontmost")
    assert row.nonce == invokes[0]["nonce"] and row.output_sha256 == outcome.output_sha256
    audit = AuditLog.objects.get(action="nodes.invoke")
    assert audit.metadata["status"] == "ok"
    assert "Terminal" not in json.dumps(audit.metadata)


def test_a_timeout_and_the_late_result_expires(link_node, settings):
    settings.NODE_INVOKE_TIMEOUT_SECONDS = 1
    settings.NODE_RESULT_TTL_SECONDS = 2
    link, node_row, reference = link_node(read_delay_seconds=1)
    outcome = read(node_row)
    assert outcome.status == "timeout"
    assert NodeInvocation.objects.get().nonce  # it was sent
    # The node answers late: the gateway still pushes it (the caller is
    # gone), with a TTL, and it expires unread.
    link.node_call(lambda node: node.tick())
    key = bus.result_key(outcome.invocation_id)
    assert link.wait_for(lambda: bus._run(lambda c: c.exists(key)) == 1)
    ttl = bus._run(lambda c: c.ttl(key))
    assert 0 < ttl <= 2
    assert link.wait_for(lambda: bus._run(lambda c: c.exists(key)) == 0, timeout=5)
    assert NodeInvocation.objects.count() == 1


def test_a_disconnect_mid_call_resolves_node_disconnected_without_retry(link_node):
    link, node_row, reference = link_node(read_delay_seconds=30)
    pending = in_thread(lambda: read(node_row))
    assert link.wait_for(lambda: link.frames_of("invoke"))
    link.drop()
    outcome = pending()
    assert outcome.status == "node_disconnected"
    assert len(link.frames_of("invoke")) == 1  # never retried
    assert not received_invokes(reference)  # the node sent no late result
    row = NodeInvocation.objects.get()
    assert row.status == "node_disconnected" and row.nonce


def test_max_inflight_plus_one_is_busy(link_node, settings):
    settings.NODE_MAX_INFLIGHT = 1
    link, node_row, reference = link_node(read_delay_seconds=30)
    pending = in_thread(lambda: read(node_row))
    assert link.wait_for(lambda: link.frames_of("invoke"))
    busy = read(node_row)
    assert busy.status == "busy" and busy.invocation_id == ""
    assert len(link.frames_of("invoke")) == 1  # the busy call sent nothing
    link.node_call(lambda node: (node.clock.advance(30), node.tick())[1])
    assert pending().status == "ok"
    statuses = sorted(NodeInvocation.objects.values_list("status", flat=True))
    assert statuses == ["busy", "ok"]
    assert NodeInvocation.objects.get(status="busy").nonce == ""
    assert bus.inflight_count(node_row.pk) == 0


def test_a_hostile_result_outside_the_server_schema_is_an_error(gateway, paired):
    app, harness = gateway
    node_row, secret, keyring = paired()
    reference = reference_node(
        node_row, keyring, read_outputs={"apps.frontmost": {"bundle_id": "x", "extra": "y"}}
    )
    harness.connect(secret=secret, node=reference)
    outcome = read(node_row)
    assert (outcome.status, outcome.refusal_code, outcome.output) == (
        "error",
        "result_schema",
        None,
    )


def test_a_node_refusal_is_recorded_with_its_code(gateway, paired):
    """A capability the node does not grant: the node refuses `not_granted`
    (check 16); the server records the D5 code."""
    from nodes.reference.node import default_catalogue

    app, harness = gateway
    node_row, secret, keyring = paired()
    reference = reference_node(node_row, keyring)
    harness.connect(secret=secret, node=reference)
    # The snapshot says granted; the node's own state changes afterwards.
    reference.catalogue = default_catalogue({"apps.frontmost": "not_granted"})
    outcome = read(node_row)
    assert (outcome.status, outcome.refusal_code) == ("refused", "not_granted")


def test_result_routing_is_per_socket(gateway, paired):
    """Node B answering node A's in-flight invocation is discarded (and
    counted as a frame error); A's caller gets A's own answer."""
    app, harness = gateway
    node_a, secret_a, keyring = paired("A")
    node_b, secret_b, _ = paired("B")
    ref_a = reference_node(node_a, keyring, read_delay_seconds=30)
    ref_b = reference_node(node_b, keyring)
    link_a = harness.connect(secret=secret_a, node=ref_a)
    link_b = harness.connect(secret=secret_b, node=ref_b)
    pending = in_thread(lambda: read(node_a))
    assert link_a.wait_for(lambda: link_a.frames_of("invoke"))
    invocation_id = link_a.frames_of("invoke")[0]["invocation_id"]
    forged = {
        "v": ref_b.version,
        "type": "result",
        "id": "0b6e4c1e-1d2f-4a8b-9c3d-2e1f0a9b8c7d",
        "invocation_id": invocation_id,
        "status": "ok",
        "refusal_code": None,
        "output": {"app_name": "Forged", "bundle_id": "evil"},
        "truncated": False,
        "output_sha256": "",
        "duration_ms": 0,
    }
    if ref_b.version == 2:
        forged.update(
            exit_code=None,
            output_bytes=0,
            tail=None,
            confirm_state="not_required",
            sandbox_violation=False,
        )
    link_b.send_frame(forged)
    assert link_b.wait_for(lambda: app.counters["discarded"] >= 1)
    # Nothing reached A's result key.
    assert bus._run(lambda c: c.exists(bus.result_key(invocation_id))) == 0
    link_a.node_call(lambda node: (node.clock.advance(30), node.tick())[1])
    outcome = pending()
    assert outcome.status == "ok" and outcome.output == FRONTMOST
    assert app.counters["frame_errors"] >= 1


def test_results_for_unknown_invocations_close_the_socket(link_node):
    link, node_row, reference = link_node(subprotocols=(V1,))
    for index in range(3):
        link.send_frame(
            {
                "v": 1,
                "type": "result",
                "id": f"0b6e4c1e-1d2f-4a8b-9c3d-2e1f0a9b8c7{index}",
                "invocation_id": "5d1e7f3a-2b4c-4d6e-8f0a-1b2c3d4e5f60",
                "status": "ok",
                "refusal_code": None,
                "output": {},
                "truncated": False,
                "output_sha256": "",
                "duration_ms": 0,
            }
        )
    assert link.wait_closed()
    assert link.close_code == 4400
    assert link.frames_of("bye")[-1]["reason"] == "protocol_error"


def test_a_misrouted_read_envelope_is_never_forwarded(link_node):
    """An inbox envelope naming another node: dropped, its caller resolved
    `server_refused:node_mismatch` through the result key."""
    link, node_row, reference = link_node(subprotocols=(V1,))
    other_node_id = "7f1e2d3c-4b5a-4968-8776-655443322110"
    envelope = services.sign_read_invoke(
        node=type("N", (), {"pk": other_node_id})(),
        workspace_id=node_row.workspace_id,
        capability="apps.frontmost",
        args={},
        trace_id="t",
    )
    bus.push_invoke(node_row.pk, envelope)
    key = bus.result_key(envelope["invocation_id"])
    assert link.wait_for(lambda: bus._run(lambda c: c.exists(key)) == 1)
    payload = bus.wait_result(envelope["invocation_id"], 1)
    assert payload == {"verdict": "node_mismatch"}
    assert not link.frames_of("invoke")


def test_logs_carry_no_frame_bodies(link_node, caplog):
    caplog.set_level("DEBUG", logger="nodes")
    link, node_row, reference = link_node()
    outcome = read(node_row)
    assert outcome.status == "ok"
    text = caplog.text
    envelope = link.frames_of("invoke")[0]
    # No output, no args, no nonce, no signature: ids, types, sizes, codes.
    assert "Terminal" not in text
    assert envelope["nonce"] not in text and envelope["sig"] not in text
    assert "node frame out" in text and "type=invoke" in text


def test_invoke_read_round_trip_latency_is_bounded(link_node):
    link, node_row, reference = link_node()
    started = time.monotonic()
    assert read(node_row).status == "ok"
    assert time.monotonic() - started < 3
