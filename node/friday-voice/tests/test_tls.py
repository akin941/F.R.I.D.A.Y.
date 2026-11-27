"""The install's CA file (`friday_voice.tls`, brief D6, runbook B7 contract
test 6) over a real TLS socket on 127.0.0.1.

A self-signed localhost certificate shaped like deploy/docker/gen-local-tls.sh
(P-256, CA:FALSE, SAN 127.0.0.1 + localhost) is generated per test session
with the `openssl` CLI and served by a stdlib HTTPS server bound to
127.0.0.1 only. The child passes only with the CA file *and* the matching
pin; the default bundle, a wrong or missing pin, a wrong hostname and a
missing, unreadable or empty CA file all fail closed before any request
reaches the server."""

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from friday_voice import cli, tls
from friday_voice.client import CODE_CA_FILE, CODE_NETWORK, CODE_PIN, FridayError, HttpFridayClient

TOKEN = "jpat_" + "tlsTestT0kenAbcdef" * 2
MODEL = "a" * 64


def _make_cert(directory: Path, name: str, san: str):
    if shutil.which("openssl") is None:
        pytest.fail("the TLS tests need the openssl CLI")
    conf = directory / f"{name}.cnf"
    conf.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
        f"[dn]\nCN = {name}\n"
        f"[ext]\nsubjectAltName = {san}\nbasicConstraints = critical, CA:FALSE\n"
        "keyUsage = critical, digitalSignature\nextendedKeyUsage = serverAuth\n",
        encoding="ascii",
    )
    key, cert = directory / f"{name}.key.pem", directory / f"{name}.pem"
    run = dict(check=True, capture_output=True)
    subprocess.run(
        ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)],
        **run,
    )
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-key", str(key), "-out", str(cert), "-days", "2"]
        + ["-config", str(conf), "-sha256"],
        **run,
    )
    der = ssl.PEM_cert_to_DER_cert(cert.read_text("ascii"))
    return cert, key, hashlib.sha256(der).hexdigest()


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("certs")
    local = _make_cert(directory, "friday-local-tls", "IP:127.0.0.1, DNS:localhost")
    other = _make_cert(directory, "elsewhere", "DNS:elsewhere.test")
    return {"local": local, "other": other}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- http.server's name
        self.server.requests.append(self.path)
        body = json.dumps({"always_on_permitted": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve(cert, key):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.handle_error = lambda *_args: None  # a refused handshake is the test's point
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def local_server(certs):
    cert, key, pin = certs["local"]
    server = _serve(cert, key)
    yield server, cert, pin
    server.shutdown()
    server.server_close()


@pytest.fixture
def other_server(certs):
    cert, key, pin = certs["other"]
    server = _serve(cert, key)
    yield server, cert, pin
    server.shutdown()
    server.server_close()


def _policy(client, url, pin):
    client.set_credential(TOKEN, url, pin)
    try:
        return client.voice_policy(MODEL, 500, 2000)
    finally:
        client.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_the_ca_file_and_the_matching_pin_pass(local_server, host):
    server, cert, pin = local_server
    url = f"https://{host}:{server.server_address[1]}"
    reply = _policy(HttpFridayClient(ca_file=str(cert)), url, pin)
    assert reply.data == {"always_on_permitted": False}
    assert len(server.requests) == 1


def test_the_ca_file_with_a_wrong_pin_fails_before_any_request(local_server):
    server, cert, _pin = local_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=str(cert)), url, "0" * 64)
    assert (caught.value.status, caught.value.code) == (0, CODE_PIN)
    assert server.requests == []


def test_the_ca_file_without_a_pin_fails(local_server):
    server, cert, _pin = local_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=str(cert)), url, None)
    assert caught.value.code == CODE_PIN
    assert server.requests == []


def test_without_the_ca_file_the_default_bundle_refuses(local_server):
    server, _cert, pin = local_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(), url, pin)
    assert (caught.value.status, caught.value.code) == (0, CODE_NETWORK)
    assert server.requests == []


def test_hostname_verification_stays_on(other_server):
    # Trusted CA file, matching pin, but the certificate does not name 127.0.0.1.
    server, cert, pin = other_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=str(cert)), url, pin)
    assert caught.value.code == CODE_NETWORK
    assert server.requests == []


def test_another_ca_file_does_not_trust_this_server(local_server, certs):
    server, _cert, pin = local_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=str(certs["other"][0])), url, pin)
    assert caught.value.code == CODE_NETWORK  # the file replaces the bundle, it adds nothing
    assert server.requests == []


