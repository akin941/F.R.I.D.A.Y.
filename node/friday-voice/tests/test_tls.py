"""The install's CA file (`friday_voice.tls`, brief D6, runbook B7 contract
test 6) over a real TLS socket on 127.0.0.1.

A self-signed localhost certificate shaped like deploy/docker/gen-local-tls.sh
(P-256, CA:FALSE, SAN 127.0.0.1 + localhost) is generated per test session
with the `openssl` CLI and served by a stdlib HTTPS server bound to
127.0.0.1 only. The child passes only with the CA file *and* the matching
pin; the default bundle, a wrong or missing pin, a wrong hostname and a
missing, unreadable or empty CA file all fail closed before any request
reaches the server."""

import base64
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


def _make_cert(directory: Path, name: str, san: str, *, ca=False):
    """`ca`: False (CA:FALSE), True (CA:TRUE) or None (no basicConstraints)."""
    if shutil.which("openssl") is None:
        pytest.fail("the TLS tests need the openssl CLI")
    conf = directory / f"{name}.cnf"
    usage = "keyCertSign, cRLSign, digitalSignature" if ca else "digitalSignature"
    constraint = "" if ca is None else f"basicConstraints = critical, CA:{str(ca).upper()}\n"
    conf.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
        f"[dn]\nCN = {name}\n"
        f"[ext]\nsubjectAltName = {san}\n{constraint}"
        f"keyUsage = critical, {usage}\nextendedKeyUsage = serverAuth\n",
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
    ca = _make_cert(directory, "a-ca", "DNS:a-ca.test", ca=True)
    bare = _make_cert(directory, "no-constraints", "DNS:bare.test", ca=None)
    return {"local": local, "other": other, "ca": ca, "bare": bare}


