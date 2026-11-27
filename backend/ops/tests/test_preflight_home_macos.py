"""Phase 13b branch 2 (brief D1, ADR-017 amendment 1): a macOS-declared
home server. `HOME_SERVER_PLATFORM=macos` (the interim server on the
MacBook) reports what macOS cannot provide as WARNINGS, never failures:
`home_server_platform` warns (Friday is down while the Mac sleeps) and
`home_disk_encryption` reads `HOME_FDE_CONFIRMED_ON` as the FileVault
attestation -- always a warning, its text naming FileVault, never LUKS.
`linux` keeps the Phase 13 behaviour; an unknown platform fails. Every
other `home_*` row is platform-independent (a stale backup still fails on
a Mac). No DNS (fake resolver), no model, no bucket."""

import socket

import pytest

from ai_core.models import ModelUsage
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from ops.tests.test_preflight_home import write_drill, write_status

HOME_ROWS = [
    "home_lan_only",
    "home_tls_pin",
    "home_backup_fresh",
    "home_restore_drill_age",
    "home_disk_encryption",
    "home_server_platform",
]


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    table = {"web": ["172.18.0.5"], "localhost": ["127.0.0.1", "::1"]}

    def resolve(host):
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return list(table[host])

    monkeypatch.setattr(preflight, "resolve_host", resolve)
    return table


@pytest.fixture
def mac(settings, tmp_path):
    """The interim Mac: LAN-only hosts (its `.local` name, its LAN address,
    loopback), fresh status files, FileVault attested, platform macos."""
    settings.HOME_SERVER_PLATFORM = "macos"
    settings.ALLOWED_HOSTS = ["owner-mbp.local", "192.168.1.30", "localhost", "127.0.0.1", "web"]
    settings.PREFLIGHT_NODE_TLS_URL = "https://192.168.1.30/ws/node/v1"
    settings.NODE_TLS_PIN_SHA256 = ""
    settings.HOME_BACKUP_STATUS_PATH = str(tmp_path / "status.json")
    settings.HOME_RESTORE_DRILL_STATUS_PATH = str(tmp_path / "drill.json")
    settings.HOME_BACKUP_WARN_HOURS = 26
    settings.HOME_BACKUP_FAIL_HOURS = 72
    settings.HOME_DRILL_WARN_DAYS = 35
    settings.HOME_DRILL_FAIL_DAYS = 90
    settings.HOME_FDE_CONFIRMED_ON = "2026-11-27"
    settings.HOME_BACKUP_OFFSITE_REPOSITORY = ""
    write_status(tmp_path, hours_ago=3)
    write_drill(tmp_path, days_ago=4)
    return tmp_path


def _rows():
    return {row.name: row for row in preflight.check_home()}


def test_the_platform_row_follows_disk_encryption_in_check_home(mac):
    assert [row.name for row in preflight.check_home()] == HOME_ROWS


@pytest.mark.django_db
@pytest.mark.parametrize("fde", ["2026-11-27", ""])
def test_a_macos_server_has_no_failing_home_row(mac, settings, fde):
    settings.HOME_FDE_CONFIRMED_ON = fde
    rows = _rows()
    assert [r for r in rows.values() if r.status == FAIL] == []
    assert rows["home_server_platform"].status == WARN
    assert rows["home_disk_encryption"].status == WARN
    assert rows["home_backup_fresh"].status == PASS
    assert rows["home_restore_drill_age"].status == PASS
    assert rows["home_lan_only"].status == PASS
    assert not ModelUsage.objects.exists()


def test_the_platform_row_warns_that_the_mac_sleeps(mac):
    row = _rows()["home_server_platform"]
    assert row.status == WARN
    assert "interim server (ADR-017 amendment 1)" in row.detail
    assert "Friday is down while the Mac sleeps" in row.detail


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-27", "FileVault owner-confirmed on 2026-11-27; no LUKS/TPM on macOS (interim)"),
        ("", "FileVault not confirmed (HOME_FDE_CONFIRMED_ON not set"),
        ("yes", "FileVault not confirmed (HOME_FDE_CONFIRMED_ON is not a past ISO date"),
        ("2999-01-01", "FileVault not confirmed (HOME_FDE_CONFIRMED_ON is not a past ISO date"),
    ],
)
def test_disk_encryption_on_macos_is_always_a_filevault_warning(mac, settings, value, expected):
    settings.HOME_FDE_CONFIRMED_ON = value
    row = _rows()["home_disk_encryption"]
    assert row.status == WARN  # never pass (unverifiable), never fail (not macOS's fault)
    assert expected in row.detail
    assert "FileVault" in row.detail
    assert "no LUKS/TPM on macOS" in row.detail
    assert "LUKS encrypted" not in row.detail
    assert "full-disk encryption on" not in row.detail


@pytest.mark.parametrize("value", ["macos", "MacOS", " macos "])
def test_the_platform_value_is_case_and_space_tolerant(mac, settings, value):
    settings.HOME_SERVER_PLATFORM = value
    assert _rows()["home_server_platform"].status == WARN


@pytest.mark.parametrize("value", ["linux", "", "Linux"])
def test_linux_keeps_the_phase_13_rows(mac, settings, value):
    settings.HOME_SERVER_PLATFORM = value
    rows = _rows()
    assert (rows["home_server_platform"].status, rows["home_server_platform"].detail) == (
        PASS,
        "linux (the always-on home server)",
    )
    assert (rows["home_disk_encryption"].status, rows["home_disk_encryption"].detail) == (
        PASS,
        "owner confirmed full-disk encryption on 2026-11-27",
    )
    settings.HOME_FDE_CONFIRMED_ON = ""
    row = _rows()["home_disk_encryption"]
    assert row.status == WARN
    assert "FileVault" not in row.detail


@pytest.mark.parametrize("value", ["windows", "darwin", "macos; rm -rf /", "linux,macos"])
def test_an_unknown_platform_fails_without_echoing_the_value(mac, settings, value):
    settings.HOME_SERVER_PLATFORM = value
    rows = _rows()
    row = rows["home_server_platform"]
    assert row.status == FAIL
    assert row.detail == "HOME_SERVER_PLATFORM is not one of linux, macos"
    assert value not in row.detail
    # Disk encryption is not assessed then (a warning; the platform row fails).
    assert rows["home_disk_encryption"].status == WARN
    assert "not assessed" in rows["home_disk_encryption"].detail


def test_a_stale_backup_still_fails_on_a_mac(mac):
    """Platform-independent rows keep their rules: a backup older than the
    fail threshold is real, not 'merely macOS'."""
    write_status(mac, hours_ago=100)
    row = _rows()["home_backup_fresh"]
    assert row.status == FAIL
    assert "older than HOME_BACKUP_FAIL_HOURS" in row.detail
    assert "LaunchAgent" in row.detail


def test_a_failed_drill_still_fails_on_a_mac(mac):
    write_drill(mac, days_ago=1, result="fail")
    assert _rows()["home_restore_drill_age"].status == FAIL


def test_a_public_host_still_fails_on_a_mac(mac, settings):
    settings.ALLOWED_HOSTS = ["8.8.8.8"]
    assert _rows()["home_lan_only"].status == FAIL


def test_the_setting_defaults_to_linux():
    from django.conf import settings as django_settings

    assert django_settings.HOME_SERVER_PLATFORM in ("linux", "")
