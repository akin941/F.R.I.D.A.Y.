from django.apps import AppConfig


class DeploysConfig(AppConfig):
    """Deploy targets, the gate-class `deploy` decision and the forced-
    command SSH runner (ADR-010 decision 7, brief D10). Registered empty in
    Phase 5 branch 1 so its import-linter contracts bit before any code
    landed; the app itself arrived in branch 6."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "deploys"

    def ready(self):
        # Once, at process startup, after every app's models have loaded.
        from deploys.tool import _register

        _register()
