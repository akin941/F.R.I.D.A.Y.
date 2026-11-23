"""Phase 9 closeout, done criterion "preflight is green with the reference
node paired", in process: `manage.py run_reference_node` pairs the Python
reference node with a node code and keeps it connected to the real gateway
process (`uvicorn --ws wsproto` serving `config.asgi_nodes`, as in
`test_node_gateway_process.py`); the preflight node checks then read the
gateway's real `/healthz` and pass. Only the worker seam is faked (no Celery
worker runs in tests) and the TLS pin is not configured (`warn`). It lives
under `config/tests` because it serves the composition root."""

import threading
import time
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connections

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from config.tests.test_node_gateway_process import served  # noqa: F401 -- fixture
from nodes import bus
from nodes.keys import signing_key_id
from nodes.models import Node
from ops import preflight

pytestmark = pytest.mark.django_db(transaction=True)

BAD_CODE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    user.is_owner = True
    user.save()
    return user


def _node_code(owner):
    raw, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    return raw


def test_preflight_is_green_with_the_reference_node_paired(
    served,  # noqa: F811 -- the fixture
    owner,
    settings,
    monkeypatch,
):
    app, port = served
    settings.DEBUG = True
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.PREFLIGHT_NODE_GATEWAY_URL = f"http://127.0.0.1:{port}/healthz"
    monkeypatch.setattr(preflight, "worker_signing_key_id", lambda *, timeout: signing_key_id())
    raw = _node_code(owner)
    out = StringIO()
    box = {}

    def run():
        try:
            call_command(
                "run_reference_node",
                code=raw,
                gateway_host="127.0.0.1",
                gateway_port=port,
                seconds=10,
                stdout=out,
            )
        except Exception as exc:  # surfaced below
            box["error"] = exc
        finally:
            connections.close_all()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    node = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            node = Node.objects.filter(owner=owner).first()
            if node is not None and bus.is_present(node.pk):
                break
            time.sleep(0.05)
        assert node is not None and bus.is_present(node.pk), (out.getvalue(), box)
        results = {r.name: r for r in preflight.check_nodes()}
        statuses = {name: r.status for name, r in results.items()}
        assert statuses == {
            "node_gateway": preflight.PASS,
            "node_signing_key": preflight.PASS,
            "node_link:Reference node": preflight.PASS,
            "node_tls_pin": preflight.WARN,
            "node_audit_chain": preflight.PASS,
            "node_conformance": preflight.PASS,
        }, results
        assert results["node_gateway"].detail == "Redis OK, 1 node(s) connected"
        # Phase 10 closeout: the reference node speaks v2 with its gate- and
        # confirm-class contracts invocable, and the real gateway process
        # reports its output scrubber.
        control = {r.name: r for r in preflight.check_control()}
        protocol = control["node_protocol_v2:Reference node"]
        assert protocol.status == preflight.PASS, protocol
        assert "protocol v2" in protocol.detail
        assert "node_protocol_v2" not in control  # a node speaks v2
        assert control["node_output_scrubber"].status == preflight.PASS, control
        assert f"paired reference node {node.pk}" in out.getvalue()
        assert "jnd_" not in out.getvalue()  # the secret is never printed
    finally:
        if node is not None:
            # The revocation closes the node's socket (bye{revoked}); the
            # command then returns.
            devices.revoke_device(device=Node.objects.get(pk=node.pk).device, actor=owner)
        thread.join(15)
        if node is not None:
            bus.clear_presence(node.pk)
            bus.clear_inbox(node.pk)
    assert not thread.is_alive()
    assert "error" not in box, box


def test_run_reference_node_is_refused_without_debug(owner, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("run_reference_node", code=_node_code(owner))
    assert not Node.objects.exists()


def test_run_reference_node_with_a_bad_code_pairs_nothing(owner, settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="pairing refused"):
        call_command("run_reference_node", code=BAD_CODE)
    assert not Node.objects.exists()


@pytest.mark.parametrize(("confirm", "expected"), [("none", None), ("approve", "approve")])
def test_the_reference_nodes_window_is_scripted_by_confirm(owner, tmp_path, confirm, expected):
    """Phase 10 closeout: `--confirm none` lets every window time out (the
    owner is away); `approve`/`deny` script the owner one second after the
    window opens. Actions only ever reach the fake executor."""
    from nodes.management.commands.run_reference_node import build_node
    from nodes.reference.executor import FakeExecutor
    from nodes.reference.fixtures.factory import build_fixture_home
    from nodes.services import pair_node

    result = pair_node(raw_code=_node_code(owner), node_name="Ref", protocol_versions=[1])
    node = build_node(result, build_fixture_home(tmp_path), confirm=confirm)
    assert isinstance(node.executor, FakeExecutor)
    script = node.window.script
    if expected is None:
        assert script == []
    else:
        assert script and {(d.action, d.after) for d in script} == {(expected, 1)}
