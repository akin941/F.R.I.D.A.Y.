"""`manage.py preflight` home-server rows (Phase 13 branch 7, brief D15):
`home_lan_only`, `home_tls_pin`, `home_backup_fresh`,
`home_restore_drill_age`, `home_disk_encryption` -- each passes, warns and
fails with fake status files and a fake resolver (seam `resolve_host`). No
DNS, no bucket, no node: the only network is a local TLS server on the
loopback for `home_tls_pin`."""

import base64
import datetime as dt
import hashlib
import json
import socket
import ssl
import threading
from io import StringIO

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.core.management import call_command
from django.utils import timezone

from ai_core.models import ModelUsage
from ops import preflight
from ops.preflight import FAIL, PASS, WARN

HOME_ROWS = [
    "home_lan_only",
    "home_tls_pin",
    "home_backup_fresh",
    "home_restore_drill_age",
    "home_disk_encryption",
]
DUMP = "friday-20261125T031000Z.dump.age"
SHA = "a" * 64
# The unpatched seam (the autouse `resolver` fixture replaces it per test).
_REAL_RESOLVE_HOST = preflight.resolve_host


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """A fake resolver: names map to the addresses in `table`; anything else
    raises like an unknown name. No test reaches DNS."""
    table = {}
    calls = []

    def resolve(host):
        calls.append(host)
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return list(table[host])

    monkeypatch.setattr(preflight, "resolve_host", resolve)
    return {"table": table, "calls": calls}


@pytest.fixture
def home(settings, tmp_path):
    """A configured home server: LAN-only hosts and URL, both status files
    present and fresh, FDE attested. Tests change one thing each."""
    settings.ALLOWED_HOSTS = ["friday-home.local", "192.168.1.20", "localhost", "web"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    settings.NODE_TLS_PIN_SHA256 = ""
    settings.HOME_BACKUP_STATUS_PATH = str(tmp_path / "status.json")
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(tmp_path / "drill.json")
    settings.HOME_BACKUP_WARN_HOURS = 26
    settings.HOME_BACKUP_FAIL_HOURS = 72
    settings.HOME_DRILL_WARN_DAYS = 35
    settings.HOME_DRILL_FAIL_DAYS = 90
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-01"
    write_status(tmp_path, hours_ago=3)
    write_drill(tmp_path, days_ago=4)
    return tmp_path


def _stamp(delta):
    return (timezone.now() - delta).astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(directory, *, hours_ago, **changes):
    data = {
        "v": 1,
        "finished_at": _stamp(dt.timedelta(hours=hours_ago)),
        "dump": DUMP,
        "bytes": 123456,
        "sha256": SHA,
        "restic_snapshot": "4f2a9c1b",
        **changes,
    }
    (directory / "status.json").write_text(json.dumps(data) + "\n")
    return data


def write_drill(directory, *, days_ago, **changes):
    data = {
        "v": 1,
        "drilled_at": _stamp(dt.timedelta(days=days_ago)),
        "dump": DUMP,
        "result": "pass",
        **changes,
    }
    (directory / "drill.json").write_text(json.dumps(data) + "\n")
    return data


def _rows():
    return {row.name: row for row in preflight.check_home()}


def _row(name):
    return _rows()[name]


# --- registration -----------------------------------------------------------


def test_the_home_checks_are_registered_after_skills_and_report_five_rows(home, resolver):
    resolver["table"]["web"] = ["172.18.0.5"]
    resolver["table"]["localhost"] = ["127.0.0.1", "::1"]
    names = [name for name, _ in preflight.CHECKS]
    # The closeout's `home_control` rows follow (test_preflight_home_control).
    assert names[-2:] == ["home", "home_control"]
    assert ("home", preflight.check_home) in preflight.CHECKS
    results = preflight.run_preflight(checks=(("home", preflight.check_home),))
    assert [r.name for r in results] == HOME_ROWS


@pytest.mark.django_db
def test_a_configured_home_server_passes_every_row_and_calls_no_model(home, resolver):
    resolver["table"]["web"] = ["172.18.0.5"]
    resolver["table"]["localhost"] = ["127.0.0.1", "::1"]
    rows = _rows()
    assert {name: rows[name].status for name in HOME_ROWS} == {
        "home_lan_only": PASS,
        "home_tls_pin": WARN,  # NODE_TLS_PIN_SHA256 not set here
        "home_backup_fresh": PASS,
        "home_restore_drill_age": PASS,
        "home_disk_encryption": PASS,
    }
    assert not ModelUsage.objects.exists()


def test_an_unconfigured_dev_stack_only_warns(settings, resolver):
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
    settings.PREFLIGHT_NODE_TLS_URL = ""
    settings.NODE_TLS_PIN_SHA256 = ""
    settings.HOME_BACKUP_STATUS_PATH = ""
    settings.HOME_RESTORE_DRILL_STATUS_PATH = ""
    settings.HOME_FDE_CONFIRMED_ON = ""
    resolver["table"]["localhost"] = ["127.0.0.1"]
    rows = _rows()
    assert {name: rows[name].status for name in HOME_ROWS} == dict.fromkeys(HOME_ROWS, WARN)
    assert "PREFLIGHT_NODE_TLS_URL not set (dev)" in rows["home_lan_only"].detail
    assert "runbook step 8" in rows["home_backup_fresh"].detail
    assert "runbook step 9" in rows["home_restore_drill_age"].detail


@pytest.mark.django_db
def test_the_command_prints_the_home_rows_and_never_a_path(home, resolver, monkeypatch):
    resolver["table"]["web"] = ["172.18.0.5"]
    resolver["table"]["localhost"] = ["127.0.0.1"]
    run = preflight.run_preflight
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda model_calls: run(model_calls=model_calls, checks=(("home", preflight.check_home),)),
    )
    (home / "status.json").write_text("not json")
    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("preflight", "--no-model-calls", stdout=out)
    text = out.getvalue()
    for name in HOME_ROWS:
        assert name in text
    assert str(home) not in text
    assert "HOME_BACKUP_STATUS_PATH" in text


