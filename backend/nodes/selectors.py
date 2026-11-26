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

Phase 11 D15: `node_health` also carries `voice`, the node's mic state as
its last `voice.state` event reported it (`voice_summary`). It reads
`unknown` when the node is offline or the state is older than two
heartbeats (`VOICE_STATE_STALE_SECONDS`, the server's receive time), and
`off` while the node is revoked or its kill switch is engaged: the node's
credential is then refused, and the protocol obliges a cut-off node to
release the mic (ADR-013 decision 6) -- "off" there is the server's
fail-closed reading, not an observation.
"""

import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from accounts.devices import node_device_refusal
from audit.models import AuditLog
from nodes import presence
from nodes.models import CapabilitySnapshot, Node, NodeInvocation
from nodes.protocol.messages import VOICE_STATE_STALE_SECONDS
from workspaces.models import Workspace

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
        .select_related("device", "device__user", "workspace", "room")
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
        # Phase 13 D3: the owner's labels (server-side data only).
        **node_labels(node),
        **_snapshot_summary(snapshot),
    }


def node_labels(node) -> dict:
    """The node's owner-set labels: name key, room (id, name, key or None),
    role, platform, aliases and when the owner last used it."""
    room = node.room
    return {
        "name_key": node.name_key,
        "room": None if room is None else {"id": str(room.pk), "name": room.name, "key": room.key},
        "role": node.role,
        "platform": node.platform,
        "aliases": list(node.aliases or []),
        "last_active_at": node.last_active_at,
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


VOICE_UNKNOWN = "unknown"
_VOICE_CUT_OFF = frozenset({"kill_switch", "revoked"})


def voice_summary(node, *, status, online, now=None) -> dict:
    """The node's mic state for the HUD (D15): state, mode, session flag,
    the three daily counters and when the server last heard it. Codes and
    counts only."""
    now = now or timezone.now()
    reported_at = node.voice_state_at if node is not None else None
    blank = {
        "state": VOICE_UNKNOWN,
        "mode": "",
        "session_open": False,
        "wake_accepts_today": 0,
        "wake_discards_today": 0,
        "storm_mutes_today": 0,
        "at": reported_at,
    }
    if node is None:
        return blank
    if status in _VOICE_CUT_OFF:
        return {**blank, "state": "off"}
    fresh = (
        bool(node.voice_state)
        and reported_at is not None
        and now - reported_at <= timedelta(seconds=VOICE_STATE_STALE_SECONDS)
    )
    if not online or not fresh:
        return blank
    return {
        "state": node.voice_state,
        "mode": node.voice_mode,
        "session_open": node.voice_session_open,
        "wake_accepts_today": node.voice_wake_accepts_today,
        "wake_discards_today": node.voice_wake_discards_today,
        "storm_mutes_today": node.voice_storm_mutes_today,
        "at": reported_at,
    }


def node_health(owner, *, now=None) -> dict:
    """The HUD health panel's `node` block (D11; `hud.services.node_section`
    since branch 7), read through `nodes_for()` (`for_actor()`): the
    most relevant of the owner's nodes -- a connected one first, else the
    most recently paired non-revoked one, else the latest. No path, no
    content."""
    never = {"status": "never_paired", "voice": voice_summary(None, status=None, online=False)}
    if owner is None or getattr(owner, "pk", None) is None:
        return never
    nodes = list(nodes_for(owner))
    if not nodes:
        return never
    pairs = [(node_summary(node, now=now), node) for node in nodes]
    ranked = sorted(
        pairs,
        key=lambda pair: (
            pair[0]["status"] != "connected",
            pair[0]["status"] == "revoked",
        ),
    )
    best, best_node = ranked[0]
    return {
        "status": best["status"],
        "node_id": best["id"],
        "last_heartbeat_at": best["last_heartbeat_at"],
        "catalogue_sha256": best["catalogue_sha256"],
        "invocable_count": best["invocable_count"],
        "advertised_count": best["advertised_count"],
        "spool_depth": best["spool_count"],
        "node_version": best["node_version"],
        "voice": voice_summary(best_node, status=best["status"], online=best["online"], now=now),
    }


# --- the invocation log (Phase 9 D11, branch 5) ---


def invocation_page(actor, node, *, before_seq=None, limit=50) -> list:
    """The node's chain rows in workspaces `actor` can see (`for_actor()`),
    newest first, `seq < before_seq` when given. Hashes, codes, sizes and
    the workspace's name -- never args or output content (D8, D11)."""
    rows = (
        NodeInvocation.objects.for_actor(actor)
        .filter(node=node)
        .select_related("workspace")
        .order_by("-seq")
    )
    if before_seq is not None:
        rows = rows.filter(seq__lt=before_seq)
    return [_invocation_row(row) for row in rows[: max(1, int(limit))]]


