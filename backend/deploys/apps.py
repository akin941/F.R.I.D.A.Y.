from django.apps import AppConfig


class DeploysConfig(AppConfig):
    """Deploy targets, the gate-class `deploy` decision and the forced-
    command SSH runner (ADR-010, brief D10). Registered empty in Phase 5
    branch 1 so its import-linter contracts bite before any code lands;
    the app itself arrives in branch 6."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "deploys"
