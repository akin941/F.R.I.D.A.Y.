from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


def default_pat_expiry():
    """Default `PersonalAccessToken.expires_at` (Phase 4 branch 1): now +
    PAT_DEFAULT_TTL_DAYS. The mint view may choose a shorter or longer
    lifetime, capped at PAT_MAX_TTL_DAYS."""
    return timezone.now() + timedelta(days=settings.PAT_DEFAULT_TTL_DAYS)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class User(TimestampedModel, AbstractBaseUser, PermissionsMixin):
    """Friday user, identified by email (no username).

    `is_agent` marks agent service accounts (MVP 7+); agents authenticate like
    users but can never widen their scope (ADR-004).

    `is_owner` (Phase 4 branch 2, ADR-008 decision 2) marks THE owner of this
    single-owner installation -- at most one row (partial unique
    constraint). Only the owner can enroll trusted devices, so only the
    owner ever holds a web session; other users (tests, a stray
    `createsuperuser`) never obtain one.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    is_agent = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_owner = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_owner"],
                condition=models.Q(is_owner=True),
                name="accounts_single_owner",
            )
        ]

    def __str__(self):
        return self.email


class PersonalAccessToken(WorkspaceScopedModel, TimestampedModel):
    """CLI bearer credential (MVP 9 §1) — the CLI's low-trust counterpart of
    a trusted device's web session. Bound to exactly one workspace
    (`workspace`, from `WorkspaceScopedModel`) for its lifetime; switching
    workspaces means revoking and reminting, never an in-place change. Lives
    here, not in `integrations`, because `assistant`/`agents`/`approvals`
    need to import `PersonalAccessTokenAuthentication` directly to allowlist
    it, and routing that through `accounts` (already an open dependency for
    all of them via `User` FKs) avoids an `integrations <-> approvals`
    import cycle those apps otherwise have no reason to need.

    `kind` (Phase 4 branch 6) decides the one surface a token can reach and
    the channel it authenticates as: a `cli` token only the PAT-allowlisted
    views (assistant/agents/approvals/activity, CHANNEL_CLI), an `openai`
    token only the OpenAI-compatible facade (CHANNEL_OPENAI), a `hook` token
    only the Claude Code hook-event ingest (CHANNEL_HOOK, Phase 8A D6). An
    Open WebUI server's token can never create agents or approval requests.
    Fixed at mint, like the workspace.
    """

    class Kind(models.TextChoices):
        CLI = "cli"
        OPENAI = "openai"
        # Phase 8A D6: the Claude Code hook relay (`friday-hook`). Reaches
        # exactly one endpoint, the append-only hook-event ingest; reads
        # nothing back. Minted only by the installation owner, bound to
        # their personal workspace (the binding authenticates the principal;
        # D4 mapping rules alone decide where each event lands).
        HOOK = "hook"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personal_access_tokens"
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.CLI)
    # sha256 hex digest of the raw token; the raw value is never stored,
    # returned exactly once in the mint response.
    token_hash = models.CharField(max_length=64, unique=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Soft-revoke, never deleted — audit trail of what credentials existed.
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Hard expiry (Phase 4 branch 1): `PersonalAccessTokenAuthentication`
    # rejects the token from this moment on. Tokens minted before expiry
    # existed were backfilled to migration time + 30 days.
    expires_at = models.DateTimeField(default=default_pat_expiry)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user_id})"


# --- Device trust (Phase 4 branch 2, ADR-008 decision 2) ---
#
# Identity is device trust, not a password: the owner enrolls a browser once
# with a short-lived pairing code (or the remote access key) and that device
# then mints its own JWT sessions. Separate tables and authenticators from
# PersonalAccessToken on purpose: a PAT is a low-trust, workspace-bound API
# credential; these mint the high-trust, workspace-spanning web session.
# Only `accounts.authentication.hash_token` is shared.


class TrustedDevice(TimestampedModel):
    """One enrolled browser/device of the owner. Soft-revoked, never
    deleted (audit trail of which devices existed). Every web request
    re-checks its row (`DeviceJWTAuthentication`), so revoking is
    immediate for the next request."""

    class EnrolledVia(models.TextChoices):
        PIN = "pin"
        STRONG_CODE = "strong_code"
        REMOTE_KEY = "remote_key"

    class RevokeReason(models.TextChoices):
        REVOKED = "revoked"  # from another trusted session
        FORGOTTEN = "forgotten"  # "Bu cihazı unut" on the device itself
        REVOKE_ALL = "revoke_all"  # manage.py revoke_all_devices
        OWNER_CHANGED = "owner_changed"  # manage.py set_owner moved ownership

    class Kind(models.TextChoices):
        # A browser: HttpOnly cookie secret (`jdv_`), idle expiry, mints JWT
        # web sessions (the only way to CHANNEL_WEB).
        BROWSER = "browser"
        # A Friday Node (Phase 9 spike S9.0, ADR-013 decision 2): a
        # background agent on the owner's Mac holding a `jnd_` secret in its
        # Keychain. Never mints a JWT; authenticates as the low-trust
        # CHANNEL_NODE on its own two surfaces only. No idle expiry of the
        # browser kind: a hard age cap from the last rotation instead, plus
        # a node-specific idle window.
        NODE = "node"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trusted_devices"
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.BROWSER)
    # sha256 hex digest of the 256-bit device secret (held only in the
    # browser's HttpOnly `friday_device` cookie, or the node's Keychain);
    # the raw value is returned exactly once, in the enrollment (or node
    # rotation) response.
    secret_hash = models.CharField(max_length=64, unique=True)
    # Node rotation (Phase 9 D1): the replaced secret's hash stays valid
    # until `previous_secret_valid_until` OR until the new secret is first
    # used, whichever comes first (a lost rotate response must not lock the
    # node out). Empty for browsers and for nodes outside a grace window.
    previous_secret_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    previous_secret_valid_until = models.DateTimeField(null=True, blank=True)
    # When the current node secret was issued (enrollment or the last
    # rotation); the age cap NODE_CREDENTIAL_MAX_AGE_DAYS counts from here.
    secret_issued_at = models.DateTimeField(null=True, blank=True)
    enrolled_via = models.CharField(max_length=20, choices=EnrolledVia.choices)
    enrolled_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(
        max_length=20, choices=RevokeReason.choices, blank=True, default=""
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user_id})"

    @property
    def is_node(self):
        return self.kind == self.Kind.NODE

    def idle_expires_at(self):
        """Idle expiry: DEVICE_IDLE_EXPIRY_DAYS (browser) or
        NODE_IDLE_EXPIRY_DAYS (node, "no successful connection for that
        long") since the last use, or since enrollment for a never-used
        device."""
        days = settings.NODE_IDLE_EXPIRY_DAYS if self.is_node else settings.DEVICE_IDLE_EXPIRY_DAYS
        return (self.last_seen_at or self.created_at) + timedelta(days=days)

    def credential_expires_at(self):
        """A node's hard age cap (NODE_CREDENTIAL_MAX_AGE_DAYS since the
        current secret was issued); None for a browser."""
        if not self.is_node:
            return None
        issued = self.secret_issued_at or self.created_at
        return issued + timedelta(days=settings.NODE_CREDENTIAL_MAX_AGE_DAYS)

    def is_usable(self, now=None):
        now = now or timezone.now()
        if self.revoked_at is not None or self.idle_expires_at() <= now:
            return False
        expires = self.credential_expires_at()
        return expires is None or expires > now


class DevicePairingCode(TimestampedModel):
    """A one-time enrollment code. `pin`: 8 digits, 5-minute TTL, accepted
    only from the pairing networks (LAN gating), burned after
    MAX_ATTEMPTS failed PIN attempts. `strong`: >=128-bit base32, 30-minute
    TTL, accepted from any network. Issuable only by an already-trusted
    principal: the host shell (`manage.py issue_pairing_code`) or a trusted
    web session. Hashed; the raw code is shown exactly once."""

    MAX_ATTEMPTS = 5

    class Kind(models.TextChoices):
        PIN = "pin"
        STRONG = "strong"

    class IssuedVia(models.TextChoices):
        WEB = "web"
        MANAGEMENT_COMMAND = "management_command"

    # Which kind of device the code may enroll (Phase 9 D1): a browser code
    # is refused by the node pairing path and a node code by `/enroll`.
    DeviceKind = TrustedDevice.Kind

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pairing_codes"
    )
    # Not unique: an 8-digit PIN space is small, and a consumed/expired
    # row's hash must never block issuing the same digits again later.
    # Lookups always filter to live rows (unconsumed, unexpired, unburned).
    code_hash = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    device_kind = models.CharField(
        max_length=10, choices=TrustedDevice.Kind.choices, default=TrustedDevice.Kind.BROWSER
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    consumed_by_device = models.ForeignKey(
        TrustedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    issued_by_device = models.ForeignKey(
        TrustedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    issued_via = models.CharField(max_length=30, choices=IssuedVia.choices)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} pairing code {self.id}"


class RemoteAccessKey(TimestampedModel):
    """The owner's reusable remote enrollment key (`jrk_...`): enrolls a
    device from any network until rotated or revoked. None exists by
    default -- remote enrollment is off until the owner creates one. At
    most one active key per user (partial unique constraint)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="remote_access_keys"
    )
    key_hash = models.CharField(max_length=64, unique=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by_device = models.ForeignKey(
        TrustedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(revoked_at__isnull=True),
                name="accounts_one_active_remote_key",
            )
        ]

    def __str__(self):
        return f"remote access key {self.id} ({self.user_id})"


class NodeKillSwitch(TimestampedModel):
    """The owner's node kill switch (Phase 9 D1). One row per owner, created
    on first use. While `engaged_at` is set every node authentication of
    that owner fails, every live node socket of that owner is closed (the
    gateway listens on `core.revocation`), and pairing a new node is
    refused. Reversible without re-pairing -- the difference from
    revocation, which is permanent. Written only by
    `accounts.devices.engage_node_kill_switch` / `release_node_kill_switch`."""

    class Via(models.TextChoices):
        WEB = "web"
        MANAGEMENT_COMMAND = "management_command"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="node_kill_switch"
    )
    engaged_at = models.DateTimeField(null=True, blank=True)
    engaged_via = models.CharField(max_length=30, choices=Via.choices, blank=True, default="")
    engaged_by_device = models.ForeignKey(
        TrustedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    released_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        state = "engaged" if self.engaged_at else "released"
        return f"node kill switch {state} ({self.user_id})"

    @property
    def engaged(self):
        return self.engaged_at is not None
