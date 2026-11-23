"""Read models of the owner's nodes (Phase 9 brief D11): what the node APIs
and (branch 7) the HUD health panel show. Codes, counts, short hashes and
times only -- never a path, a schema's content or any read output.

`node_status(node)` is one of:
- `revoked`: the device row is revoked, or its credential or idle window
  expired (re-pairing is the only way back);
- `kill_switch`: the owner's kill switch is engaged;
- `connected` / `disconnected`: the presence key (`nodes.presence`);
- `credential_expiring`: connected or not, the secret is older than
  `NODE_CREDENTIAL_ROTATE_AFTER_DAYS` (the node should have rotated);
  reported instead of `connected`/`disconnected`.
`node_health(owner)` adds `never_paired` for an owner without nodes.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.devices import node_device_refusal
from nodes import presence
from nodes.models import CapabilitySnapshot, Node

STATUSES = (
    "connected",
    "disconnected",
    "kill_switch",
    "revoked",
    "credential_expiring",
    "never_paired",
)


def nodes_for(actor):
    """The actor's own nodes, through `for_actor()` (rule 1): the node rows
    in workspaces the actor can see, of which the actor is the owner."""
    return (
        Node.objects.for_actor(actor)
        .filter(owner=actor)
        .select_related("device", "device__user", "workspace")
        .order_by("-created_at")
    )


def latest_snapshot(node):
    return CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()


def credential_age_days(device, now=None) -> int:
    now = now or timezone.now()
    issued = device.secret_issued_at or device.created_at
    return max(0, (now - issued).days)


def node_status(node, *, online=None, now=None) -> str:
    now = now or timezone.now()
    refusal = node_device_refusal(node.device, now)
    if refusal == "kill_switch":
        return "kill_switch"
    if refusal is not None:
        return "revoked"
    issued = node.device.secret_issued_at or node.device.created_at
    rotate_after = timedelta(days=int(settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS))
    if now - issued >= rotate_after:
        return "credential_expiring"
    online = presence.is_online(node) if online is None else online
    return "connected" if online else "disconnected"


def _snapshot_summary(snapshot) -> dict:
    entries = list(snapshot.entries or ()) if snapshot is not None else []
    return {
        "catalogue_sha256": snapshot.catalogue_sha256[:12] if snapshot is not None else "",
        "advertised_count": len(entries),
        "invocable_count": sum(1 for entry in entries if entry.get("invocable")),
        "snapshot_at": snapshot.created_at if snapshot is not None else None,
    }


def node_summary(node, *, now=None) -> dict:
    """One node for the list (D11): status, versions, heartbeat, spool
    depth, credential age and catalogue counts."""
    now = now or timezone.now()
    online = presence.is_online(node)
    snapshot = latest_snapshot(node)
    device = node.device
    return {
        "id": str(node.pk),
        "name": node.name,
        "status": node_status(node, online=online, now=now),
        "online": online,
        "protocol_version": node.protocol_version,
        "node_version": node.node_version,
        "os_version": node.os_version,
        "last_heartbeat_at": node.last_heartbeat_at,
        "spool_count": node.spool_count,
        "spool_bytes": node.spool_bytes,
        "paired_at": node.created_at,
        "revoked_at": device.revoked_at,
        "credential_age_days": credential_age_days(device, now),
        "credential_expires_at": device.credential_expires_at(),
        "rotate_after_days": int(settings.NODE_CREDENTIAL_ROTATE_AFTER_DAYS),
        "pinned_key_id": node.pinned_key_id,
        **_snapshot_summary(snapshot),
    }


def snapshot_entries(snapshot) -> list:
    """The snapshot's entries as the detail view shows them: name, version,
    class, availability, invocable and the `not_invocable` reason. Never the
    schemas (their hashes stay server-side)."""
    if snapshot is None:
        return []
    return [
        {
            "name": entry.get("name"),
            "version": entry.get("version"),
            "class": entry.get("class"),
            "availability": entry.get("availability"),
            "invocable": bool(entry.get("invocable")),
            "reason": entry.get("reason"),
        }
        for entry in snapshot.entries or ()
    ]


def node_detail(node, *, now=None) -> dict:
    summary = node_summary(node, now=now)
    summary["capabilities"] = snapshot_entries(latest_snapshot(node))
    return summary


def node_health(owner, *, now=None) -> dict:
    """The HUD health panel's `node` block (D11; wired by branch 7): the
    most relevant of the owner's nodes -- a connected one first, else the
    most recently paired non-revoked one, else the latest. No path, no
    content."""
    nodes = list(
        Node.objects.filter(owner=owner)
        .select_related("device", "device__user")
        .order_by("-created_at")
    )
    if not nodes:
        return {"status": "never_paired"}
    summaries = [node_summary(node, now=now) for node in nodes]
    ranked = sorted(
        summaries,
        key=lambda item: (
            item["status"] != "connected",
            item["status"] == "revoked",
        ),
    )
    best = ranked[0]
    return {
        "status": best["status"],
        "node_id": best["id"],
        "last_heartbeat_at": best["last_heartbeat_at"],
        "catalogue_sha256": best["catalogue_sha256"],
        "invocable_count": best["invocable_count"],
        "advertised_count": best["advertised_count"],
        "spool_depth": best["spool_count"],
        "node_version": best["node_version"],
    }
