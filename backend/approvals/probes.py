"""The `approvals.pending` probe (Phase 12 D1, branch 2).

Pending, still-decidable approval requests of one workspace (the HUD's
freshness rule: created within `APPROVAL_REQUEST_TTL_HOURS`). The label is
a Friday template over the registered tool's code name -- never the
payload, never a model's words. Each item links to `/approvals/<id>`,
where the owner decides; the probe decides nothing.
"""

import re
from datetime import timedelta

from django.conf import settings

from approvals.models import ApprovalRequest
from core import probes

PROBE_NAME = "approvals.pending"
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")


def _label(tool_name):
    name = tool_name if isinstance(tool_name, str) and _TOOL_NAME.match(tool_name) else ""
    return probes.safe_label(name, "araç", prefix="Onay: ")


def collect(workspace, owner, now) -> probes.ProbeResult:
    fresh_since = now - timedelta(hours=int(settings.APPROVAL_REQUEST_TTL_HOURS))
    pending = ApprovalRequest.objects.for_actor(owner, workspace=workspace).filter(
        status=ApprovalRequest.Status.PENDING, created_at__gte=fresh_since
    )
    rows = list(
        pending.order_by("-created_at", "-id").values_list("id", "tool_name")[
            : int(settings.PROACTIVE_PROBE_MAX_KEYS)
        ]
    )
    items = tuple(
        probes.ProbeItem(key=str(pk), label=_label(tool), link=f"/approvals/{pk}")
        for pk, tool in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=pending.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _ in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="approvals",
    collect=collect,
    digest_section="approvals",
    notice_kind=PROBE_NAME,
    link="/approvals",
    notice_template="{label} bekliyor",
)


def register():
    probes.register_probe(SPEC)
