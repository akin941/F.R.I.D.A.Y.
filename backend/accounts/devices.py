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
`friday_device` cookie) and later mints JWT sessions carrying the device
id as the `did` claim. `accounts.authentication.DeviceJWTAuthentication`
re-checks the device row on every request, so revocation takes effect on
the next request.

Only the owner (`User.is_owner`, active, not an agent) can hold devices.

*Friday Node (Phase 9 spike S9.0, ADR-013 decision 2).* A node is a
`TrustedDevice` of `kind=node`, enrolled with a node pairing code
(`DevicePairingCode.device_kind=node`, `manage.py issue_pairing_code --node`
or a trusted web session) through `enroll_node_device` -- never through
`enroll_device`, which refuses node codes (and the node path refuses browser
codes and the remote key). Its `jnd_` secret never mints a JWT
(`start_device_session` and `device_session_is_valid` refuse `kind=node`);
`authenticate_node_secret` checks it for the node's own surfaces. Instead of
browser idle expiry it has a hard age cap from the last rotation
(NODE_CREDENTIAL_MAX_AGE_DAYS), a node idle window (NODE_IDLE_EXPIRY_DAYS),
rotation without re-pairing (`rotate_node_secret`, with a grace for the
previous secret) and the owner's reversible kill switch
(`engage_node_kill_switch` / `release_node_kill_switch`). Revocation and
the kill switch are announced through `core.revocation` so the node gateway
closes live sockets at once.
"""

import base64
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import (
    AUTH_FAILURE_AUDIT_LIMIT,
    AUTH_FAILURE_AUDIT_WINDOW_SECONDS,
    hash_token,
)
from accounts.models import (
    DevicePairingCode,
    NodeKillSwitch,
    RemoteAccessKey,
    TrustedDevice,
    User,
)
from audit.models import AuditLog
from audit.services import record_audit
from core.client_ip import ip_in_networks
from core.revocation import publish_device_revoked, publish_kill_switch_engaged

# The JWT claim naming the TrustedDevice a session belongs to.
DEVICE_CLAIM = "did"

DEVICE_SECRET_PREFIX = "jdv_"
NODE_SECRET_PREFIX = "jnd_"
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
# Failed node authentications: the same flood cap as PAT failures.
_NODE_AUTH_FAILURE_AUDIT_KEY = "accounts:node-auth-failure-audits"
# The audited identifier of a refused node secret: the prefix plus 4
# characters of the random part (never enough to reconstruct one).
_NODE_AUDITED_PREFIX_LENGTH = len(NODE_SECRET_PREFIX) + 4

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


class NodeKillSwitchEngaged(DeviceTrustError):
    """The owner's node kill switch is engaged: pairing a node (issuing a
    node code, or enrolling with one) is refused until it is released."""


class NodeCredentialRejected(DeviceRejected):
    """A node secret that is malformed, unknown, revoked, idle- or
    age-expired, a previous secret past its grace, of a non-owner, or of an
    owner whose kill switch is engaged -> 401. `reason` is the audited
    cause (one generic answer to the client, no oracle)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class NodeRotationRefused(DeviceTrustError):
    """A rotation presented with the previous secret during its grace (or
    racing another rotation) -> 403. Refused so a rotation cannot chain."""


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


def _live_codes(kind, now, device_kind=None):
    codes = DevicePairingCode.objects.filter(
        kind=kind,
        consumed_at__isnull=True,
        expires_at__gt=now,
        attempts__lt=DevicePairingCode.MAX_ATTEMPTS,
    )
    if device_kind is not None:
        codes = codes.filter(device_kind=device_kind)
    return codes


@transaction.atomic
def issue_pairing_code(
    *, owner, kind, issued_via, issued_by_device=None, device_kind=TrustedDevice.Kind.BROWSER
):
    """Mint a pairing code for the owner. Returns `(raw_code, row)`; the raw
    code exists only in this return value (shown once). Audited.

    `device_kind` names what the code may enroll: a browser (`/enroll`) or
    a Friday Node (`enroll_node_device`). The PIN/strong rules are the same
    for both. A node code is refused while the owner's kill switch is
    engaged."""
    if owner is None:
        raise OwnerNotConfigured(
            "No owner account: run `manage.py bootstrap_owner` (fresh install) or "
            "`manage.py set_owner --email ...` (existing database)."
        )
    _require_owner(owner)
    if device_kind not in TrustedDevice.Kind.values:
        raise DeviceTrustError(f"Unknown device kind {device_kind!r}.")
    if device_kind == TrustedDevice.Kind.NODE and node_kill_switch_engaged(owner):
        raise NodeKillSwitchEngaged("The node kill switch is engaged; release it first.")
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
        device_kind=device_kind,
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
            "device_kind": device_kind,
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


