"""`manage.py preflight` node checks (Phase 9 closeout): `node_gateway`,
`node_signing_key`, `node_link`, `node_tls_pin`, `node_audit_chain` and
`node_conformance` -- each passes, fails and warns with fakes. Every
outside contact is a seam (`http_get` for the gateway's /healthz,
`worker_signing_key_id` for the worker, `served_spki_sha256` for the TLS
pin, `node_conformance_drift` for the case files): no test reaches a
gateway, a worker, a proxy or a Mac."""

import base64
import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from ai_core.models import ModelUsage
from nodes import bus, tasks
from nodes.audit_chain import append
from nodes.keys import published_test_key_ids, signing_key_id
from nodes.models import CapabilitySnapshot, Node, NodeInvocation
from nodes.protocol import sha256_hex
from nodes.protocol.signing import SigningKey
from nodes.services import pair_node
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
GATEWAY_URL = "http://node-gateway:8002/healthz"
PIN = bytes(range(32))
OTHER_PIN = bytes(range(1, 33))


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def paired(owner):
    """Pair nodes through the service; their Redis keys are cleared after."""
    made = []

    def make(name="MacBook"):
        raw_code, _ = devices.issue_pairing_code(
            owner=owner,
            kind=DevicePairingCode.Kind.STRONG,
            issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            device_kind=TrustedDevice.Kind.NODE,
        )
        node = pair_node(raw_code=raw_code, node_name=name, protocol_versions=[1]).node
        made.append(node)
        return node

    yield make
    for node in made:
        bus.clear_presence(node.pk)


