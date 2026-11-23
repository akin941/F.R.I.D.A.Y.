"""The gateway process over a real localhost socket (Phase 9 brief D3;
branch 4's done criterion): `uvicorn --ws wsproto` serving
`config.asgi_nodes.application`, the reference node's own `wsproto` client
transport (`NodeClient`), a signed read answered, and the socket cut within
2 s of `revoke_device`."""

import socket
import threading
import time

import pytest

from accounts import devices
from nodes import bus, services
from nodes.reference.transport import NodeClient
from nodes.tests.gateway_harness import reference_node

pytestmark = pytest.mark.django_db(transaction=True)


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def served(real_revocation):
    import uvicorn

    from config import asgi_nodes

    port = _free_port()
    config = uvicorn.Config(
        asgi_nodes.application,
        host="127.0.0.1",
        port=port,
        ws="wsproto",
        lifespan="on",
        log_level="warning",
        ws_max_size=256 * 1024,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    yield asgi_nodes.node_app, port
    server.should_exit = True
    thread.join(10)
    from django.db import connections

    connections.close_all()


def test_the_reference_node_over_a_real_socket(served, owner, paired):
    app, port = served
    node, secret, keyring = paired()
    reference = reference_node(node, keyring)
    client = NodeClient(reference, "127.0.0.1", port, secret=secret).start()
    try:
        deadline = time.monotonic() + 10
        # Present = `hello` processed (snapshot written, presence set).
        while not bus.is_present(node.pk) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert client.version == 2
        outcome = services.invoke_read(
            node=node,
            capability="apps.frontmost",
            args={},
            workspace=node.workspace,
            actor=owner,
        )
        assert outcome.status == "ok", outcome
        assert outcome.output["bundle_id"] == "com.apple.Terminal"
        assert app.subscriber_ready.wait(5)
        started = time.monotonic()
        devices.revoke_device(device=node.device, actor=owner)
        assert client.finished.wait(2.0)
        assert time.monotonic() - started < 2.0
        assert reference.closed_reason == "bye:revoked"
        assert not reference.reconnect_allowed
    finally:
        client.stop()
