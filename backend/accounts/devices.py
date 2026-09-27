"""Device trust services (Phase 4 branch 2, ADR-008 decision 2) -- the only
sanctioned write path for `TrustedDevice`, `DevicePairingCode` and
`RemoteAccessKey` rows.

Flows:

- *First device:* `manage.py bootstrap_owner`, then `manage.py
  issue_pairing_code [--strong]` prints a code once. Host shell access is
  the proof of ownership. The owner enters it at `/enroll`.
- *Further devices:* a trusted web session issues a PIN ("Yeni cihaz
  eşleştir"), the new device enters it at `/enroll`.
- *Outside the LAN:* a strong code, or the reusable remote access key
  (`jrk_...`, created/rotated/revoked from a trusted session or `manage.py
  rotate_remote_key`).

Every enrollment creates a `TrustedDevice` holding the sha256 of a fresh
256-bit device secret; the raw secret goes to the browser once (HttpOnly
`jarvis_device` cookie) and later mints JWT sessions carrying the device
id as the `did` claim. `accounts.authentication.DeviceJWTAuthentication`
re-checks the device row on every request, so revocation takes effect on
the next request.

Only the owner (`User.is_owner`, active, not an agent) can hold devices.
"""

import base64
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import hash_token
from accounts.models import DevicePairingCode, RemoteAccessKey, TrustedDevice, User
from audit.models import AuditLog
from audit.services import record_audit
from core.client_ip import ip_in_networks

# The JWT claim naming the TrustedDevice a session belongs to.
DEVICE_CLAIM = "did"

DEVICE_SECRET_PREFIX = "jdv_"
REMOTE_KEY_PREFIX = "jrk_"

PIN_TTL_SECONDS = 5 * 60
STRONG_TTL_SECONDS = 30 * 60
_STRONG_CODE_BYTES = 20  # 160 bits -> 32 base32 characters (>= 128 bits)
_PIN_DIGITS = 8

_PIN_SHAPE = re.compile(r"\d{8}")
_STRONG_SHAPE = re.compile(r"[A-Z2-7]{26,64}")
_SEPARATORS = re.compile(r"[\s-]")

# Failed enrollments are audited, but a flood must not flood the audit log
# (same fixed-window cap as PAT auth failures).
_FAILURE_AUDIT_LIMIT = 10
_FAILURE_AUDIT_WINDOW_SECONDS = 60
_FAILURE_AUDIT_KEY = "accounts:enroll-failure-audits"

# `last_seen_*` is refreshed at most this often per device, so the per-request
# device check stays a read, not a write on every request.
LAST_SEEN_WRITE_INTERVAL_SECONDS = 300

_USER_AGENT_MAX = 255
_DEVICE_NAME_MAX = 100


class DeviceTrustError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class OwnerNotConfigured(DeviceTrustError):
    """No (usable) owner account -> the installation must run
    `bootstrap_owner` or `set_owner` first."""


class NotOwner(DeviceTrustError):
    """The actor is not the installation's owner -> 403."""


class InvalidPairingCode(DeviceTrustError):
    """Unknown, expired, consumed or burned code / wrong remote key -- one
    generic error for every reason (no oracle); the reason is audited."""


class PinNetworkRefused(DeviceTrustError):
    """A PIN from outside DEVICE_PAIRING_NETWORKS (or from an unknown IP
    while DEVICE_PAIRING_ALLOW_UNKNOWN_IP is off) -> 403."""


class DeviceRejected(DeviceTrustError):
    """A device secret that is unknown, revoked or idle-expired, or whose
    user is no longer the active owner -> 401 (the browser re-enrolls)."""


# --- owner ---


def get_owner():
    return User.objects.filter(is_owner=True, is_active=True, is_agent=False).first()


def is_usable_owner(user):
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_owner
        and user.is_active
        and not user.is_agent
    )