def _audit_enroll_failure(*, reason, kind, client_ip, device_kind=TrustedDevice.Kind.BROWSER):
    cache.add(_FAILURE_AUDIT_KEY, 0, timeout=_FAILURE_AUDIT_WINDOW_SECONDS)
    if cache.incr(_FAILURE_AUDIT_KEY) > _FAILURE_AUDIT_LIMIT:
        return
    record_audit(
        action="accounts.device.enroll_failed",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        metadata={
            "reason": reason,
            "kind": kind or "unrecognized",
            "device_kind": device_kind,
            "client_ip": client_ip or "",
        },
    )


def _new_device(*, owner, name, via, client_ip, user_agent, now, kind=TrustedDevice.Kind.BROWSER):
    prefix = NODE_SECRET_PREFIX if kind == TrustedDevice.Kind.NODE else DEVICE_SECRET_PREFIX
    raw_secret = prefix + secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=owner,
        name=name,
        kind=kind,
        secret_hash=hash_token(raw_secret),
        secret_issued_at=now if kind == TrustedDevice.Kind.NODE else None,
        enrolled_via=via,
        enrolled_ip=client_ip,
        user_agent=(user_agent or "")[:_USER_AGENT_MAX],
        last_seen_at=now,
        last_seen_ip=client_ip,
    )
    return device, raw_secret


def _consume(
    *,
    owner,
    kind,
    normalized,
    name,
    client_ip,
    user_agent,
    now,
    device_kind=TrustedDevice.Kind.BROWSER,
):
    """Inside the caller's transaction: lock and consume the matching code
    (or touch the remote key) and create the device. Returns
    `(device, raw_secret)`, or None when nothing matched. Only codes issued
    for `device_kind` match; the remote key enrolls browsers only.

    `select_for_update` on the code row serializes concurrent consumers of
    the same code: the second one blocks until the first commits, and
    Postgres then re-evaluates the `consumed_at IS NULL` predicate against
    the committed row, so it matches nothing -- single use holds under
    concurrency."""
    if kind == "remote_key":
        if device_kind != TrustedDevice.Kind.BROWSER:
            return None  # the remote key never enrolls a node
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
        _live_codes(kind, now, device_kind=device_kind)
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
        owner=owner,
        name=name,
        via=via,
        client_ip=client_ip,
        user_agent=user_agent,
        now=now,
        kind=device_kind,
    )
    code.consumed_at = now
    code.consumed_by_device = device
    code.save(update_fields=["consumed_at", "consumed_by_device", "updated_at"])
    return device, raw_secret


def enroll_device(*, raw_code, device_name, client_ip=None, user_agent=""):
    """Enroll a new trusted browser of the owner. Returns
    `(device, raw_device_secret)`.

    `client_ip` is the BFF-key-validated browser IP (`core.client_ip`) or
    None when unknown. A failed PIN attempt counts against every live PIN
    (a wrong guess cannot name "its" code); MAX_ATTEMPTS failures burn them.
    Strong codes (>=128 bits) and the remote key are not attempt-limited --
    they are not guessable, and burning them on internet noise would let
    anyone lock the owner out of remote enrollment; the enroll throttle
    bounds them instead. A code issued for a node is refused (generic
    InvalidPairingCode; the reason `wrong_device_kind` is audited)."""
    return _enroll(
        device_kind=TrustedDevice.Kind.BROWSER,
        raw_code=raw_code,
        device_name=device_name,
        default_name="Adsız cihaz",
        client_ip=client_ip,
        user_agent=user_agent,
    )


