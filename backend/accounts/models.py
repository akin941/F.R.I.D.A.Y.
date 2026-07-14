from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


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
    """Jarvis user. Email login, no username.

    `is_agent` marks agent service accounts (MVP 7+); agents authenticate like
    users but can never widen their scope (ADR-004).
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    is_agent = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class PersonalAccessToken(WorkspaceScopedModel, TimestampedModel):
    """CLI bearer credential (MVP 9 §1) — the CLI's equivalent of the JWT
    `AuditedLoginView` already mints. Bound to exactly one workspace
    (`workspace`, from `WorkspaceScopedModel`) for its lifetime; switching
    workspaces means revoking and reminting, never an in-place change. Lives
    here, not in `integrations`, because `assistant`/`agents`/`approvals`
    need to import `PersonalAccessTokenAuthentication` directly to allowlist
    it, and routing that through `accounts` (already an open dependency for
    all of them via `User` FKs) avoids an `integrations <-> approvals`
    import cycle those apps otherwise have no reason to need.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personal_access_tokens"
    )
    name = models.CharField(max_length=255)
    # sha256 hex digest of the raw token; the raw value is never stored,
    # returned exactly once in the mint response.
    token_hash = models.CharField(max_length=64, unique=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Soft-revoke, never deleted — audit trail of what credentials existed.
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user_id})"