def _require_owner(actor):
    if not is_usable_owner(actor):
        raise NotOwner("Only the installation owner can manage devices.")


@transaction.atomic
def set_owner(*, user, replace=False):
    """Mark `user` as the owner (`manage.py set_owner`). Moving ownership
    away from an existing owner requires `replace=True` and revokes every
    device and remote key of the previous owner -- their web sessions end
    with the next request."""
    if user.is_agent or not user.is_active:
        raise DeviceTrustError("The owner must be an active, human (non-agent) user.")
    current = User.objects.select_for_update().filter(is_owner=True).first()
    if current is not None and current.pk == user.pk:
        return user
    if current is not None:
        if not replace:
            raise DeviceTrustError(
                f"{current.email} is already the owner; pass --replace to move ownership."
            )
        current.is_owner = False
        current.save(update_fields=["is_owner", "updated_at"])
        revoke_all_devices(user=current, reason=TrustedDevice.RevokeReason.OWNER_CHANGED)
        RemoteAccessKey.objects.filter(user=current, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
    user.is_owner = True
    user.save(update_fields=["is_owner", "updated_at"])
    record_audit(
        action="accounts.owner.set",
        actor=user,
        resource=user,
        metadata={"previous_owner_id": str(current.pk) if current else ""},
    )
    return user


# --- pairing codes ---


def _generate_pin():
    return f"{secrets.randbelow(10**_PIN_DIGITS):0{_PIN_DIGITS}d}"


def _generate_strong_code():
    raw = base64.b32encode(secrets.token_bytes(_STRONG_CODE_BYTES)).decode().rstrip("=")
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def normalize_code(raw):
    """Classify and normalize user input: `(kind, normalized)` where kind is
    "remote_key", "pin", "strong" or None (unrecognizable). PINs and strong
    codes are compared without separators/whitespace and case-insensitively
    (people type them from a screen)."""
    raw = (raw or "").strip()
    if raw.startswith(REMOTE_KEY_PREFIX):
        return "remote_key", raw
    compact = _SEPARATORS.sub("", raw).upper()
    if _PIN_SHAPE.fullmatch(compact):
        return DevicePairingCode.Kind.PIN, compact
    if _STRONG_SHAPE.fullmatch(compact):
        return DevicePairingCode.Kind.STRONG, compact
    return None, None


def _live_codes(kind, now):
    return DevicePairingCode.objects.filter(
        kind=kind,
        consumed_at__isnull=True,
        expires_at__gt=now,
        attempts__lt=DevicePairingCode.MAX_ATTEMPTS,
    )


@transaction.atomic
def issue_pairing_code(*, owner, kind, issued_via, issued_by_device=None):
    """Mint a pairing code for the owner. Returns `(raw_code, row)`; the raw
    code exists only in this return value (shown once). Audited."""
    if owner is None:
        raise OwnerNotConfigured(
            "No owner account: run `manage.py bootstrap_owner` (fresh install) or "
            "`manage.py set_owner --email ...` (existing database)."
        )
    _require_owner(owner)
    now = timezone.now()
    if kind == DevicePairingCode.Kind.PIN:
        ttl = PIN_TTL_SECONDS
        # A live PIN with the same digits would make both ambiguous; draw
        # again (the space is 10^8, so this practically never loops).
        for _ in range(10):
            raw = _generate_pin()
            if not _live_codes(kind, now).filter(code_hash=hash_token(raw)).exists():
                break
        else:  # pragma: no cover - astronomically unlikely
            raise DeviceTrustError("Could not draw a unique PIN; try again.")
        normalized = raw
    elif kind == DevicePairingCode.Kind.STRONG:
        ttl = STRONG_TTL_SECONDS
        raw = _generate_strong_code()
        normalized = _SEPARATORS.sub("", raw)
    else:
        raise DeviceTrustError(f"Unknown pairing code kind {kind!r}.")
    code = DevicePairingCode.objects.create(
        user=owner,
        code_hash=hash_token(normalized),
        kind=kind,
        expires_at=now + timedelta(seconds=ttl),
        issued_via=issued_via,
        issued_by_device=issued_by_device,
    )
    record_audit(
        action="accounts.pairing_code.issued",
        actor=owner,
        resource=code,
        metadata={
            "kind": kind,
            "via": issued_via,
            "issued_by_device_id": str(issued_by_device.pk) if issued_by_device else "",
            "expires_at": code.expires_at.isoformat(),
        },
    )
    return raw, code


# --- enrollment ---


def pin_allowed_from(client_ip):
    """LAN gating for PINs: a validated client IP must be inside
    DEVICE_PAIRING_NETWORKS; an unknown IP passes only while
    DEVICE_PAIRING_ALLOW_UNKNOWN_IP is on ("LAN gating off")."""
    if client_ip is None:
        return settings.DEVICE_PAIRING_ALLOW_UNKNOWN_IP
    return ip_in_networks(client_ip, settings.DEVICE_PAIRING_NETWORKS)


def _audit_enroll_failure(*, reason, kind, client_ip):
    cache.add(_FAILURE_AUDIT_KEY, 0, timeout=_FAILURE_AUDIT_WINDOW_SECONDS)
    if cache.incr(_FAILURE_AUDIT_KEY) > _FAILURE_AUDIT_LIMIT:
        return
    record_audit(
        action="accounts.device.enroll_failed",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        metadata={"reason": reason, "kind": kind or "unrecognized", "client_ip": client_ip or ""},
    )


def _new_device(*, owner, name, via, client_ip, user_agent, now):
    raw_secret = DEVICE_SECRET_PREFIX + secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=owner,
        name=name,
        secret_hash=hash_token(raw_secret),
        enrolled_via=via,
        enrolled_ip=client_ip,
        user_agent=(user_agent or "")[:_USER_AGENT_MAX],
        last_seen_at=now,
        last_seen_ip=client_ip,
    )
    return device, raw_secret


