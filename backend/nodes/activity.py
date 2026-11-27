"""`Node.last_active_at`: when the owner demonstrably used a PC (Phase 13
D6). The "last active" rule of `nodes.targeting` reads it.

Written (at most once a minute per node, like `last_heartbeat_at`) only
for evidence of the owner AT that PC:

- a node-relayed Claude Code hook batch (`nodes.events`, the owner's coding
  activity on it);
- a `node_voice` transcription request made with that node's voice token
  (`voice` views, through the callable `config/urls.py` injects);
- a click on that node's own confirmation window (`nodes.services.
  record_gated_progress`: `approved` / `denied`).

Heartbeats never count: every online node heartbeats. The value is
node-influenced (a compromised node can relay hook events), so it only ever
decides between candidates nobody named: an explicit mention or the speaking
node always wins over it (`nodes.targeting`, rule order), and the approval
card and the node's own confirmation window still name the target.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from nodes.models import Node

# At most one write per node per minute.
LAST_ACTIVE_MIN_INTERVAL_SECONDS = 60


def mark_node_active(node_id, *, now=None) -> bool:
    """Bump the node's `last_active_at` to `now` unless it was written in
    the last minute. Returns whether a row was written. An unknown id writes
    nothing."""
    if node_id is None:
        return False
    now = now or timezone.now()
    since = now - timedelta(seconds=LAST_ACTIVE_MIN_INTERVAL_SECONDS)
    try:
        written = (
            Node.objects.filter(pk=node_id)
            .filter(Q(last_active_at__isnull=True) | Q(last_active_at__lte=since))
            .update(last_active_at=now)
        )
    except (ValidationError, ValueError, TypeError):
        return False
    return bool(written)
