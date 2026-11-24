from django.apps import AppConfig
from django.conf import settings

from core.unattended import deny_task_if_unattended


def guard_task_publish(sender=None, **kwargs):
    """`before_task_publish` (a real broker publish; `sender` is the task
    name): inside an unattended context, refuse any task outside
    `PROACTIVE_UNATTENDED_TASKS`. The context does not cross a Celery hop,
    so dispatch itself is the guard (D2)."""
    deny_task_if_unattended(str(sender or ""), settings.PROACTIVE_UNATTENDED_TASKS)


def guard_task_prerun(sender=None, **kwargs):
    """`task_prerun`: the same check for an eager `apply_async` (no publish
    happens in eager mode, so `before_task_publish` never fires). In a
    worker process no context is open when a task starts: a no-op there."""
    deny_task_if_unattended(getattr(sender, "name", "") or "", settings.PROACTIVE_UNATTENDED_TASKS)


class ProactiveConfig(AppConfig):
    """Phase 12 (D1-D5, ADR-016): owner schedules, proactive runs, notices.
    May import only `core`, `workspaces`, `audit`, `ai_core`, `accounts`;
    other apps' data arrives through `core.probes`. Only `assistant`, `hud`
    (both through `proactive.api` alone) and `ops` may import it."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "proactive"

    def ready(self):
        from celery.signals import before_task_publish, task_prerun

        before_task_publish.connect(
            guard_task_publish, weak=False, dispatch_uid="proactive.guard_publish"
        )
        task_prerun.connect(guard_task_prerun, weak=False, dispatch_uid="proactive.guard_prerun")
