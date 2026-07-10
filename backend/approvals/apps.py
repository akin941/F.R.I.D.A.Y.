from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class ApprovalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "approvals"

    def ready(self):
        # A `.env.example` comment is not enforcement (Codex plan-review
        # round-1 HIGH finding): the process refuses to boot at all if the
        # dev-only self-approval bypass is set while DEBUG is off, rather
        # than merely being discouraged by documentation.
        from django.conf import settings

        if getattr(settings, "DEV_ALLOW_SELF_APPROVAL", False) and not settings.DEBUG:
            raise ImproperlyConfigured(
                "DEV_ALLOW_SELF_APPROVAL must never be enabled outside DEBUG mode."
            )