def enroll_node_device(*, raw_code, node_name, client_ip=None, user_agent=""):
    """Pair a Friday Node (Phase 9 D1/D4; the anonymous `POST
    /api/v1/nodes/pair` endpoint of branch 2 calls this). Returns
    `(device, raw_node_secret)` -- a `kind=node` TrustedDevice and its
    `jnd_` secret, shown once.

    The same code rules as `enroll_device` (PIN on the pairing networks
    only, attempt burn, single use under concurrency, TTLs), but only a code
    issued for a node matches: a browser code and the remote access key are
    refused (generic InvalidPairingCode, the reason audited). Refused with
    NodeKillSwitchEngaged while the owner's kill switch is engaged (nothing
    is consumed or burned)."""
    return _enroll(
        device_kind=TrustedDevice.Kind.NODE,
        raw_code=raw_code,
        device_name=node_name,
        default_name="Mac düğümü",
        client_ip=client_ip,
        user_agent=user_agent,
    )


def _enroll(*, device_kind, raw_code, device_name, default_name, client_ip, user_agent):
    audit_failure = partial(_audit_enroll_failure, client_ip=client_ip, device_kind=device_kind)
    owner = get_owner()
    if owner is None:
        audit_failure(reason="no_owner", kind=None)
        raise OwnerNotConfigured("No owner account is configured.")
    name = (device_name or "").strip()[:_DEVICE_NAME_MAX] or default_name
    kind, normalized = normalize_code(raw_code)
    if kind is None:
        audit_failure(reason="unrecognized", kind=None)
        raise InvalidPairingCode()
    if kind == "remote_key" and device_kind == TrustedDevice.Kind.NODE:
        audit_failure(reason="remote_key_for_node", kind=kind)
        raise InvalidPairingCode()
    if kind == DevicePairingCode.Kind.PIN and not pin_allowed_from(client_ip):
        # Refused before any lookup: an off-LAN source can neither use nor
        # burn a PIN.
        audit_failure(reason="pin_network", kind=kind)
        raise PinNetworkRefused()
    if device_kind == TrustedDevice.Kind.NODE and node_kill_switch_engaged(owner):
        # Refused before any lookup: nothing is consumed or burned.
        audit_failure(reason="kill_switch", kind=kind)
        raise NodeKillSwitchEngaged("The node kill switch is engaged.")

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
            device_kind=device_kind,
        )
    if result is None:
        reason = "no_match"
        if (
            kind != "remote_key"
            and _live_codes(kind, now)
            .exclude(device_kind=device_kind)
            .filter(user=owner, code_hash=hash_token(normalized))
            .exists()
        ):
            # A live code of the other device kind: refused like any miss
            # (and it counts as a failed PIN attempt), never consumed.
            reason = "wrong_device_kind"
        if kind == DevicePairingCode.Kind.PIN:
            _live_codes(kind, now).filter(user=owner).update(attempts=F("attempts") + 1)
        audit_failure(reason=reason, kind=kind)
        raise InvalidPairingCode()

    device, raw_secret = result
    remote = device.enrolled_via == TrustedDevice.EnrolledVia.REMOTE_KEY or not (
        client_ip and ip_in_networks(client_ip, settings.DEVICE_PAIRING_NETWORKS)
    )
    record_audit(
        action=(
            "accounts.node.enrolled"
            if device_kind == TrustedDevice.Kind.NODE
            else "accounts.device.enrolled"
        ),
        actor=owner,
        resource=device,
        metadata={
            "via": device.enrolled_via,
            "kind": device_kind,
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
        and device.kind == TrustedDevice.Kind.BROWSER  # a node never holds a web session
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
    # Fan-out to the node gateway (Phase 9 D1/D6), once the revoke is
    # committed; best effort, never raises (the heartbeat re-check is the
    # guarantee).
    transaction.on_commit(partial(publish_device_revoked, device.pk, device.user_id))
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


# --- Friday Node credentials (Phase 9 spike S9.0) ---


@dataclass(frozen=True)
class NodeCredential:
    """A successfully authenticated node secret. `via_previous_secret` is
    True when the previous secret was presented during its rotation grace
    (valid for the socket, never for another rotation)."""

    device: TrustedDevice
    via_previous_secret: bool = False


def node_kill_switch_engaged(user):
    return NodeKillSwitch.objects.filter(user=user, engaged_at__isnull=False).exists()


def node_device_refusal(device, now=None):
    """Why `device` may not act as a node right now, or None. The gateway's
    per-heartbeat re-check (Phase 9 D6) calls this with the freshly read
    row; the reasons map onto `bye` reasons (`revoked`, `kill_switch`,
    `credential_expired`)."""
    now = now or timezone.now()
    if device is None:
        return "unknown"
    if device.kind != TrustedDevice.Kind.NODE:
        return "wrong_kind"
    if device.revoked_at is not None:
        return "revoked"
    if not is_usable_owner(device.user):
        return "inactive_owner"
    if node_kill_switch_engaged(device.user):
        return "kill_switch"
    expires = device.credential_expires_at()
    if expires is not None and expires <= now:
        return "credential_expired"
    if device.idle_expires_at() <= now:
        return "idle_expired"
    return None


def _audit_node_auth_failure(raw_secret, reason, surface, client_ip):
    cache.add(_NODE_AUTH_FAILURE_AUDIT_KEY, 0, timeout=AUTH_FAILURE_AUDIT_WINDOW_SECONDS)
    if cache.incr(_NODE_AUTH_FAILURE_AUDIT_KEY) > AUTH_FAILURE_AUDIT_LIMIT:
        return
    prefix = (raw_secret or "")[:_NODE_AUDITED_PREFIX_LENGTH]
    if not prefix.startswith(NODE_SECRET_PREFIX):
        prefix = ""  # never audit a fragment of some other credential
    record_audit(
        action="accounts.node.auth_failed",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        metadata={
            "secret_prefix": prefix,
            "reason": reason,
            "surface": surface,
            "client_ip": client_ip or "",
        },
    )


def authenticate_node_secret(*, raw_secret, client_ip=None, surface="node", now=None):
    """Check a `jnd_` node secret. Returns a NodeCredential; raises
    NodeCredentialRejected (audited, flood-capped) otherwise.

    Accepted: the current secret of a `kind=node` device that is not
    revoked, not idle- or age-expired, of the active, human owner whose
    kill switch is released -- or, during the rotation grace, the previous
    secret. The first successful use of the new secret ends the grace (the
    previous hash is cleared), so an old secret dies as soon as the node is
    on its new one. Used by `NodeCredentialAuthentication` (the rotate
    endpoint) and, through `nodes`, by the WebSocket handshake."""
    now = now or timezone.now()

    def reject(reason):
        _audit_node_auth_failure(raw_secret, reason, surface, client_ip)
        raise NodeCredentialRejected(reason)

    if not raw_secret or not raw_secret.startswith(NODE_SECRET_PREFIX):
        reject("malformed")
    digest = hash_token(raw_secret)
    nodes = TrustedDevice.objects.select_related("user").filter(kind=TrustedDevice.Kind.NODE)
    device = nodes.filter(secret_hash=digest).first()
    via_previous = False
    if device is None:
        device = nodes.filter(previous_secret_hash=digest).first()
        if device is None:
            reject("unknown")
        if device.previous_secret_valid_until is None or device.previous_secret_valid_until <= now:
            reject("previous_expired")
        via_previous = True
    reason = node_device_refusal(device, now)
    if reason is not None:
        reject(reason)
    if not via_previous and device.previous_secret_hash:
        # First use of the new secret: the previous one stops working now.
        TrustedDevice.objects.filter(pk=device.pk, secret_hash=digest).update(
            previous_secret_hash="", previous_secret_valid_until=None
        )
        device.previous_secret_hash = ""
        device.previous_secret_valid_until = None
    touch_device(device, client_ip=client_ip, now=now)
    return NodeCredential(device=device, via_previous_secret=via_previous)


def rotate_node_secret(*, raw_secret, client_ip=None, now=None):
    """Rotate a node's secret without re-pairing (`POST
    /api/v1/auth/nodes/credential/rotate`). Returns `(device, raw_new_secret)`;
    the new secret exists only in this return value.

    The presented secret must be the CURRENT one: a rotation with the
    previous secret during its grace is refused (NodeRotationRefused), so a
    rotation cannot chain. The replaced secret stays valid for
    NODE_ROTATION_GRACE_SECONDS or until the new one is first used. The age
    cap restarts. Audited `accounts.node.rotated`."""
    now = now or timezone.now()
    credential = authenticate_node_secret(
        raw_secret=raw_secret, client_ip=client_ip, surface="rotate", now=now
    )
    device = credential.device
    if credential.via_previous_secret:
        record_audit(
            action="accounts.node.rotation_refused",
            actor=device.user,
            resource=device,
            metadata={"reason": "previous_secret", "client_ip": client_ip or ""},
        )
        raise NodeRotationRefused("Rotate with the current node secret.")
    with transaction.atomic():
        # Re-checked under the row lock: a concurrent rotation that won the
        # race has already replaced `secret_hash`, so this one matches
        # nothing and is refused (never two live successors).
        locked = (
            TrustedDevice.objects.select_for_update()
            .filter(pk=device.pk, kind=TrustedDevice.Kind.NODE, secret_hash=hash_token(raw_secret))
            .first()
        )
        if locked is None or node_device_refusal(locked, now) is not None:
            raise NodeRotationRefused("The node secret changed; rotate again with the new one.")
        raw_new = NODE_SECRET_PREFIX + secrets.token_urlsafe(32)
        locked.previous_secret_hash = locked.secret_hash
        locked.previous_secret_valid_until = now + timedelta(
            seconds=settings.NODE_ROTATION_GRACE_SECONDS
        )
        locked.secret_hash = hash_token(raw_new)
        locked.secret_issued_at = now
        locked.save(
            update_fields=[
                "previous_secret_hash",
                "previous_secret_valid_until",
                "secret_hash",
                "secret_issued_at",
                "updated_at",
            ]
        )
        record_audit(
            action="accounts.node.rotated",
            actor=locked.user,
            resource=locked,
            metadata={
                "previous_valid_until": locked.previous_secret_valid_until.isoformat(),
                "client_ip": client_ip or "",
            },
        )
    return locked, raw_new


@transaction.atomic
def engage_node_kill_switch(*, owner, via, device=None):
    """Engage the owner's node kill switch: every node authentication of
    this owner fails from now on, pairing a node is refused, and the gateway
    is told (after commit) to close this owner's live node sockets
    (`{"device_id": "*", "user_id": <owner>}`). Idempotent; re-engaging
    re-announces. Returns the row."""
    _require_owner(owner)
    switch, _ = NodeKillSwitch.objects.select_for_update().get_or_create(user=owner)
    if switch.engaged_at is None:
        switch.engaged_at = timezone.now()
        switch.engaged_via = via
        switch.engaged_by_device = device
        switch.save(update_fields=["engaged_at", "engaged_via", "engaged_by_device", "updated_at"])
        record_audit(
            action="accounts.node.kill_switch.engaged",
            actor=owner,
            resource=switch,
            metadata={"via": via, "device_id": str(device.pk) if device else ""},
        )
    transaction.on_commit(partial(publish_kill_switch_engaged, owner.pk))
    return switch


@transaction.atomic
def release_node_kill_switch(*, owner, via, device=None):
    """Release the kill switch: the owner's (non-revoked, unexpired) nodes
    authenticate again without re-pairing. Idempotent. Returns the row."""
    _require_owner(owner)
    switch, _ = NodeKillSwitch.objects.select_for_update().get_or_create(user=owner)
    if switch.engaged_at is not None:
        engaged_at = switch.engaged_at
        switch.engaged_at = None
        switch.released_at = timezone.now()
        switch.save(update_fields=["engaged_at", "released_at", "updated_at"])
        record_audit(
            action="accounts.node.kill_switch.released",
            actor=owner,
            resource=switch,
            metadata={
                "via": via,
                "device_id": str(device.pk) if device else "",
                "engaged_at": engaged_at.isoformat(),
            },
        )
    return switch


def node_kill_switch_state(owner):
    """The kill switch as the API/command show it (never anything else)."""
    switch = NodeKillSwitch.objects.filter(user=owner).first()
    if switch is None:
        return {"engaged": False, "engaged_at": None, "engaged_via": "", "released_at": None}
    return {
        "engaged": switch.engaged,
        "engaged_at": switch.engaged_at,
        "engaged_via": switch.engaged_via if switch.engaged else "",
        "released_at": switch.released_at,
    }


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
