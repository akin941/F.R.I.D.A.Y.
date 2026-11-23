"""Node presence and heartbeat bookkeeping (Phase 9 brief D6).

- **Presence** is the Redis key `nodes:presence:<node_id>` (`nodes.bus`),
  TTL 45 s = three missed heartbeats. The gateway sets it on `hello` and
  refreshes it on every `heartbeat`; it clears it when the socket closes.
  `is_online(node)` is what the read tools' `is_available`, the node APIs
  and the HUD read: a Redis outage reads as offline (fail closed).
- **`Node.last_heartbeat_at`** (and the spool depth the heartbeat reports)
  is written at most once per `HEARTBEAT_WRITE_INTERVAL_SECONDS` (60), so a
  node heartbeating every 15 s costs one row update a minute, not four.
- **`node_version` / `os_version`** from `hello` are stored on the row when
  they change (they are bounded by the `hello` schema).
"""

from datetime import timedelta

from django.utils import timezone

from nodes import bus
from nodes.models import Node

HEARTBEAT_WRITE_INTERVAL_SECONDS = 60


def is_online(node) -> bool:
    """The node holds a live presence key. False when Redis is down."""
    try:
        return bus.is_present(node.pk)
    except bus.BusUnavailable:
        return False


def record_hello(node, *, node_version="", os_version="", now=None) -> None:
    """Store the versions the node reported in `hello` (when they changed)
    and count the hello as a heartbeat for `last_heartbeat_at`."""
    now = now or timezone.now()
    Node.objects.filter(pk=node.pk).update(
        node_version=str(node_version or "")[:64],
        os_version=str(os_version or "")[:64],
        last_heartbeat_at=now,
        updated_at=now,
    )
    node.node_version = str(node_version or "")[:64]
    node.os_version = str(os_version or "")[:64]
    node.last_heartbeat_at = now


def record_heartbeat(node, *, spool_count=0, spool_bytes=0, now=None) -> bool:
    """Write `last_heartbeat_at` and the spool depth when the last write is
    older than the interval. Returns whether a row was written."""
    now = now or timezone.now()
    last = node.last_heartbeat_at
    if last is not None and now - last < timedelta(seconds=HEARTBEAT_WRITE_INTERVAL_SECONDS):
        return False
    Node.objects.filter(pk=node.pk).update(
        last_heartbeat_at=now,
        spool_count=max(0, int(spool_count)),
        spool_bytes=max(0, int(spool_bytes)),
        updated_at=now,
    )
    node.last_heartbeat_at = now
    node.spool_count = max(0, int(spool_count))
    node.spool_bytes = max(0, int(spool_bytes))
    return True