# --- home_lan_only ----------------------------------------------------------


@pytest.mark.parametrize(
    "addresses",
    [
        ["192.168.1.20"],  # RFC 1918
        ["10.1.2.3", "172.16.9.9"],
        ["fd12:3456:789a::20"],  # ULA
        ["100.101.102.103"],  # Tailscale CGNAT
        ["127.0.0.1", "::1"],
        ["::ffff:192.168.1.20"],  # v4-mapped
    ],
)
def test_lan_addresses_pass(settings, resolver, addresses):
    settings.ALLOWED_HOSTS = ["friday-home.lan"]
    settings.PREFLIGHT_NODE_TLS_URL = "wss://friday-home.lan/ws/node/v1"
    resolver["table"]["friday-home.lan"] = addresses
    row = _row("home_lan_only")
    assert (row.status, row.detail) == (PASS, "2 host(s) LAN-only")


@pytest.mark.parametrize(
    "addresses",
    [["203.0.113.7"], ["192.168.1.20", "8.8.8.8"], ["2001:db8::1"], ["169.254.1.1"], ["0.0.0.0"]],
)
def test_a_name_resolving_to_a_public_address_fails(settings, resolver, addresses):
    settings.ALLOWED_HOSTS = ["friday.example.net"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    resolver["table"]["friday.example.net"] = addresses
    row = _row("home_lan_only")
    assert row.status == FAIL
    assert "not a LAN address: friday.example.net (DJANGO_ALLOWED_HOSTS)" in row.detail


def test_a_public_ip_literal_in_allowed_hosts_fails_without_resolving(settings, resolver):
    settings.ALLOWED_HOSTS = ["192.168.1.20", "203.0.113.7"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    row = _row("home_lan_only")
    assert row.status == FAIL
    assert "203.0.113.7 (DJANGO_ALLOWED_HOSTS)" in row.detail
    assert resolver["calls"] == []


@pytest.mark.parametrize("wildcard", ["*", ".example.net"])
def test_a_wildcard_host_fails(settings, resolver, wildcard):
    settings.ALLOWED_HOSTS = ["192.168.1.20", wildcard]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    row = _row("home_lan_only")
    assert row.status == FAIL
    assert f"a wildcard host: {wildcard}" in row.detail


def test_an_empty_host_list_fails(settings):
    settings.ALLOWED_HOSTS = []
    row = _row("home_lan_only")
    assert (row.status, row.detail) == (FAIL, "DJANGO_ALLOWED_HOSTS is empty")


def test_a_public_node_tls_url_host_fails(settings, resolver):
    settings.ALLOWED_HOSTS = ["192.168.1.20"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://friday.example.net/ws/node/v1"
    resolver["table"]["friday.example.net"] = ["198.51.100.4"]
    row = _row("home_lan_only")
    assert row.status == FAIL
    assert "friday.example.net (PREFLIGHT_NODE_TLS_URL)" in row.detail


def test_an_unresolvable_mdns_name_counts_as_link_local(settings, resolver):
    settings.ALLOWED_HOSTS = ["friday-home.local"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://friday-home.local/ws/node/v1"
    row = _row("home_lan_only")
    assert row.status == PASS
    assert "2 mDNS name(s) taken as link-local" in row.detail


def test_a_resolving_mdns_name_is_still_checked(settings, resolver):
    settings.ALLOWED_HOSTS = ["friday-home.local"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    resolver["table"]["friday-home.local"] = ["203.0.113.9"]
    assert _row("home_lan_only").status == FAIL


def test_an_unresolvable_ordinary_name_warns(settings, resolver):
    settings.ALLOWED_HOSTS = ["192.168.1.20", "friday-home.lan"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.20/ws/node/v1"
    row = _row("home_lan_only")
    assert row.status == WARN
    assert "could not resolve here: friday-home.lan (DJANGO_ALLOWED_HOSTS)" in row.detail
    # The warn says plainly the name was not proven LAN-only.
    assert "not verified LAN-only" in row.detail


def test_a_bracketed_ipv6_host_is_parsed(settings, resolver):
    settings.ALLOWED_HOSTS = ["[fd00::20]"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://[fd00::20]:8443/ws/node/v1"
    assert _row("home_lan_only").status == PASS


def test_a_url_without_a_host_fails(settings, resolver):
    settings.ALLOWED_HOSTS = ["192.168.1.20"]
    settings.PREFLIGHT_NODE_TLS_URL = "https:///ws/node/v1"
    row = _row("home_lan_only")
    assert (row.status, row.detail) == (FAIL, "PREFLIGHT_NODE_TLS_URL has no host")


def test_the_real_resolver_seam_reads_getaddrinfo(monkeypatch):
    seen = []

    def fake_getaddrinfo(host, port):
        seen.append((host, port))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 0)),
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.168.1.20", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::20", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert _REAL_RESOLVE_HOST("friday-home.local") == ["192.168.1.20", "fd00::20"]
    assert seen == [("friday-home.local", None)]


# --- home_tls_pin -------------------------------------------------------------


def _self_signed(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "friday-home.local")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return cert_path, key_path, hashlib.sha256(spki).digest()


@pytest.fixture
def tls_server(tmp_path):
    """A real TLS listener on 127.0.0.1 serving a fresh self-signed cert
    (accepts handshakes until the test ends)."""
    cert_path, key_path, pin = _self_signed(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as tls:
                    tls.recv(1)
            except (ssl.SSLError, OSError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield {"port": listener.getsockname()[1], "pin": pin}
    stop.set()
    listener.close()
    thread.join(timeout=2)


def test_the_served_lan_certificate_matches_the_pin(settings, tls_server):
    settings.NODE_TLS_PIN_SHA256 = base64.b64encode(tls_server["pin"]).decode()
    settings.PREFLIGHT_NODE_TLS_URL = f"https://127.0.0.1:{tls_server['port']}/ws/node/v1"
    row = _row("home_tls_pin")
    assert (row.status, row.detail) == (PASS, "served SPKI matches the pin")


def test_a_different_served_key_fails(settings, tls_server):
    settings.NODE_TLS_PIN_SHA256 = hashlib.sha256(b"another key").hexdigest()
    settings.PREFLIGHT_NODE_TLS_URL = f"wss://127.0.0.1:{tls_server['port']}/ws/node/v1"
    row = _row("home_tls_pin")
    assert row.status == FAIL
    assert "does not match NODE_TLS_PIN_SHA256" in row.detail


def test_a_handshake_error_fails(settings):
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()  # nothing listens there any more
    settings.NODE_TLS_PIN_SHA256 = hashlib.sha256(b"k").hexdigest()
    settings.PREFLIGHT_NODE_TLS_URL = f"https://127.0.0.1:{port}/ws/node/v1"
    row = _row("home_tls_pin")
    assert row.status == FAIL
    assert row.detail.startswith("TLS handshake failed")


@pytest.mark.parametrize(
    ("pin", "url", "detail"),
    [
        ("", "https://192.168.1.20/ws/node/v1", "NODE_TLS_PIN_SHA256 not set"),
        ("a" * 64, "", "PREFLIGHT_NODE_TLS_URL not set"),
    ],
)
def test_an_unconfigured_pin_warns(settings, pin, url, detail):
    settings.NODE_TLS_PIN_SHA256 = pin
    settings.PREFLIGHT_NODE_TLS_URL = url
    row = _row("home_tls_pin")
    assert (row.status, row.detail) == (WARN, detail)


def test_the_node_row_keeps_its_own_name(settings):
    settings.NODE_TLS_PIN_SHA256 = ""
    assert preflight._check_node_tls_pin().name == "node_tls_pin"


# --- home_backup_fresh --------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "status"), [(0.1, PASS), (25.9, PASS), (26.5, WARN), (71.5, WARN), (73, FAIL)]
)
def test_backup_age_thresholds(home, hours, status):
    write_status(home, hours_ago=hours)
    row = _row("home_backup_fresh")
    assert row.status == status
    assert DUMP in row.detail
    assert "restic 4f2a9c1b" in row.detail


def test_the_thresholds_are_settings(home, settings):
    settings.HOME_BACKUP_WARN_HOURS = 1
    settings.HOME_BACKUP_FAIL_HOURS = 2
    write_status(home, hours_ago=1.5)
    assert _row("home_backup_fresh").status == WARN


def test_inverted_thresholds_fail(home, settings):
    settings.HOME_BACKUP_WARN_HOURS = 72
    settings.HOME_BACKUP_FAIL_HOURS = 26
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert "HOME_BACKUP_FAIL_HOURS must be greater than HOME_BACKUP_WARN_HOURS" in row.detail


def test_a_configured_but_missing_status_file_fails(home):
    (home / "status.json").unlink()
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert "no status file at HOME_BACKUP_STATUS_PATH" in row.detail
    assert str(home) not in row.detail


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"v": 2}, "is not format v1"),
        ({"v": True}, "is not format v1"),
        ({"finished_at": "2026-11-25 03:10"}, "finished_at in the HOME_BACKUP_STATUS_PATH file"),
        ({"finished_at": "2026-13-45T03:10:00Z"}, "is not a UTC timestamp"),
        ({"dump": "../../etc/passwd"}, "dump in the HOME_BACKUP_STATUS_PATH file"),
        ({"dump": "friday-latest.dump"}, "is not a backup file name"),
        ({"bytes": 0}, "bytes in the"),
        ({"bytes": "12"}, "bytes in the"),
        ({"bytes": True}, "bytes in the"),
        ({"sha256": "XYZ"}, "sha256 in the"),
        ({"restic_snapshot": ""}, "restic_snapshot in the"),
        ({"restic_snapshot": "latest; rm -rf"}, "restic_snapshot in the"),
    ],
)
def test_a_malformed_status_file_fails(home, changes, detail):
    write_status(home, hours_ago=1, **changes)
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert detail in row.detail


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", "\xff\xfe"])
def test_a_non_json_status_file_fails(home, content):
    (home / "status.json").write_bytes(content.encode("latin-1"))
    assert _row("home_backup_fresh").status == FAIL


def test_an_oversized_status_file_fails(home):
    (home / "status.json").write_text(" " * 5000 + "{}")
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert "larger than 4096 bytes" in row.detail


def test_a_future_timestamp_fails(home):
    write_status(home, hours_ago=-2)
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert "in the future" in row.detail


def test_a_directory_in_place_of_the_status_file_fails(home, settings):
    settings.HOME_BACKUP_STATUS_PATH = str(home)
    row = _row("home_backup_fresh")
    assert row.status == FAIL
    assert "is not readable" in row.detail


# --- home_restore_drill_age ---------------------------------------------------


@pytest.mark.parametrize(
    ("days", "status"), [(0, PASS), (34.9, PASS), (36, WARN), (89, WARN), (91, FAIL)]
)
def test_drill_age_thresholds(home, days, status):
    write_drill(home, days_ago=days)
    row = _row("home_restore_drill_age")
    assert row.status == status
    assert DUMP in row.detail


def test_a_failed_last_drill_fails_whatever_its_age(home):
    write_drill(home, days_ago=0, result="fail")
    row = _row("home_restore_drill_age")
    assert row.status == FAIL
    assert "the last restore drill failed" in row.detail


def test_a_failed_drill_without_a_dump_still_reads(home):
    write_drill(home, days_ago=0, result="fail", dump="")
    row = _row("home_restore_drill_age")
    assert row.status == FAIL
    assert "no dump" in row.detail


@pytest.mark.parametrize(
    "changes",
    [
        {"result": "ok"},
        {"result": None},
        {"dump": ""},  # a passed drill names its dump
        {"dump": "friday.sql"},
        {"drilled_at": 12},
        {"v": "1"},
    ],
)
def test_a_malformed_drill_file_fails(home, changes):
    write_drill(home, days_ago=1, **changes)
    assert _row("home_restore_drill_age").status == FAIL


def test_a_configured_but_missing_drill_file_fails(home):
    (home / "drill.json").unlink()
    row = _row("home_restore_drill_age")
    assert row.status == FAIL
    assert "no status file at HOME_RESTORE_DRILL_STATUS_PATH" in row.detail


# --- home_disk_encryption -----------------------------------------------------


def test_disk_encryption_attested_passes(settings):
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-01"
    row = _row("home_disk_encryption")
    assert (row.status, row.detail) == (PASS, "owner confirmed full-disk encryption on 2026-11-01")


@pytest.mark.parametrize(
    ("value", "detail"),
    [
        ("", "HOME_FDE_CONFIRMED_ON not set"),
        ("yes", "not an ISO date"),
        ("2999-01-01", "in the future"),
    ],
)
def test_disk_encryption_never_fails(settings, value, detail):
    settings.HOME_FDE_CONFIRMED_ON = value
    row = _row("home_disk_encryption")
    assert row.status == WARN
    assert detail in row.detail


# --- robustness ---------------------------------------------------------------


def test_one_crashing_home_row_does_not_hide_the_others(home, monkeypatch):
    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(preflight, "_check_home_backup_fresh", boom)
    rows = _rows()
    assert rows["home_backup_fresh"].status == FAIL
    assert rows["home_backup_fresh"].detail == "check crashed: RuntimeError"
    assert rows["home_restore_drill_age"].status == PASS