@pytest.fixture
def seams(monkeypatch, settings):
    """A healthy stack: the gateway and a worker report web's key id, the
    served certificate matches the pin, the case files are current."""
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.PREFLIGHT_NODE_GATEWAY_URL = GATEWAY_URL
    state = {
        "healthz": (
            200,
            {
                "status": "ok",
                "redis": True,
                "connected_nodes": 0,
                "signing_key_id": signing_key_id(),
            },
        ),
        "worker": signing_key_id(),
        "spki": PIN,
        "drift": [],
        "calls": [],
    }

    def http_get(url, *, timeout):
        state["calls"].append(("http", url))
        value = state["healthz"]
        if isinstance(value, Exception):
            raise value
        status, body = value
        return status, body if isinstance(body, str) else json.dumps(body)

    def worker(*, timeout):
        state["calls"].append(("worker", timeout))
        value = state["worker"]
        if isinstance(value, Exception):
            raise value
        return value

    def spki(host, port, *, timeout):
        state["calls"].append(("tls", host, port))
        value = state["spki"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(preflight, "http_get", http_get)
    monkeypatch.setattr(preflight, "worker_signing_key_id", worker)
    monkeypatch.setattr(preflight, "served_spki_sha256", spki)
    monkeypatch.setattr(preflight, "node_conformance_drift", lambda: list(state["drift"]))
    return state


def _nodes():
    return {r.name: r for r in preflight.check_nodes()}


def _healthz(state, **changes):
    status, body = state["healthz"]
    state["healthz"] = (status, {**body, **changes})


def _connect(node, *, heartbeat_age=10, entries=None):
    """The node as the gateway leaves it after `hello`: a presence key, a
    heartbeat row and a capability snapshot."""
    bus.mark_present(node.pk)
    Node.objects.filter(pk=node.pk).update(
        last_heartbeat_at=timezone.now() - timedelta(seconds=heartbeat_age)
    )
    CapabilitySnapshot.objects.create(
        workspace=node.workspace,
        node=node,
        catalogue_sha256="ab" * 32,
        entries=entries
        if entries is not None
        else [
            {"name": "apps.frontmost", "version": 1, "class": "read", "invocable": True},
            {"name": "git.status", "version": 1, "class": "read", "invocable": True},
            {"name": "shell.run", "version": 1, "class": "gated", "invocable": False},
        ],
    )


# --- node_gateway --------------------------------------------------------------


def test_gateway_passes_with_redis_up(seams):
    _healthz(seams, connected_nodes=1)
    result = _nodes()["node_gateway"]
    assert (result.status, result.detail) == (PASS, "Redis OK, 1 node(s) connected")
    assert ("http", GATEWAY_URL) in seams["calls"]


def test_gateway_unreachable_fails(seams):
    seams["healthz"] = ConnectionError("refused: http://user:pw@node-gateway")
    result = _nodes()["node_gateway"]
    assert result.status == FAIL
    assert result.detail == "unreachable: ConnectionError"
    assert "pw" not in result.detail


@pytest.mark.parametrize(
    "healthz, detail",
    [
        ((503, {"status": "ok"}), "HTTP 503"),
        ((200, "<html>proxy</html>"), "unexpected /healthz body"),
        ((200, {"status": "ok", "redis": False, "signing_key_id": None}), "Redis down"),
    ],
)
def test_gateway_unhealthy_fails(seams, healthz, detail):
    seams["healthz"] = healthz
    result = _nodes()["node_gateway"]
    assert result.status == FAIL and detail in result.detail


def test_gateway_not_configured_warns(seams, settings):
    settings.PREFLIGHT_NODE_GATEWAY_URL = ""
    results = _nodes()
    assert (results["node_gateway"].status, results["node_gateway"].detail) == (
        WARN,
        "PREFLIGHT_NODE_GATEWAY_URL not set",
    )
    # The gateway's key id could not be compared: a warning, not a pass.
    assert results["node_signing_key"].status == WARN
    assert "node-gateway key id not compared" in results["node_signing_key"].detail
    assert not [c for c in seams["calls"] if c[0] == "http"]


# --- node_signing_key -----------------------------------------------------------


def test_signing_key_passes_when_web_worker_and_gateway_agree(seams, paired):
    paired()
    result = _nodes()["node_signing_key"]
    assert result.status == PASS
    assert result.detail == f"key id {signing_key_id()} in web, worker and node-gateway"


def test_signing_key_missing_in_web_fails(seams, settings):
    settings.NODE_SIGNING_KEY = ""
    result = _nodes()["node_signing_key"]
    assert (result.status, result.detail) == (FAIL, "NODE_SIGNING_KEY is not set in web")


def test_signing_key_invalid_in_web_fails(seams, settings):
    settings.NODE_SIGNING_KEY = "not-a-key"
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL and "not a valid Ed25519 key" in result.detail
    assert "not-a-key" not in result.detail


def test_the_conformance_key_configured_fails(seams, settings):
    from nodes.keys import _CONFORMANCE_KEYS

    document = json.loads(_CONFORMANCE_KEYS.read_text(encoding="utf-8"))
    conformance = document["signing"]
    assert conformance["key_id"] in published_test_key_ids()
    settings.NODE_SIGNING_KEY = conformance["private_key_b64"]
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL
    assert result.detail == "NODE_SIGNING_KEY in web is a published test or conformance key"
    assert conformance["private_key_b64"] not in result.detail


def test_signing_key_differing_in_the_gateway_fails(seams):
    _healthz(seams, signing_key_id="0123456789abcdef")
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL
    assert "node-gateway signs with another key than web" in result.detail


def test_signing_key_missing_in_the_gateway_fails(seams):
    _healthz(seams, signing_key_id=None)
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL and "no usable key in node-gateway" in result.detail


def test_signing_key_differing_in_the_worker_fails(seams):
    seams["worker"] = "fedcba9876543210"
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL
    assert "the worker signs with another key than web" in result.detail


def test_signing_key_missing_in_the_worker_fails(seams):
    seams["worker"] = None
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL and "no usable key in the worker" in result.detail


def test_no_worker_answering_fails(seams):
    seams["worker"] = TimeoutError("no reply from redis://:secret@redis")
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL
    assert "no worker answered nodes.signing_key_id (TimeoutError)" in result.detail
    assert "secret" not in result.detail


def test_eager_celery_skips_the_worker_with_a_warning(seams, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    result = _nodes()["node_signing_key"]
    assert result.status == WARN and "CELERY_TASK_ALWAYS_EAGER" in result.detail
    assert not [c for c in seams["calls"] if c[0] == "worker"]


def test_a_node_pinned_to_another_key_fails(seams, paired, settings):
    node = paired()
    # The owner rolled the key without re-pairing: web, worker and the
    # gateway agree on the new key, the paired node still pins the old one.
    new_key = SigningKey.generate()
    settings.NODE_SIGNING_KEY = new_key.private_b64()
    _healthz(seams, signing_key_id=new_key.key_id)
    seams["worker"] = new_key.key_id
    assert node.pinned_key_id != new_key.key_id
    result = _nodes()["node_signing_key"]
    assert result.status == FAIL
    assert "1 active node(s) pinned another key id: re-pair them" in result.detail


def test_a_revoked_node_pinned_to_an_old_key_is_ignored(seams, paired, owner, settings):
    node = paired()
    devices.revoke_device(device=node.device, actor=owner)
    new_key = SigningKey.generate()
    settings.NODE_SIGNING_KEY = new_key.private_b64()
    _healthz(seams, signing_key_id=new_key.key_id)
    seams["worker"] = new_key.key_id
    assert _nodes()["node_signing_key"].status == PASS


def test_the_signing_key_id_task_returns_the_id_only(settings):
    assert tasks.signing_key_id_task.name == "nodes.signing_key_id"
    assert tasks.signing_key_id_task.run() == signing_key_id()
    settings.NODE_SIGNING_KEY = ""
    assert tasks.signing_key_id_task.run() is None


def test_the_worker_seam_sends_the_task_by_name(monkeypatch):
    sent = []

    class Result:
        def get(self, timeout):
            sent.append(timeout)
            return "0123456789abcdef"

    from celery import current_app

    monkeypatch.setattr(current_app, "send_task", lambda name: sent.append(name) or Result())
    assert preflight.worker_signing_key_id(timeout=3) == "0123456789abcdef"
    assert sent == ["nodes.signing_key_id", 3]


# --- node_link ------------------------------------------------------------------


def test_no_node_paired_warns_and_does_not_fail(seams):
    result = _nodes()["node_link"]
    assert result.status == WARN
    assert result.detail == "no node paired (Mac session pending owner evaluation)"


def test_a_connected_node_passes_with_its_counts(seams, paired):
    node = paired()
    _connect(node)
    result = _nodes()["node_link:MacBook"]
    assert result.status == PASS
    assert result.detail.startswith("connected, heartbeat ")
    assert "protocol v1" in result.detail
    assert f"catalogue {'ab' * 6}" in result.detail
    assert result.detail.endswith("2/3 invocable")


def test_a_disconnected_node_warns(seams, paired):
    paired()
    result = _nodes()["node_link:MacBook"]
    assert result.status == WARN
    assert result.detail.startswith("disconnected, no heartbeat yet")
    assert "no catalogue" in result.detail


def test_a_stale_heartbeat_row_warns(seams, paired):
    node = paired()
    _connect(node, heartbeat_age=preflight.NODE_HEARTBEAT_STALE_SECONDS + 60)
    result = _nodes()["node_link:MacBook"]
    assert result.status == WARN and "stale heartbeat row" in result.detail


def test_a_killed_node_warns_and_a_revoked_one_is_not_listed(seams, paired, owner):
    kept = paired("MacBook")
    gone = paired("Old Mac")
    devices.revoke_device(device=gone.device, actor=owner)
    _connect(kept)
    devices.engage_node_kill_switch(owner=owner, via="web")
    results = _nodes()
    assert results["node_link:MacBook"].status == WARN
    assert results["node_link:MacBook"].detail.startswith("kill_switch")
    assert "node_link:Old Mac" not in results


# --- node_tls_pin ---------------------------------------------------------------


def test_tls_pin_not_set_warns(seams):
    result = _nodes()["node_tls_pin"]
    assert (result.status, result.detail) == (WARN, "NODE_TLS_PIN_SHA256 not set")


@pytest.mark.parametrize(
    "pin",
    [base64.b64encode(PIN).decode(), PIN.hex(), "sha256/" + base64.b64encode(PIN).decode()],
)
def test_tls_pin_matching_passes(seams, settings, pin):
    settings.NODE_TLS_PIN_SHA256 = pin
    settings.PREFLIGHT_NODE_TLS_URL = "wss://friday.example.net:8443/ws/node/v1"
    result = _nodes()["node_tls_pin"]
    assert (result.status, result.detail) == (PASS, "served SPKI matches the pin")
    assert ("tls", "friday.example.net", 8443) in seams["calls"]


def test_tls_pin_mismatched_fails(seams, settings):
    settings.NODE_TLS_PIN_SHA256 = base64.b64encode(OTHER_PIN).decode()
    settings.PREFLIGHT_NODE_TLS_URL = "https://friday.example.net/ws/node/v1"
    result = _nodes()["node_tls_pin"]
    assert result.status == FAIL
    assert "does not match NODE_TLS_PIN_SHA256" in result.detail
    assert ("tls", "friday.example.net", 443) in seams["calls"]


def test_tls_pin_handshake_failure_fails(seams, settings):
    settings.NODE_TLS_PIN_SHA256 = PIN.hex()
    settings.PREFLIGHT_NODE_TLS_URL = "https://friday.example.net/ws/node/v1"
    seams["spki"] = OSError("connection refused")
    result = _nodes()["node_tls_pin"]
    assert (result.status, result.detail) == (FAIL, "TLS handshake failed: OSError")


@pytest.mark.parametrize(
    "pin, url, status",
    [
        ("not a pin", "https://friday.example.net", FAIL),
        (base64.b64encode(b"short").decode(), "https://friday.example.net", FAIL),
        (PIN.hex(), "", WARN),
        (PIN.hex(), "http://friday.example.net", FAIL),
        (PIN.hex(), "https://friday.example.net:notaport", FAIL),
    ],
)
def test_tls_pin_misconfiguration(seams, settings, pin, url, status):
    settings.NODE_TLS_PIN_SHA256 = pin
    settings.PREFLIGHT_NODE_TLS_URL = url
    result = _nodes()["node_tls_pin"]
    assert result.status == status
    assert not [c for c in seams["calls"] if c[0] == "tls"]


def test_the_spki_seam_hashes_the_served_public_key(monkeypatch):
    """The real seam over a fake TLS socket: sha256 of the DER SPKI of the
    certificate the peer presents."""
    import hashlib
    import socket
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "friday.example.net")])
    now = timezone.now()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    seen = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def getpeercert(self, binary_form=False):
            assert binary_form
            return der

    def create_connection(address, timeout):
        seen["address"] = (address, timeout)
        return FakeSocket()

    def wrap_socket(self, sock, server_hostname):
        seen["sni"] = server_hostname
        return sock

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)
    digest = preflight.served_spki_sha256("friday.example.net", 443, timeout=2)
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert digest == hashlib.sha256(spki).digest()
    assert seen == {"address": (("friday.example.net", 443), 2), "sni": "friday.example.net"}


