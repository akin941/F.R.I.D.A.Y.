"""The credential file, pairing over pinned TLS, and the VPS re-point (D10,
ADR-017 decision 7). The secret never reaches a log, an error or repr()."""

import base64
import hashlib
import io
import json
import logging
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from friday_node_linux import paths
from friday_node_linux.cli import main
from friday_node_linux.credential import (
    Credential,
    CredentialError,
    load_credential,
    save_credential,
)
from friday_node_linux.daemon import Daemon
from friday_node_linux.tls import Pins

from .conftest import SECRET

NODE_ID = "6f1e2d3c-4b5a-4987-8a6b-5c4d3e2f1a0b"


def signing_entry():
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "key_id": hashlib.sha256(public).hexdigest()[:16],
        "alg": "ed25519",
        "public_key_b64": base64.b64encode(public).decode("ascii"),
    }


def pairing_response(**overrides):
    body = {
        "node_id": NODE_ID,
        "node_secret": SECRET,
        "server_signing_keys": [signing_entry()],
        "protocol_version": 2,
        "gateway_path": "/ws/node/v1",
        "rotate_after_days": 30,
    }
    body.update(overrides)
    return body


def make_credential(tls, *, url="https://127.0.0.1:8443"):
    return Credential.from_pairing(
        pairing_response(), server_url=url, pins=Pins(ca_pem=tls.pem), now=1_796_000_000
    )


