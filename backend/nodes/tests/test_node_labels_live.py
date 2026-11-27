"""Phase 13 D3: relabelling a live node sends it nothing and its next
`hello` is unaffected (labels are server-side data, ADR-013 decision 5).
Transactional: the gateway harness reads the database from its own thread."""

import time

import pytest

from core.channels import CHANNEL_WEB
from nodes.services import relabel_node
from nodes.tests.gateway_harness import V1, reference_node
from workspaces import rooms

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_live_node_sees_no_frame_and_its_next_hello_is_unaffected(
    gateway, paired, owner, personal
):
    salon = rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Salon")
    app, harness = gateway
    node, secret, keyring = paired("MacBook")
    link = harness.connect(secret=secret, node=reference_node(node, keyring), subprotocols=(V1,))
    assert link.wait_for(lambda: link.frames_of("hello.ack")), (link.server_frames, link.close_code)
    (ack,) = link.frames_of("hello.ack")
    before = len(link.server_frames)
    relabel_node(
        actor=owner, node=node, channel=CHANNEL_WEB, name="Laptop", room_id=salon.pk, role="laptop"
    )
    time.sleep(0.3)
    later = [frame["type"] for frame in link.server_frames[before:]]
    assert set(later) <= {"heartbeat.ack"}, later
    assert link.close_code is None
    link.drop()
    assert link.wait_done()
    again = harness.connect(secret=secret, node=reference_node(node, keyring), subprotocols=(V1,))
    (ack2,) = again.frames_of("hello.ack")
    assert ack2["accepted"] == ack["accepted"]
    assert ack2.get("node_id") == ack.get("node_id")
    assert "Laptop" not in str(again.server_frames) and "laptop" not in str(again.server_frames)