def _consume(*, owner, kind, normalized, name, client_ip, user_agent, now):
    """Inside the caller's transaction: lock and consume the matching code
    (or touch the remote key) and create the device. Returns
    `(device, raw_secret)`, or None when nothing matched.

    `select_for_update` on the code row serializes concurrent consumers of
    the same code: the second one blocks until the first commits, and
    Postgres then re-evaluates the `consumed_at IS NULL` predicate against
    the committed row, so it matches nothing -- single use holds under
    concurrency."""
    if kind == "remote_key":
        key = (
            RemoteAccessKey.objects.select_for_update()
            .filter(user=owner, key_hash=hash_token(normalized), revoked_at__isnull=True)
            .first()
        )
        if key is None:
            return None
        key.last_used_at = now
        key.save(update_fields=["last_used_at", "updated_at"])
        return _new_device(
            owner=owner,
            name=name,
            via=TrustedDevice.EnrolledVia.REMOTE_KEY,
            client_ip=client_ip,
            user_agent=user_agent,
            now=now,
        )
    code = (
        _live_codes(kind, now)
        .select_for_update()
        .filter(user=owner, code_hash=hash_token(normalized))
        .first()
    )
    if code is None:
        return None
    via = (
        TrustedDevice.EnrolledVia.PIN
        if kind == DevicePairingCode.Kind.PIN
        else TrustedDevice.EnrolledVia.STRONG_CODE
    )
    device, raw_secret = _new_device(
        owner=owner, name=name, via=via, client_ip=client_ip, user_agent=user_agent, now=now
    )
    code.consumed_at = now
    code.consumed_by_device = device
    code.save(update_fields=["consumed_at", "consumed_by_device", "updated_at"])
    return device, raw_secret


