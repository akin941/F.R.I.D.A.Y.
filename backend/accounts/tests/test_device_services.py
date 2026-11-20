"""Device trust services and commands (Phase 4 branch 2): owner flag,
pairing codes (TTL, single use under concurrency, attempt burn, LAN
gating), remote key, revocation, idle expiry."""

import io
import threading
from datetime import timedelta
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from accounts import devices
from accounts.authentication import hash_token
from accounts.models import DevicePairingCode, RemoteAccessKey, TrustedDevice, User
from accounts.services import register_user
from audit.models import AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


def pin(owner, **kw):
    return devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.PIN,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        **kw,
    )


def strong(owner):
    return devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
    )


# --- owner flag ---


def test_only_one_owner_can_exist(owner):
    other = register_user(email="other@test.dev", password=PASSWORD)
    other.is_owner = True
    with pytest.raises(IntegrityError), transaction.atomic():
        other.save()


def _run_backfill():
    module = import_module("accounts.migrations.0005_backfill_owner")
    module.mark_single_owner(django_apps, None)


def test_backfill_marks_the_single_human_user():
    user = register_user(email="solo@test.dev", password=PASSWORD)
    User.objects.create_user(email="agent@test.dev", is_agent=True)
    _run_backfill()
    user.refresh_from_db()
    assert user.is_owner


def test_backfill_leaves_an_ambiguous_database_alone():
    register_user(email="a@test.dev", password=PASSWORD)
    register_user(email="b@test.dev", password=PASSWORD)
    _run_backfill()
    assert not User.objects.filter(is_owner=True).exists()
    with pytest.raises(CommandError, match="set_owner"):
        call_command("issue_pairing_code")


def test_set_owner_command_marks_and_moves_ownership(owner):
    other = register_user(email="other@test.dev", password=PASSWORD)
    device, _ = devices.enroll_device(raw_code=pin(owner)[0], device_name="Mac")
    with pytest.raises(CommandError, match="--replace"):
        call_command("set_owner", "--email", other.email)
    call_command("set_owner", "--email", other.email, "--replace", stdout=io.StringIO())
    owner.refresh_from_db()
    other.refresh_from_db()
    device.refresh_from_db()
    assert other.is_owner and not owner.is_owner
    assert device.revoke_reason == TrustedDevice.RevokeReason.OWNER_CHANGED
    assert AuditLog.objects.filter(action="accounts.owner.set", actor=other).exists()


def test_set_owner_refuses_an_agent():
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    with pytest.raises(CommandError):
        call_command("set_owner", "--email", agent.email)


def test_bootstrap_owner_marks_the_owner_and_if_absent_is_idempotent(monkeypatch):
    monkeypatch.setenv("FRIDAY_OWNER_PASSWORD", PASSWORD)
    out = io.StringIO()
    call_command("bootstrap_owner", "--email", "boss@test.dev", "--if-absent", stdout=out)
    assert User.objects.get(email="boss@test.dev").is_owner
    call_command("bootstrap_owner", "--email", "boss@test.dev", "--if-absent", stdout=out)
    assert User.objects.count() == 1
    assert "already exists" in out.getvalue()


def test_bootstrap_owner_if_absent_still_refuses_an_ownerless_populated_db(monkeypatch):
    register_user(email="stray@test.dev", password=PASSWORD)
    monkeypatch.setenv("FRIDAY_OWNER_PASSWORD", PASSWORD)
    with pytest.raises(CommandError, match="set_owner"):
        call_command("bootstrap_owner", "--email", "boss@test.dev", "--if-absent")


# --- pairing codes ---


def test_issue_pairing_code_command_prints_a_pin_and_audits(owner):
    out = io.StringIO()
    call_command("issue_pairing_code", "--plain", stdout=out)
    raw = out.getvalue().strip()
    assert raw.isdigit() and len(raw) == 8
    code = DevicePairingCode.objects.get()
    assert code.kind == DevicePairingCode.Kind.PIN
    assert code.code_hash == hash_token(raw)
    assert code.issued_via == DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND
    assert timedelta(minutes=4) < code.expires_at - timezone.now() <= timedelta(minutes=5)
    log = AuditLog.objects.get(action="accounts.pairing_code.issued")
    assert raw not in str(log.metadata)