def _spki_b64(cert):
    """The node pairing pin exactly as deploy/docker/gen-local-tls.sh computes it."""
    pubkey = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"], check=True, capture_output=True
    ).stdout
    der = subprocess.run(
        ["openssl", "pkey", "-pubin", "-outform", "der"],
        input=pubkey,
        check=True,
        capture_output=True,
    ).stdout
    return base64.b64encode(hashlib.sha256(der).digest()).decode()


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
    tmp_path.chmod(0o755)
    assert cli.main(["ca", "set", str(cert), "--home", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert f"voice pin (credential ca_sha256, DER sha256 hex): {pin}" in captured.out
    spki = _spki_b64(cert)
    assert (
        f"node pairing pin (SPKI sha256 base64, NODE_TLS_PIN_SHA256; not the voice pin): {spki}"
        in captured.out
    )
    assert captured.err == ""  # one non-CA certificate: no warning
    copy = tmp_path / tls.CA_COPY_NAME
    # 0600 files in a 0700 home; the child (same user) still reads them.
    assert (tmp_path.stat().st_mode & 0o777) == 0o700
    assert (copy.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / tls.CONFIG_NAME).stat().st_mode & 0o777) == 0o600
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
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


@pytest.mark.parametrize("name", [tls.CA_COPY_NAME, tls.CONFIG_NAME])
def test_ca_set_refuses_a_symlink_in_the_home(certs, tmp_path, capsys, name):
    cert = certs["local"][0]
    home = tmp_path / "home"
    home.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (home / name).symlink_to(victim)
    assert cli.main(["ca", "set", str(cert), "--home", str(home)]) == 1
    assert "symlink" in capsys.readouterr().err
    assert victim.read_text() == "untouched"
    assert not (home / tls.CONFIG_NAME).exists() or (home / tls.CONFIG_NAME).is_symlink()


def test_the_write_never_follows_a_planted_temporary(tmp_path, monkeypatch):
    """The temporary is O_EXCL|O_NOFOLLOW: a link already at its name fails
    the write instead of being written through."""
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    target = tmp_path / tls.CONFIG_NAME
    monkeypatch.setattr(tls.secrets, "token_hex", lambda _n: "fixed")
    (tmp_path / f".{tls.CONFIG_NAME}.fixed.tmp").symlink_to(victim)
    with pytest.raises(OSError):
        tls._write_atomic(target, b"{}")
    assert victim.read_text() == "untouched" and not target.exists()


@pytest.mark.parametrize(
    ("kind", "label"), [("ca", "CA:TRUE"), ("bare", "no basicConstraints: possibly a CA")]
)
def test_a_possible_ca_certificate_needs_allow_ca(certs, tmp_path, capsys, kind, label):
    cert = certs[kind][0]
    (info,) = tls.check_ca_file(cert)
    assert info.is_ca and info.ca_label == label
    assert cli.main(["ca", "set", str(cert), "--home", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "--allow-ca" in err and label in err
    assert not (tmp_path / tls.CONFIG_NAME).exists()
    assert cli.main(["ca", "set", str(cert), "--allow-ca", "--home", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert f"certificate 1 is treated as a CA ({label})" in captured.err
    assert f"({label})" in captured.out


def test_the_leaf_is_ca_false(certs):
    (info,) = tls.check_ca_file(certs["local"][0])
    assert info.ca_constraint is False and not info.is_ca and info.ca_label == "CA:FALSE"


# --- which directory `ca set` may write into (tls.check_home) ---


def test_ca_set_refuses_a_symlinked_home(certs, tmp_path, capsys):
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert cli.main(["ca", "set", str(certs["local"][0]), "--home", str(link)]) == 1
    assert "symlink" in capsys.readouterr().err
    assert (real.stat().st_mode & 0o777) == 0o755 and not list(real.iterdir())


def test_ca_set_refuses_the_users_home(certs, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tls.Path, "home", classmethod(lambda cls: tmp_path))
    tmp_path.chmod(0o755)
    assert cli.main(["ca", "set", str(certs["local"][0]), "--home", str(tmp_path)]) == 1
    assert "home directory" in capsys.readouterr().err
    assert (tmp_path.stat().st_mode & 0o777) == 0o755


def test_ca_set_refuses_a_home_owned_by_someone_else(certs, tmp_path, monkeypatch, capsys):
    other_uid = os.getuid() + 1
    monkeypatch.setattr(tls.os, "getuid", lambda: other_uid)
    assert cli.main(["ca", "set", str(certs["local"][0]), "--home", str(tmp_path)]) == 1
    assert "not owned by this user" in capsys.readouterr().err


def test_ca_set_refuses_a_foreign_directory(certs, tmp_path, capsys):
    (tmp_path / "notes.txt").write_text("someone else's")
    tmp_path.chmod(0o755)
    assert cli.main(["ca", "set", str(certs["local"][0]), "--home", str(tmp_path)]) == 1
    assert "neither a friday-voice install" in capsys.readouterr().err
    assert (tmp_path.stat().st_mode & 0o777) == 0o755
    assert not (tmp_path / tls.CONFIG_NAME).exists()


def test_ca_set_accepts_a_friday_voice_install(certs, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "friday-voice"\n')
    (tmp_path / "friday_voice").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "uv.lock").write_text("")
    tls.set_ca(certs["local"][0], tmp_path)
    assert tls.installed_ca_file(tmp_path) == str((tmp_path / tls.CA_COPY_NAME).resolve())
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
    with pytest.raises(tls.TlsConfigError):
        tls.set_ca(certs["local"][0], tmp_path)


def test_a_bundle_of_several_certificates_warns(certs, tmp_path, capsys):
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(certs["local"][0].read_bytes() + certs["other"][0].read_bytes())
    home = tmp_path / "home"
    home.mkdir()
    assert cli.main(["ca", "set", str(bundle), "--home", str(home)]) == 0
    captured = capsys.readouterr()
    assert "holds 2 certificates" in captured.err
    assert certs["local"][2] in captured.out and certs["other"][2] in captured.out
    assert [info.is_ca for info in tls.check_ca_file(bundle)] == [False, False]


def test_an_empty_ca_file_is_an_error_not_the_default(local_server, capsys):
    server, _cert, pin = local_server
    with pytest.raises(tls.TlsConfigError):
        tls.load_context("")
    url = f"https://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(FridayError) as caught:
        _policy(HttpFridayClient(ca_file=""), url, pin)
    assert caught.value.code == CODE_CA_FILE
    for argv in (
        ["replay", "--server", url, "--token-file", "t", "--ca-file", ""],
        ["selftest", "--nightly", "--ca-file", " "],
        ["ca", "set", ""],
    ):
        with pytest.raises(SystemExit):
            cli.main(argv)
        assert "must not be empty" in capsys.readouterr().err
    assert server.requests == []


def test_the_context_never_turns_verification_off(certs):
    context = tls.load_context(str(certs["local"][0]))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    default = tls.load_context(None)
    assert default.verify_mode == ssl.CERT_REQUIRED and default.check_hostname