# --- node_audit_chain -----------------------------------------------------------


def _chain_row(node):
    return append(
        workspace=node.workspace,
        node=node,
        capability="git.status",
        capability_version=1,
        args_sha256=sha256_hex({"repo_path": "~/work/acme"}),
        status=NodeInvocation.Status.OK,
        channel="web",
        trace_id="trace-1",
        actor=None,
        nonce="A" * 43,
        output_sha256=sha256_hex({"branch": "main"}),
        output_bytes=17,
        duration_ms=12,
    )


def test_an_intact_chain_passes(seams, paired):
    node = paired()
    for _ in range(3):
        _chain_row(node)
    result = _nodes()["node_audit_chain"]
    assert (result.status, result.detail) == (PASS, "3 row(s) intact")


def test_a_broken_chain_fails_with_its_seq(seams, paired):
    node = paired()
    rows = [_chain_row(node) for _ in range(3)]
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE nodes_nodeinvocation DISABLE TRIGGER USER")
        try:
            cursor.execute(
                "UPDATE nodes_nodeinvocation SET status = 'refused' WHERE id = %s", [rows[1].pk]
            )
        finally:
            cursor.execute("ALTER TABLE nodes_nodeinvocation ENABLE TRIGGER USER")
    result = _nodes()["node_audit_chain"]
    assert result.status == FAIL
    assert result.detail == "broken at seq 2 (row_hash_mismatch); 1 row(s) verified before it"


