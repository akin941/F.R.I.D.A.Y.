"""The `tasks.due_today` and `tasks.overdue` probes (Phase 12 D1, branch 2).

Open, live (not trashed) tasks of one workspace by their calendar
`due_date` against the local date of `now` (`TIME_ZONE`). The label is the
task's title -- the owner's own row -- through `core.probes.safe_label`
(a title with markup or a link falls back to the Friday word "görev").
"""

from django.conf import settings
from django.utils import timezone

from core import probes
from tasks.models import Task

DUE_TODAY = "tasks.due_today"
OVERDUE = "tasks.overdue"


def _result(workspace, queryset):
    rows = list(
        queryset.order_by("due_date", "created_at", "id").values_list("id", "title")[
            : int(settings.PROACTIVE_PROBE_MAX_KEYS)
        ]
    )
    items = tuple(
        probes.ProbeItem(key=str(pk), label=probes.safe_label(title, "görev"), link="/tasks")
        for pk, title in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=queryset.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _ in rows),
    )


def _open(owner, workspace):
    return Task.objects.for_actor(owner, workspace=workspace).filter(
        status=Task.Status.OPEN, due_date__isnull=False
    )


def collect_due_today(workspace, owner, now) -> probes.ProbeResult:
    today = timezone.localdate(now)
    return _result(workspace, _open(owner, workspace).filter(due_date=today))


def collect_overdue(workspace, owner, now) -> probes.ProbeResult:
    today = timezone.localdate(now)
    return _result(workspace, _open(owner, workspace).filter(due_date__lt=today))


SPECS = (
    probes.ProbeSpec(
        name=DUE_TODAY,
        owner_app="tasks",
        collect=collect_due_today,
        digest_section="tasks",
        notice_kind=DUE_TODAY,
        link="/tasks",
        notice_template="Bugün: {label}",
    ),
    probes.ProbeSpec(
        name=OVERDUE,
        owner_app="tasks",
        collect=collect_overdue,
        digest_section="tasks",
        link="/tasks",
    ),
)


def register():
    for spec in SPECS:
        probes.register_probe(spec)
