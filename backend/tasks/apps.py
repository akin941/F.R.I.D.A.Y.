from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasks"

    def ready(self):
        # Phase 12 D1: the `tasks.due_today` / `tasks.overdue` probes.
        from tasks.probes import register

        register()