# --- node_conformance -----------------------------------------------------------


def test_current_case_files_pass(seams):
    result = _nodes()["node_conformance"]
    assert (result.status, result.detail) == (PASS, "case and vector files current")


def test_stale_case_files_fail(seams):
    seams["drift"] = ["cases/v1/v1_read_git_status.json", "vectors/scrub.json"]
    result = _nodes()["node_conformance"]
    assert result.status == FAIL
    assert result.detail == (
        "stale: cases/v1/v1_read_git_status.json, vectors/scrub.json (run build_node_conformance)"
    )


def test_the_conformance_seam_runs_the_real_check():
    """The committed case files of this checkout are current (the same
    check CI runs as `build_node_conformance --check`)."""
    assert preflight.node_conformance_drift() == []


def test_the_conformance_seam_reports_a_stale_vendored_scrubber(monkeypatch):
    from nodes.conformance import build
    from nodes.reference import vendor_scrubber

    monkeypatch.setattr(build, "check", lambda: ["cases/v1/x.json"])
    monkeypatch.setattr(vendor_scrubber, "main", lambda argv: 1)
    assert preflight.node_conformance_drift() == [
        "cases/v1/x.json",
        "reference/_scrubber_vendored.py",
    ]


# --- wiring ---------------------------------------------------------------------