class FakeFriday:
    """A localhost HTTPS server answering `POST /api/v1/nodes/pair`."""

    def __init__(self, tls, *, status=201, body=None, routes=None):
        self.requests = []
        self.authorization = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append((self.path, json.loads(self.rfile.read(length))))
                outer.authorization.append(self.headers.get("Authorization"))
                answer_status, answer = (routes or {}).get(
                    self.path, (status, body if body is not None else pairing_response())
                )
                payload = json.dumps(answer).encode()
                self.send_response(answer_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = tls.server_context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def friday(tls):
    servers = []

    def make(cert=None, **kwargs):
        server = FakeFriday(cert or tls, **kwargs)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


# --- the file -------------------------------------------------------------------


def test_round_trip_0600(tmp_path, tls):
    path = tmp_path / "state" / "credential"
    credential = make_credential(tls)
    save_credential(path, credential)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_credential(path) == credential
    assert credential.pinned() and credential.port == 8443


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_readable_credential_is_refused(tmp_path, tls, mode):
    path = tmp_path / "credential"
    save_credential(path, make_credential(tls))
    os.chmod(path, mode)
    with pytest.raises(CredentialError, match="0600") as exc:
        load_credential(path)
    assert SECRET not in str(exc.value)


def test_a_credential_of_another_owner_is_refused(tmp_path, tls, monkeypatch):
    path = tmp_path / "credential"
    save_credential(path, make_credential(tls))
    real = os.getuid()
    monkeypatch.setattr(paths.os, "getuid", lambda: real + 1)
    with pytest.raises(CredentialError, match="another user"):
        load_credential(path)


def test_the_secret_is_never_in_repr_or_errors(tmp_path, tls):
    credential = make_credential(tls)
    assert SECRET not in repr(credential) and SECRET not in str(credential)
    body = credential.document()
    body["node_id"] = "not-a-uuid"
    path = tmp_path / "credential"
    path.write_text(json.dumps(body))
    os.chmod(path, 0o600)
    with pytest.raises(CredentialError) as exc:
        load_credential(path)
    assert SECRET not in str(exc.value)


def test_a_signing_key_whose_id_does_not_match_is_refused(tls):
    entry = signing_entry()
    entry["key_id"] = "0" * 16
    with pytest.raises(CredentialError):
        Credential.from_pairing(
            pairing_response(server_signing_keys=[entry]),
            server_url="https://friday-home.local",
            pins=Pins(ca_pem=tls.pem),
            now=1,
        )


@pytest.mark.parametrize(
    "url", ["http://friday-home.local", "https://u:p@host", "https://host/api", "ftp://x"]
)
def test_only_plain_https_server_urls(tls, url):
    with pytest.raises(CredentialError):
        Credential.from_pairing(
            pairing_response(), server_url=url, pins=Pins(ca_pem=tls.pem), now=1
        )


def test_a_node_needs_a_tls_pin():
    with pytest.raises(ValueError, match="TLS pin"):
        Pins()


def test_the_daemon_refuses_to_start_with_an_unsafe_credential(make_io, env, tls):
    path = paths.credential_path(env)
    save_credential(path, make_credential(tls))
    os.chmod(path, 0o644)
    term = make_io()
    assert main(["run"], term.io) == 1
    assert "refusing to start" in term.stderr.getvalue()
    assert SECRET not in term.stderr.getvalue()


def test_the_secret_never_appears_in_the_daemon_logs(tmp_path, tls, caplog, home):
    """A daemon that cannot reach its server: every log line at DEBUG is
    free of the secret (the connection attempt carries it in a header)."""
    caplog.set_level(logging.DEBUG)
    credential = make_credential(tls, url="https://127.0.0.1:1")  # nothing listens
    fake = {"t": 0.0}
    daemon = Daemon(
        monotonic=lambda: fake["t"],
        credential=credential,
        policy_path=tmp_path / "policy.json",
        home=home,
        spool_dir=tmp_path / "spool",
        hook_socket=tmp_path / "run" / "hook.sock",
        sleep=lambda s: fake.__setitem__("t", fake["t"] + s),
        max_sessions=3,
    )
    assert daemon.run() == 0
    assert daemon.sessions == 3
    assert caplog.records
    assert SECRET not in caplog.text
    assert credential.node_id in caplog.text


# --- pairing ----------------------------------------------------------------------


def test_pair_posts_platform_linux_and_stores_the_credential(make_io, env, friday, tls):
    server = friday()
    term = make_io()
    argv = ["pair", "--server", server.url, "--code", "CODE-1234", "--ca", str(tls.cert_path)]
    assert main([*argv, "--name", "Masaüstü"], term.io) == 0
    [(path, body)] = server.requests
    assert path == "/api/v1/nodes/pair"
    assert body == {
        "code": "CODE-1234",
        "node_name": "Masaüstü",
        "protocol_versions": [1],
        "platform": "linux",
    }
    credential = load_credential(paths.credential_path(env))
    assert credential.node_id == NODE_ID and credential.pins.ca_pem == tls.pem
    printed = term.stdout.getvalue() + term.stderr.getvalue()
    assert SECRET not in printed and "CODE-1234" not in printed
    assert f"paired: node {NODE_ID}" in printed


def test_pair_with_a_spki_pin(make_io, env, friday, tls):
    server = friday()
    pin = base64.b64encode(bytes.fromhex(tls.spki)).decode()
    argv = ["pair", "--server", server.url, "--code", "C", "--tls-pin-sha256", pin]
    assert main(argv, make_io().io) == 0
    assert load_credential(paths.credential_path(env)).pins.spki_sha256 == tls.spki


@pytest.mark.parametrize("pin_kind", ["ca", "spki"])
def test_pair_refuses_a_server_off_its_pins_before_sending_the_code(
    make_io, env, friday, tls, other_tls, pin_kind
):
    server = friday(cert=other_tls)
    pin = ["--ca", str(tls.cert_path)] if pin_kind == "ca" else ["--tls-pin-sha256", tls.spki]
    term = make_io()
    assert main(["pair", "--server", server.url, "--code", "C", *pin], term.io) == 1
    assert server.requests == []  # the code never left the node
    assert not paths.credential_path(env).exists()
    assert "refused" in term.stderr.getvalue()


def test_pair_refused_by_the_server_stores_nothing(make_io, env, friday, tls):
    server = friday(status=400, body={"code": "invalid_code", "detail": "Invalid or expired code."})
    term = make_io()
    argv = ["pair", "--server", server.url, "--code", "BAD", "--ca", str(tls.cert_path)]
    assert main(argv, term.io) == 1
    assert "HTTP 400 invalid_code" in term.stderr.getvalue()
    assert not paths.credential_path(env).exists()


def test_pair_twice_is_refused(make_io, env, friday, tls):
    save_credential(paths.credential_path(env), make_credential(tls))
    server = friday()
    argv = ["pair", "--server", server.url, "--code", "C", "--ca", str(tls.cert_path)]
    assert main(argv, make_io().io) == 1
    assert server.requests == []


def test_unpair_removes_the_local_credential(make_io, env, tls):
    save_credential(paths.credential_path(env), make_credential(tls))
    assert main(["unpair"], make_io().io) == 0
    assert not paths.credential_path(env).exists()


# --- re-point (ADR-017 decision 7) -------------------------------------------------------


def test_repoint_keeps_the_pins_and_accepts_a_server_that_passes_them(make_io, env, friday, tls):
    original = make_credential(tls)
    save_credential(paths.credential_path(env), original)
    server = friday()  # the "VPS": same certificate (same CA / key)
    term = make_io()
    assert main(["config", "set", "server-url", server.url], term.io) == 0
    moved = load_credential(paths.credential_path(env))
    assert moved.server_url == server.url
    assert moved.pins == original.pins
    assert moved.signing_keys == original.signing_keys
    assert moved.node_id == original.node_id and moved.node_secret == original.node_secret


def test_repoint_refuses_a_server_whose_pins_differ(make_io, env, friday, tls, other_tls):
    save_credential(paths.credential_path(env), make_credential(tls))
    before = paths.credential_path(env).read_bytes()
    server = friday(cert=other_tls)
    term = make_io()
    assert main(["config", "set", "server-url", server.url], term.io) == 1
    assert "does not match this node's pins" in term.stderr.getvalue()
    assert paths.credential_path(env).read_bytes() == before


def test_repoint_refuses_plain_http(make_io, env, tls):
    save_credential(paths.credential_path(env), make_credential(tls))
    term = make_io()
    assert main(["config", "set", "server-url", "http://10.0.0.5"], term.io) == 1


def test_status_never_prints_the_secret(make_io, env, tls):
    save_credential(paths.credential_path(env), make_credential(tls))
    term = make_io()
    assert main(["status"], term.io) == 0
    out = term.stdout.getvalue()
    assert NODE_ID in out and "tls pins: ca" in out
    assert SECRET not in out


def test_hook_always_exits_zero_and_prints_nothing(make_io, env):
    term = make_io()
    term.io.stdin = io.TextIOWrapper(io.BytesIO(b'{"hook_event_name": "Stop"}'))
    assert main(["hook"], term.io) == 0  # no daemon listening: the event is lost, silently
    assert term.stdout.getvalue() == "" and term.stderr.getvalue() == ""
