from django.apps import AppConfig


class ApprovalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "approvals"

    def ready(self):
        # Phase 12 D1: the `approvals.pending` probe (core.probes registry).
        from approvals.probes import register

        register()