def test_strong_code_is_long_and_lives_thirty_minutes(owner):
    out = io.StringIO()
    call_command("issue_pairing_code", "--strong", "--plain", stdout=out)
    raw = out.getvalue().strip()
    assert len(raw.replace("-", "")) >= 26  # >= 128 bits of base32
    code = DevicePairingCode.objects.get()
    assert code.kind == DevicePairingCode.Kind.STRONG
    assert timedelta(minutes=29) < code.expires_at - timezone.now() <= timedelta(minutes=30)


def test_pin_enrolls_once(owner):
    raw, code = pin(owner)
    device, secret = devices.enroll_device(raw_code=raw, device_name="Mac", client_ip="192.168.1.5")
    assert device.user == owner
    assert device.enrolled_via == TrustedDevice.EnrolledVia.PIN
    assert device.secret_hash == hash_token(secret)
    assert str(device.enrolled_ip) == "192.168.1.5"
    code.refresh_from_db()
    assert code.consumed_at is not None and code.consumed_by_device == device
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=raw, device_name="Again", client_ip="192.168.1.5")
    enrolled = AuditLog.objects.get(action="accounts.device.enrolled")
    assert enrolled.metadata["via"] == "pin"
    assert enrolled.metadata["remote"] is False


def test_code_input_is_normalized(owner):
    raw, _ = strong(owner)
    messy = "  " + raw.lower().replace("-", " ") + " "
    device, _ = devices.enroll_device(raw_code=messy, device_name="Phone", client_ip="8.8.8.8")
    assert device.enrolled_via == TrustedDevice.EnrolledVia.STRONG_CODE


def test_expired_code_is_refused(owner):
    raw, code = pin(owner)
    DevicePairingCode.objects.filter(pk=code.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=raw, device_name="Mac", client_ip="10.0.0.2")
    assert not TrustedDevice.objects.exists()


def test_five_wrong_pins_burn_the_live_pin(owner):
    raw, code = pin(owner)
    wrong = "00000000" if raw != "00000000" else "11111111"
    for _ in range(DevicePairingCode.MAX_ATTEMPTS):
        with pytest.raises(devices.InvalidPairingCode):
            devices.enroll_device(raw_code=wrong, device_name="x", client_ip="10.0.0.2")
    code.refresh_from_db()
    assert code.attempts == DevicePairingCode.MAX_ATTEMPTS
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=raw, device_name="Mac", client_ip="10.0.0.2")
    assert AuditLog.objects.filter(action="accounts.device.enroll_failed").exists()


def test_pin_from_a_non_lan_ip_is_refused_without_burning_it(owner, settings):
    raw, code = pin(owner)
    with pytest.raises(devices.PinNetworkRefused):
        devices.enroll_device(raw_code=raw, device_name="Mac", client_ip="203.0.113.9")
    code.refresh_from_db()
    assert code.attempts == 0 and code.consumed_at is None


@pytest.mark.parametrize("allow, accepted", [(True, True), (False, False)])
def test_pin_from_an_unknown_ip_depends_on_the_setting(owner, settings, allow, accepted):
    settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP = allow
    raw, _ = pin(owner)
    if accepted:
        devices.enroll_device(raw_code=raw, device_name="Mac", client_ip=None)
    else:
        with pytest.raises(devices.PinNetworkRefused):
            devices.enroll_device(raw_code=raw, device_name="Mac", client_ip=None)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.20.0.1", "100.101.1.1", "fd00::1"])
def test_pin_accepted_from_every_pairing_network(owner, ip):
    raw, _ = pin(owner)
    devices.enroll_device(raw_code=raw, device_name="Mac", client_ip=ip)


def test_strong_code_is_accepted_from_any_network(owner, settings):
    settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP = False
    raw, _ = strong(owner)
    device, _ = devices.enroll_device(raw_code=raw, device_name="Hotel", client_ip=None)
    assert device.enrolled_via == TrustedDevice.EnrolledVia.STRONG_CODE
    assert AuditLog.objects.get(action="accounts.device.enrolled").metadata["remote"] is True


def test_a_strong_code_cannot_be_burned_by_wrong_guesses(owner):
    raw, code = strong(owner)
    for _ in range(DevicePairingCode.MAX_ATTEMPTS + 2):
        with pytest.raises(devices.InvalidPairingCode):
            devices.enroll_device(raw_code="A" * 32, device_name="x", client_ip="8.8.8.8")
    devices.enroll_device(raw_code=raw, device_name="Hotel", client_ip="8.8.8.8")