@pytest.mark.parametrize("kind", ["missing", "unreadable", "empty", "garbage"])
def test_an_unusable_ca_file_fails_closed_with_a_clear_code(local_server, tmp_path, kind):
    server, cert, pin = local_server
    if kind == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-0 file")
    path = tmp_path / f"{kind}.pem"
    if kind == "unreadable":
        shutil.copy(cert, path)
        path.chmod(0)
    elif kind == "empty":
        path.write_text("")
    elif kind == "garbage":
        path.write_text("not a certificate\n")
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=str(path)), url, pin)
    assert (caught.value.status, caught.value.code) == (0, CODE_CA_FILE)
    assert server.requests == []
    with pytest.raises(tls.TlsConfigError):
        tls.check_ca_file(path)


def test_an_unusable_install_config_refuses_every_request(local_server):
    server, _cert, pin = local_server
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_unusable=True), url, pin)
    assert caught.value.code == CODE_CA_FILE
    assert server.requests == []


# --- the install record (`friday-voice ca`) ---


def test_no_record_is_the_default_bundle(tmp_path):
    assert tls.installed_ca_file(tmp_path) is None
    assert cli.install_ca(tmp_path) == (None, False)


def test_ca_set_copies_records_and_the_child_uses_it(local_server, tmp_path, capsys):
    server, cert, pin = local_server
    assert cli.main(["ca", "set", str(cert), "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"certificate DER sha256: {pin}" in out
    copy = tmp_path / tls.CA_COPY_NAME
    assert copy.read_bytes() == cert.read_bytes()
    record = json.loads((tmp_path / tls.CONFIG_NAME).read_text("utf-8"))
    assert record == {"ca_file": str(copy.resolve())}
    ca_file, unusable = cli.install_ca(tmp_path)
    assert (ca_file, unusable) == (str(copy.resolve()), False)
    url = f"https://127.0.0.1:{server.server_address[1]}"
    reply = _policy(HttpFridayClient(ca_file=ca_file, ca_unusable=unusable), url, pin)
    assert reply.data == {"always_on_permitted": False}
    assert cli.main(["ca", "show", "--home", str(tmp_path)]) == 0
    assert pin in capsys.readouterr().out
    assert cli.main(["ca", "clear", "--home", str(tmp_path)]) == 0
    assert tls.installed_ca_file(tmp_path) is None and not copy.exists()


def test_a_recorded_ca_file_that_vanished_fails_closed(local_server, tmp_path, capsys):
    server, cert, pin = local_server
    assert cli.main(["ca", "set", str(cert), "--home", str(tmp_path)]) == 0
    (tmp_path / tls.CA_COPY_NAME).unlink()
    capsys.readouterr()
    ca_file, unusable = cli.install_ca(tmp_path)
    assert "friday-voice tls.ca_file_unreadable" in capsys.readouterr().err
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=ca_file, ca_unusable=unusable), url, pin)
    assert caught.value.code == CODE_CA_FILE  # never the default bundle instead
    assert server.requests == []


@pytest.mark.parametrize(
    "content",
    [b"{", b"[]", b'{"ca_file": "relative.pem"}', b'{"ca_file": "/x.pem", "verify": false}'],
)
def test_a_malformed_record_fails_closed(tmp_path, capsys, content):
    (tmp_path / tls.CONFIG_NAME).write_bytes(content)
    with pytest.raises(tls.TlsConfigError):
        tls.installed_ca_file(tmp_path)
    assert cli.install_ca(tmp_path) == (None, True)
    assert "friday-voice tls.config_invalid" in capsys.readouterr().err


def test_ca_set_refuses_a_private_key_and_a_missing_home(certs, tmp_path, capsys):
    cert, key, _pin = certs["local"]
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(cert.read_bytes() + key.read_bytes())
    assert cli.main(["ca", "set", str(bundle), "--home", str(tmp_path)]) == 1
    assert "private key" in capsys.readouterr().err
    assert cli.main(["ca", "set", str(cert), "--home", str(tmp_path / "absent")]) == 1
    assert not (tmp_path / tls.CONFIG_NAME).exists()


def test_the_context_never_turns_verification_off(certs):
    context = tls.load_context(str(certs["local"][0]))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    default = tls.load_context(None)
    assert default.verify_mode == ssl.CERT_REQUIRED and default.check_hostname