def enroll_device(*, raw_code, device_name, client_ip=None, user_agent=""):
    """Enroll a new trusted device of the owner. Returns
    `(device, raw_device_secret)`.

    `client_ip` is the BFF-key-validated browser IP (`core.client_ip`) or
    None when unknown. A failed PIN attempt counts against every live PIN
    (a wrong guess cannot name "its" code); MAX_ATTEMPTS failures burn them.
    Strong codes (>=128 bits) and the remote key are not attempt-limited --
    they are not guessable, and burning them on internet noise would let
    anyone lock the owner out of remote enrollment; the enroll throttle
    bounds them instead."""
    owner = get_owner()
    if owner is None:
        _audit_enroll_failure(reason="no_owner", kind=None, client_ip=client_ip)
        raise OwnerNotConfigured("No owner account is configured.")
    name = (device_name or "").strip()[:_DEVICE_NAME_MAX] or "Adsız cihaz"
    kind, normalized = normalize_code(raw_code)
    if kind is None:
        _audit_enroll_failure(reason="unrecognized", kind=None, client_ip=client_ip)
        raise InvalidPairingCode()
    if kind == DevicePairingCode.Kind.PIN and not pin_allowed_from(client_ip):
        # Refused before any lookup: an off-LAN source can neither use nor
        # burn a PIN.
        _audit_enroll_failure(reason="pin_network", kind=kind, client_ip=client_ip)
        raise PinNetworkRefused()

    now = timezone.now()
    with transaction.atomic():
        result = _consume(
            owner=owner,
            kind=kind,
            normalized=normalized,
            name=name,
            client_ip=client_ip,
            user_agent=user_agent,
            now=now,
        )
    if result is None:
        if kind == DevicePairingCode.Kind.PIN:
            _live_codes(kind, now).filter(user=owner).update(attempts=F("attempts") + 1)
        _audit_enroll_failure(reason="no_match", kind=kind, client_ip=client_ip)
        raise InvalidPairingCode()

    device, raw_secret = result
    remote = device.enrolled_via == TrustedDevice.EnrolledVia.REMOTE_KEY or not (
        client_ip and ip_in_networks(client_ip, settings.DEVICE_PAIRING_NETWORKS)
    )
    record_audit(
        action="accounts.device.enrolled",
        actor=owner,
        resource=device,
        metadata={
            "via": device.enrolled_via,
            "client_ip": client_ip or "",
            "remote": remote,
        },
    )
    return device, raw_secret


# --- sessions ---