def test_no_owner_means_no_enrollment():
    with pytest.raises(devices.OwnerNotConfigured):
        devices.enroll_device(raw_code="12345678", device_name="x", client_ip="10.0.0.1")


def test_non_owner_cannot_issue_codes(owner):
    other = register_user(email="other@test.dev", password=PASSWORD)
    with pytest.raises(devices.NotOwner):
        pin(other)


@pytest.mark.django_db(transaction=True)
def test_concurrent_consumption_enrolls_exactly_one_device():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    raw, _ = pin(user)
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt(name):
        try:
            barrier.wait()
            devices.enroll_device(raw_code=raw, device_name=name, client_ip="10.0.0.2")
            outcomes.append("ok")
        except devices.InvalidPairingCode:
            outcomes.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(f"d{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["ok", "refused"]
    assert TrustedDevice.objects.count() == 1


# --- sessions, idle expiry, revocation ---


def test_session_tokens_carry_the_device_claim(owner):
    device, secret = devices.enroll_device(raw_code=pin(owner)[0], device_name="Mac")
    same, tokens = devices.start_device_session(raw_secret=secret)
    assert same == device
    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

    assert AccessToken(tokens["access"])[devices.DEVICE_CLAIM] == str(device.pk)
    assert RefreshToken(tokens["refresh"])[devices.DEVICE_CLAIM] == str(device.pk)


def test_idle_expired_device_cannot_start_a_session(owner, settings):
    device, secret = devices.enroll_device(raw_code=pin(owner)[0], device_name="Mac")
    TrustedDevice.objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now() - timedelta(days=settings.DEVICE_IDLE_EXPIRY_DAYS + 1)
    )
    with pytest.raises(devices.DeviceRejected):
        devices.start_device_session(raw_secret=secret)


def test_revoked_device_cannot_start_a_session(owner):
    device, secret = devices.enroll_device(raw_code=pin(owner)[0], device_name="Mac")
    devices.revoke_device(device=device, actor=owner)
    with pytest.raises(devices.DeviceRejected):
        devices.start_device_session(raw_secret=secret)
    assert AuditLog.objects.filter(action="accounts.device.revoked").count() == 1
    devices.revoke_device(device=device, actor=owner)  # idempotent
    assert AuditLog.objects.filter(action="accounts.device.revoked").count() == 1


def test_device_of_a_former_owner_is_rejected(owner):
    device, secret = devices.enroll_device(raw_code=pin(owner)[0], device_name="Mac")
    User.objects.filter(pk=owner.pk).update(is_owner=False)
    with pytest.raises(devices.DeviceRejected):
        devices.start_device_session(raw_secret=secret)


def test_revoke_all_devices_command(owner):
    devices.enroll_device(raw_code=pin(owner)[0], device_name="A")
    devices.enroll_device(raw_code=pin(owner)[0], device_name="B")
    call_command("revoke_all_devices", stdout=io.StringIO())
    assert not TrustedDevice.objects.filter(revoked_at__isnull=True).exists()


# --- remote key ---


def test_no_remote_key_by_default_and_rotation_keeps_one_active(owner):
    assert devices.active_remote_key(owner) is None
    out = io.StringIO()
    call_command("rotate_remote_key", stdout=out)
    first = out.getvalue().strip()
    assert first.startswith("jrk_")
    second, _ = devices.rotate_remote_key(owner=owner, via="web")
    assert RemoteAccessKey.objects.filter(revoked_at__isnull=True).count() == 1
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=first, device_name="x", client_ip="8.8.8.8")
    device, _ = devices.enroll_device(raw_code=second, device_name="Laptop", client_ip="8.8.8.8")
    device2, _ = devices.enroll_device(raw_code=second, device_name="Phone", client_ip="8.8.8.8")
    assert device.enrolled_via == device2.enrolled_via == TrustedDevice.EnrolledVia.REMOTE_KEY
    assert devices.active_remote_key(owner).last_used_at is not None
    assert AuditLog.objects.filter(action="accounts.device.enrolled").count() == 2


def test_revoked_remote_key_enrolls_nothing(owner):
    raw, _ = devices.rotate_remote_key(owner=owner, via="web")
    call_command("rotate_remote_key", "--revoke", stdout=io.StringIO())
    with pytest.raises(devices.InvalidPairingCode):
        devices.enroll_device(raw_code=raw, device_name="x", client_ip="8.8.8.8")
