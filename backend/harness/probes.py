"""The `harness.waiting_gates` probe (Phase 12 D1, branch 2).

Implementation jobs of one workspace waiting at Gate A (plan approval) or
Gate B (push approval). The label is a Friday template over the repo's
allowlisted name (an owner-registered `HarnessRepo` row) -- never the goal,
the plan, the diff summary or any agent output. Each item links to the
job's page, where the owner decides on `/approvals`; the probe decides
nothing.
"""

from django.conf import settings

from core import probes
from harness.models import HarnessImplementationJob

PROBE_NAME = "harness.waiting_gates"
_GATE_LABEL = {
    HarnessImplementationJob.Status.PENDING_GATE_A: "Gate A",
    HarnessImplementationJob.Status.PENDING_GATE_B: "Gate B",
}


def collect(workspace, owner, now) -> probes.ProbeResult:
    waiting = HarnessImplementationJob.objects.for_actor(owner, workspace=workspace).filter(
        status__in=list(_GATE_LABEL)
    )
    rows = list(
        waiting.order_by("-created_at", "-id").values_list("id", "status", "repo__name")[
            : int(settings.PROACTIVE_PROBE_MAX_KEYS)
        ]
    )
    items = tuple(
        probes.ProbeItem(
            key=str(pk),
            label=probes.safe_label(repo_name, "depo", prefix=f"{_GATE_LABEL[status]}: "),
            link=f"/harness/implementations/{pk}",
        )
        for pk, status, repo_name in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=waiting.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _, _ in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="harness",
    collect=collect,
    digest_section="harness",
    notice_kind=PROBE_NAME,
    link="/harness",
    notice_template="{label} onayını bekliyor",
)


def register():
    probes.register_probe(SPEC)