def issue_session_tokens(device):
    """A fresh JWT pair bound to `device` (the `did` claim is copied into
    the access token and survives refresh rotation)."""
    refresh = RefreshToken.for_user(device.user)
    refresh[DEVICE_CLAIM] = str(device.pk)
    # `for_user` recorded the OutstandingToken before the claim existed;
    # keep the stored copy identical to the token actually handed out.
    OutstandingToken.objects.filter(jti=refresh[jwt_settings.JTI_CLAIM]).update(token=str(refresh))
    update_last_login(None, device.user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def device_session_is_valid(device, user):
    """The per-request check behind the web channel's definition: the
    device belongs to this user, is neither revoked nor idle-expired, and
    the user is the active, human owner."""
    return (
        device is not None
        and device.user_id == user.pk
        and device.is_usable()
        and (is_usable_owner(user))
    )


def touch_device(device, client_ip=None, now=None):
    """Refresh `last_seen_*`, at most once per LAST_SEEN_WRITE_INTERVAL."""
    now = now or timezone.now()
    last = device.last_seen_at
    if last is not None and (now - last).total_seconds() < LAST_SEEN_WRITE_INTERVAL_SECONDS:
        return
    device.last_seen_at = now
    fields = ["last_seen_at"]
    if client_ip:
        device.last_seen_ip = client_ip
        fields.append("last_seen_ip")
    TrustedDevice.objects.filter(pk=device.pk).update(**{f: getattr(device, f) for f in fields})


def start_device_session(*, raw_secret, client_ip=None):
    """Mint a session from a device secret (the BFF's silent re-mint when a
    refresh fails, and `/api/auth/session/start` on a fresh page load).
    Raises DeviceRejected for anything but a usable device of the owner."""
    if not raw_secret or not raw_secret.startswith(DEVICE_SECRET_PREFIX):
        raise DeviceRejected()
    device = (
        TrustedDevice.objects.select_related("user")
        .filter(secret_hash=hash_token(raw_secret))
        .first()
    )
    if device is None or not device_session_is_valid(device, device.user):
        raise DeviceRejected()
    touch_device(device, client_ip=client_ip)
    return device, issue_session_tokens(device)


# --- revocation ---


def _revoke(device, reason, now):
    if device.revoked_at is not None:
        return False
    device.revoked_at = now
    device.revoke_reason = reason
    device.save(update_fields=["revoked_at", "revoke_reason", "updated_at"])
    return True


@transaction.atomic
def revoke_device(*, device, actor, reason=TrustedDevice.RevokeReason.REVOKED):
    """Revoke one of the owner's devices; idempotent. Immediate for the next
    request of that device (DeviceJWTAuthentication re-checks the row)."""
    _require_owner(actor)
    locked = TrustedDevice.objects.select_for_update().filter(pk=device.pk, user=actor).first()
    if locked is None:
        raise NotOwner("Unknown device.")
    if _revoke(locked, reason, timezone.now()):
        record_audit(
            action="accounts.device.revoked",
            actor=actor,
            resource=locked,
            metadata={"reason": reason},
        )
    return locked


@transaction.atomic
def revoke_all_devices(*, user, reason=TrustedDevice.RevokeReason.REVOKE_ALL):
    """Revoke every active device of `user` (lost/stolen device response,
    ownership change). Returns the number revoked."""
    now = timezone.now()
    count = 0
    for device in TrustedDevice.objects.select_for_update().filter(
        user=user, revoked_at__isnull=True
    ):
        if _revoke(device, reason, now):
            count += 1
    record_audit(
        action="accounts.device.revoked_all",
        actor=user,
        resource=user,
        metadata={"reason": reason, "count": count},
    )
    return count


# --- remote access key ---


@transaction.atomic
def rotate_remote_key(*, owner, via, device=None):
    """Create the owner's remote access key, revoking any active one first
    (create and rotate are the same operation). Returns `(raw_key, row)`."""
    if owner is None:
        raise OwnerNotConfigured("No owner account is configured.")
    _require_owner(owner)
    now = timezone.now()
    replaced = RemoteAccessKey.objects.filter(user=owner, revoked_at__isnull=True).update(
        revoked_at=now
    )
    raw_key = REMOTE_KEY_PREFIX + secrets.token_urlsafe(32)
    key = RemoteAccessKey.objects.create(
        user=owner, key_hash=hash_token(raw_key), created_by_device=device
    )
    record_audit(
        action="accounts.remote_key.rotated" if replaced else "accounts.remote_key.created",
        actor=owner,
        resource=key,
        metadata={"via": via, "device_id": str(device.pk) if device else ""},
    )
    return raw_key, key


@transaction.atomic
def revoke_remote_key(*, owner, via):
    """Turn remote enrollment off. Returns True when a key was revoked."""
    if owner is None:
        raise OwnerNotConfigured("No owner account is configured.")
    _require_owner(owner)
    keys = list(
        RemoteAccessKey.objects.select_for_update().filter(user=owner, revoked_at__isnull=True)
    )
    now = timezone.now()
    for key in keys:
        key.revoked_at = now
        key.save(update_fields=["revoked_at", "updated_at"])
        record_audit(
            action="accounts.remote_key.revoked", actor=owner, resource=key, metadata={"via": via}
        )
    return bool(keys)


def active_remote_key(owner):
    return RemoteAccessKey.objects.filter(user=owner, revoked_at__isnull=True).first()