def test_one_crashing_node_check_does_not_hide_the_others(seams, monkeypatch):
    def boom():
        raise RuntimeError("chain table missing")

    monkeypatch.setattr(preflight, "_check_node_audit_chain", boom)
    results = _nodes()
    assert results["node_audit_chain"].status == FAIL
    assert results["node_audit_chain"].detail == "check crashed: RuntimeError"
    assert results["node_conformance"].status == PASS


def test_node_checks_are_registered_read_only_and_make_no_model_call(seams, paired):
    node = paired()
    _connect(node)
    _chain_row(node)
    before = NodeInvocation.objects.count()
    assert ("nodes", preflight.check_nodes) in preflight.CHECKS
    results = preflight.run_preflight(checks=(("nodes", preflight.check_nodes),))
    assert [r.name for r in results] == [
        "node_gateway",
        "node_signing_key",
        "node_link:MacBook",
        "node_tls_pin",
        "node_audit_chain",
        "node_conformance",
    ]
    assert not ModelUsage.objects.exists()
    # Nothing sent to the node, nothing written to the chain.
    assert NodeInvocation.objects.count() == before
    assert bus.drain_inbox(node.pk) == []


def test_the_command_prints_the_node_lines_and_never_key_material(seams, paired, settings):
    paired()
    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    out = StringIO()
    try:
        call_command("preflight", "--no-model-calls", stdout=out)
    except SystemExit:
        pass  # other checks may fail in the test environment
    text = out.getvalue()
    lines = text.splitlines()
    assert "PASS  node_gateway  Redis OK, 0 node(s) connected" in lines
    assert any(line.startswith("WARN  node_link:MacBook  disconnected") for line in lines)
    assert "WARN  node_tls_pin  NODE_TLS_PIN_SHA256 not set" in lines
    assert "PASS  node_audit_chain  0 row(s) intact" in lines
    assert settings.NODE_SIGNING_KEY not in text