def _invocation_row(row) -> dict:
    return {
        "id": str(row.pk),
        "seq": row.seq,
        "created_at": row.created_at,
        "capability": row.capability,
        "capability_version": row.capability_version,
        "class": row.invocation_class or "read",
        "status": row.status,
        "refusal_code": row.refusal_code,
        "args_sha256": row.args_sha256,
        "output_sha256": row.output_sha256,
        "output_bytes": row.output_bytes,
        "truncated": row.truncated,
        "dropped_unmapped": row.dropped_unmapped,
        "duration_ms": row.duration_ms,
        "channel": row.channel,
        "trace_id": row.trace_id,
        "workspace_id": str(row.workspace_id),
        "workspace_name": row.workspace.name,
        "row_hash": row.row_hash,
    }


# --- the per-node audit (Phase 13 D8) ---

# Rows that are about the node: its chain rows are listed from
# `NodeInvocation` itself, so their `nodes.invoke` AuditLog twin is left
# out, as are the "somebody viewed this log" rows.
_AUDIT_EXCLUDED_ACTIONS = frozenset(
    {"nodes.invoke", "nodes.invocations.viewed", "nodes.audit.viewed"}
)
# Owner-wide events that affect every node of the owner.
_AUDIT_OWNER_WIDE_ACTIONS = frozenset(
    {"accounts.node.kill_switch.engaged", "accounts.node.kill_switch.released"}
)
# The only metadata keys an audit event shows: codes, keys, ids, counts --
# never args, output content, free text or a client address.
AUDIT_DETAIL_KEYS = frozenset(
    {
        "reason",
        "refusal_code",
        "via",
        "status",
        "capability",
        "capability_version",
        "class",
        "fields",
        "old_key",
        "new_key",
        "name_key",
        "alias_keys",
        "old_role",
        "new_role",
        "old_room_id",
        "new_room_id",
        "platform",
        "protocol_version",
        "pinned_key_id",
        "candidates",
        "count",
    }
)
AUDIT_PAGE_SIZE = 50


class InvalidAuditCursor(ValueError):
    pass


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def encode_audit_cursor(created_at, row_id) -> str:
    """URL-safe: `<microseconds since the epoch>.<uuid>`."""
    return f"{(created_at - _EPOCH) // _MICROSECOND}.{row_id}"


def decode_audit_cursor(cursor):
    """`(created_at, id)` of an `encode_audit_cursor` string, or
    InvalidAuditCursor."""
    try:
        stamp, row_id = str(cursor).split(".", 1)
        if not stamp.isdigit() or len(stamp) > 18:
            raise ValueError("bad timestamp")
        created_at = _EPOCH + timedelta(microseconds=int(stamp))
        row_id = str(uuid.UUID(row_id))
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidAuditCursor(str(exc)) from exc
    return created_at, row_id


def _before(queryset, cursor):
    if cursor is None:
        return queryset
    created_at, row_id = cursor
    return queryset.filter(Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=row_id))


def _event_details(metadata) -> dict:
    details = {}
    for key, value in (metadata or {}).items():
        if key not in AUDIT_DETAIL_KEYS:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            details[key] = value if not isinstance(value, str) else value[:100]
        elif isinstance(value, list):
            details[key] = [str(item)[:100] for item in value[:16]]
    return details


