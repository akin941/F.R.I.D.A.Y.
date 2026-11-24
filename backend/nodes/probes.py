"""The `nodes.link` probe (Phase 12 D1, branch 2).

The owner's nodes in one workspace whose link is not healthy: offline
(`disconnected`), the kill switch engaged, or the credential revoked
(`nodes.selectors.node_status`). Labels are Friday words only -- a status,
never the node's self-reported version strings or name. The item key is
`<node id>:<status>`, so a node that goes from offline to revoked is new.
"""

from django.conf import settings

from core import probes
from nodes.selectors import node_status, nodes_for

PROBE_NAME = "nodes.link"
_STATUS_LABEL = {
    "disconnected": "Mac bağlantısı kopuk",
    "kill_switch": "Mac acil durdurmada",
    "revoked": "Mac kimliği iptal edildi",
}


def collect(workspace, owner, now) -> probes.ProbeResult:
    keys, items = [], []
    for node in nodes_for(owner).filter(workspace=workspace):
        status = node_status(node, now=now)
        label = _STATUS_LABEL.get(status)
        if label is None:
            continue
        key = f"{node.pk}:{status}"
        keys.append(key)
        if len(items) < probes.MAX_ITEMS:
            items.append(probes.ProbeItem(key=key, label=label, link="/devices"))
    keys = keys[: int(settings.PROACTIVE_PROBE_MAX_KEYS)]
    return probes.ProbeResult(
        count=len(keys),
        data_class=probes.data_class_of(workspace),
        items=tuple(item for item in items if item.key in keys),
        item_keys=tuple(keys),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="nodes",
    collect=collect,
    digest_section="nodes",
    notice_kind=PROBE_NAME,
    link="/devices",
    notice_template="{label}",
)


def register():
    probes.register_probe(SPEC)
