from django.apps import AppConfig


class HarnessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harness"

    def ready(self):
        import harness.checks  # noqa: F401 -- registers the agent-policy system check
        import harness.signals  # noqa: F401 -- connects the gate-decision receiver
        from harness.tools import _register

        _register()