def _event_row(row) -> dict:
    return {
        "kind": "event",
        "id": str(row.pk),
        "created_at": row.created_at,
        "action": row.action,
        "actor_type": row.actor_type,
        "details": _event_details(row.metadata),
    }


def node_audit_events(actor, node):
    """The node's AuditLog rows the actor may see: rows naming the node
    (as their resource, or `node_id` / `device_id` / `node_device_id` in
    their metadata) and the owner's kill-switch rows, in the actor's
    workspaces (`for_actor()`) or workspace-less rows of the actor (or the
    system). Another user's refused probe of this node id is theirs, not
    listed."""
    node_id = str(node.pk)
    visible = Workspace.objects.for_actor(actor).values("pk")
    about_node = (
        Q(resource_id=node_id)
        | Q(metadata__node_id=node_id)
        | Q(metadata__device_id=node_id)
        | Q(metadata__node_device_id=node_id)
        | Q(action__in=_AUDIT_OWNER_WIDE_ACTIONS, actor=node.owner)
    )
    scope = (
        Q(workspace__in=visible)
        | Q(workspace__isnull=True, actor__isnull=True)
        | Q(workspace__isnull=True, actor=actor)
    )
    return (
        AuditLog.objects.filter(about_node & scope)
        .exclude(action__in=_AUDIT_EXCLUDED_ACTIONS)
        .order_by("-created_at", "-id")
    )


def node_audit_page(actor, node, *, before=None, limit=AUDIT_PAGE_SIZE):
    """The node's audit (D8): its chain rows (`kind: "invocation"`, as
    `invocation_page` shows them) merged with its AuditLog rows (`kind:
    "event"`, codes and keys only), newest first, `limit` a page. `before`
    is a decoded cursor `(created_at, id)`. Returns `(rows, next_cursor)`."""
    limit = max(1, int(limit))
    chain = _before(
        NodeInvocation.objects.for_actor(actor).filter(node=node).select_related("workspace"),
        before,
    ).order_by("-created_at", "-id")[: limit + 1]
    events = _before(node_audit_events(actor, node), before)[: limit + 1]
    merged = [(row.created_at, str(row.pk), "invocation", row) for row in chain] + [
        (row.created_at, str(row.pk), "event", row) for row in events
    ]
    merged.sort(key=lambda item: (item[0], item[1]), reverse=True)
    page = merged[:limit]
    rows = [
        {"kind": "invocation", **_invocation_row(row)} if kind == "invocation" else _event_row(row)
        for _, _, kind, row in page
    ]
    next_cursor = None
    if len(merged) > limit:
        last = page[-1]
        next_cursor = encode_audit_cursor(last[0], last[1])
    return rows, next_cursor


# --- the read tools' node (Phase 9 D9, branch 5) ---


def read_node_for(owner, *, now=None):
    """The node the read tools (`tools.node_reads`) read from for `owner`:
    a usable one (not revoked, killed or expired, D1), a live one first,
    else the most recently paired usable one (a direct call then ends
    `node_offline`, with its chain row). None when the owner has no usable
    node. A system lookup by owner: the callers have already checked that
    the actor is that owner (or, for `is_available`, offer nothing more
    than a tool name)."""
    if owner is None or getattr(owner, "pk", None) is None:
        return None
    now = now or timezone.now()
    usable = [
        node
        for node in Node.objects.filter(owner=owner)
        .select_related("device", "device__user", "workspace", "owner")
        .order_by("-created_at")
        if node_device_refusal(node.device, now) is None
    ]
    for node in usable:
        if presence.is_online(node):
            return node
    return usable[0] if usable else None


def invocable_capabilities(node) -> frozenset:
    """The names of the read capabilities the node's latest snapshot marks
    invocable (matching contract, granted on the Mac)."""
    snapshot = latest_snapshot(node)
    if snapshot is None:
        return frozenset()
    return frozenset(
        entry.get("name")
        for entry in snapshot.entries or ()
        if entry.get("invocable") and entry.get("class") == "read"
    )
