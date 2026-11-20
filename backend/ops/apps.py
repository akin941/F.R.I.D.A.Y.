from django.apps import AppConfig


class OpsConfig(AppConfig):
    """Operations tooling (Phase 5 closeout): `manage.py preflight`, the
    live end-to-end check of a running stack. A leaf app: it reads kernel
    state (the gateway, connector pins, the database) but no app imports it
    (import-linter), and it has no models, views or URLs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ops"
