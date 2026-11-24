"""The `connectors.n8n_finished` probe (Phase 12 D1/D12, branch 4).

Announces approved n8n writes that reached a terminal state in the last
day: the tool's Friday label and a Friday status word ("n8n: Ev yedeği
bitti"), linked to the approval request the owner decided. Never the
workflow's result text, never arguments: the label comes from the manifest
(Friday-authored), the status from the closed execution vocabulary. The
probe decides nothing.
"""

from datetime import timedelta

from django.conf import settings

from connectors.manifest import get_manifest
from connectors.models import ConnectorWriteExecution
from core import probes

PROBE_NAME = "connectors.n8n_finished"
CONNECTOR = "n8n"
WINDOW = timedelta(days=1)

Status = ConnectorWriteExecution.Status
STATUS_WORDS = {
    Status.SUCCEEDED: "bitti",
    Status.FAILED: "başarısız",
    Status.UNKNOWN_OUTCOME: "sonucu belirsiz",
}


def _label(tool, status):
    manifest = get_manifest(CONNECTOR)
    policy = manifest.get_tool(tool) if manifest else None
    name = policy.display_label if policy else "iş akışı"
    return probes.safe_label(f"{name} {STATUS_WORDS[status]}", "iş akışı", prefix="n8n: ")


def collect(workspace, owner, now) -> probes.ProbeResult:
    finished = ConnectorWriteExecution.objects.for_actor(owner, workspace=workspace).filter(
        connector=CONNECTOR,
        status__in=list(STATUS_WORDS),
        finished_at__gte=now - WINDOW,
    )
    rows = list(
        finished.order_by("-finished_at", "-id").values_list(
            "id", "tool", "status", "approval_request_id"
        )[: int(settings.PROACTIVE_PROBE_MAX_KEYS)]
    )
    items = tuple(
        probes.ProbeItem(
            key=str(pk),
            label=_label(tool, status),
            link=f"/approvals/{approval_id}",
        )
        for pk, tool, status, approval_id in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=finished.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _, _, _ in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="connectors",
    collect=collect,
    digest_section="n8n",
    notice_kind=PROBE_NAME,
    link="/approvals",
    notice_template="{label}",
)


def register():
    probes.register_probe(SPEC)
