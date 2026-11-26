"""Phase 13 branch 3 "Done when": two reference nodes paired through the
service and connected to the gateway show as two presence rows with the
right live status; one dropping flips only its own row. Transactional: the
gateway harness reads the database from its own thread."""

import pytest

from nodes.selectors import node_presence
from nodes.tests.gateway_harness import V1, reference_node

pytestmark = pytest.mark.django_db(transaction=True)


def _status(owner):
    return {entry["name"]: (entry["status"], entry["online"]) for entry in node_presence(owner)}


def test_two_live_reference_nodes_are_two_rows_with_live_status(gateway, paired, owner):
    _, harness = gateway
    mac, mac_secret, keyring = paired("MacBook")
    desk, desk_secret, _ = paired("Masaüstü")
    assert _status(owner) == {
        "MacBook": ("disconnected", False),
        "Masaüstü": ("disconnected", False),
    }
    mac_link = harness.connect(
        secret=mac_secret, node=reference_node(mac, keyring), subprotocols=(V1,)
    )
    desk_link = harness.connect(
        secret=desk_secret, node=reference_node(desk, keyring), subprotocols=(V1,)
    )
    for link in (mac_link, desk_link):
        assert link.frames_of("hello.ack"), (link.server_frames, link.close_code)
    assert _status(owner) == {"MacBook": ("connected", True), "Masaüstü": ("connected", True)}
    rows = node_presence(owner)
    assert all(row["invocable_read_count"] > 0 for row in rows)

    desk_link.drop()
    assert desk_link.wait_done()
    assert _status(owner) == {"MacBook": ("connected", True), "Masaüstü": ("disconnected", False)}
    # Connected first.
    assert [row["name"] for row in node_presence(owner)] == ["MacBook", "Masaüstü"]
