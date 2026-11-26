"""Node-relayed events (Phase 9 brief D10).

A node sends `event` frames of two kinds; the gateway answers each with an
`event.ack` holding **counts only** (`stored` / `duplicate` / `dropped`),
never content, and never with anything else: there is no path from an
`event` to an `invoke` (nothing here touches the invoke bus).

- **`claude_code.hook`**: a batch (<= 50, the protocol schema) in the same
  per-event shape as 8A's ingest endpoint. It is handed to the sink the
  composition root registered at start-up (`config.asgi_nodes` registers
  `memory.ingestion.hooks.ingest_hook_events`; `nodes` imports no `memory`,
  ADR-013 decision 9). The sink is called with the node's owner and the
  node's (personal) workspace: the same principal binding as the `hook`
  PAT, so the 8A mapping rules alone decide each event's workspace, an
  unmapped `cwd` stores nothing, and an event relayed by both `friday-hook`
  and the node dedupes on its event id (`duplicate`). With no sink
  registered (a test settings module, a misconfigured process) the batch is
  acked `dropped` and stored nowhere.
- **`grant.availability_changed`**: read-only data. Each item is
  `{name, version, availability}`; the node's latest `CapabilitySnapshot`
  gets a successor row with those entries' availability (and the derived
  invocable / `not_invocable` verdict) changed. Nothing here writes node
  policy: availability is what the owner chose on the Mac (ADR-013
  decision 5), and the node still refuses an ungranted read itself (D5
  check 16).
- **`voice.state`** (Phase 11 D15, protocol v2 only): the node's mic state
  as its voice child reports it (one item: `state`, `mode`,
  `session_open`, three daily counters, `at`; the v2 event schema's
  `$defs.voice_state_item`). It is `nodes`' own data, like availability:
  stored on the `Node` row (`voice_*` fields, the server's receive time in
  `voice_state_at`), never handed to a sink, never touching `voice`.
  **Coalesced and rate-limited per node**: at most one write per node per
  `VOICE_STATE_MIN_INTERVAL_SECONDS`; a frame inside that second is acked
  `duplicate` (detail `voice_state_coalesced`) and writes nothing. The
  node itself coalesces to <= 1/s with the latest state winning, so an
  honest node's latest state is always the one written. Nothing here can
  arm, mute or change the node: the server -> node set is frozen.

Phase 13 D6: a non-empty hook batch the sink accepted also bumps the node's
`last_active_at` (`nodes.activity`, at most once a minute); no other kind
does.

Spooled uploads (`spooled: true`, after a reconnect) are paced by
`SpoolThrottle` (10 batches/s per node); the gateway handles one socket's
frames in order and acks each before reading the next, so batches are
applied in the order the node sent them.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from nodes.activity import mark_node_active
from nodes.models import CapabilitySnapshot, Node
from nodes.protocol.messages import (
    AVAILABILITY,
    MAX_EVENT_BATCH,
    MAX_VOICE_COUNTER,
    MAX_VOICE_STATE_AT,
    VOICE_MODES,
    VOICE_STATE_COUNTERS,
    VOICE_STATE_ITEM_KEYS,
    VOICE_STATE_KIND,
    VOICE_STATE_MIN_INTERVAL_SECONDS,
    VOICE_STATES,
)

logger = logging.getLogger(__name__)

HOOK_EVENTS = "claude_code.hook"
AVAILABILITY_CHANGED = "grant.availability_changed"
VOICE_STATE = VOICE_STATE_KIND
# Kinds a composition root may register a sink for. Availability changes and
# voice states are `nodes`' own data and never leave it.
SINK_KINDS = frozenset({HOOK_EVENTS})

SPOOLED_BATCHES_PER_SECOND = 10

# Verdicts an availability change may move between; any other
# `not_invocable` reason (unknown capability, schema mismatch, ...) stays.
_AVAILABILITY_REASONS = {"granted": None, "not_granted": "not_granted"}
_MOVABLE_REASONS = frozenset({None, "not_granted", "unsupported"})
_AVAILABILITY_ITEM_KEYS = frozenset({"name", "version", "availability"})
MAX_NAME_CHARS = 64

_sinks: dict = {}


class SinkFailed(Exception):
    """The sink raised (a database error, say). The batch is not acked, so
    the node keeps it (in flight, then spooled after a reconnect) and sends
    it again; the server dedupes on the event id."""


def register_sink(kind, fn) -> None:
    """Register the sink for `kind` (composition root only; a source test
    pins `config.asgi_nodes` as the only caller). `fn(*, user,
    bound_workspace_id, events)` returns 8A-style counts."""
    if kind not in SINK_KINDS:
        raise ValueError(f"no sink can be registered for event kind {kind!r}")
    if not callable(fn):
        raise TypeError("an event sink must be callable")
    _sinks[kind] = fn


def reset_sinks() -> None:
    """Tests only: forget every registered sink."""
    _sinks.clear()


def sink_registered(kind) -> bool:
    return kind in _sinks


@dataclass(frozen=True)
class EventOutcome:
    """What an `event.ack` reports (`stored`, `duplicate`, `dropped`) plus
    `detail`: server-side counts by reason (for the gateway's counters and
    logs). Counts only, never content."""

    stored: int = 0
    duplicate: int = 0
    dropped: int = 0
    detail: dict = field(default_factory=dict)


def _bounded(value, size) -> int:
    return max(0, min(int(value or 0), size))


def handle_event(*, node, frame) -> EventOutcome:
    """Apply one (schema-valid) `event` frame of `node`. Raises SinkFailed
    when the registered sink raised."""
    batch = list(frame.get("batch") or ())[:MAX_EVENT_BATCH]
    kind = frame.get("kind")
    if kind == HOOK_EVENTS:
        return _hook_events(node, batch)
    if kind == AVAILABILITY_CHANGED:
        return apply_availability_change(node, batch)
    if kind == VOICE_STATE:
        return apply_voice_state(node, batch)
    return EventOutcome(dropped=len(batch), detail={"unknown_kind": len(batch)})


def _hook_events(node, batch) -> EventOutcome:
    sink = _sinks.get(HOOK_EVENTS)
    if sink is None:
        return EventOutcome(dropped=len(batch), detail={"no_sink": len(batch)})
    try:
        counts = sink(user=node.owner, bound_workspace_id=node.workspace_id, events=batch)
    except Exception as exc:
        logger.exception("node event sink failed node=%s kind=%s", node.pk, HOOK_EVENTS)
        raise SinkFailed(HOOK_EVENTS) from exc
    counts = counts if isinstance(counts, dict) else {}
    size = len(batch)
    if size:
        # Phase 13 D6: the owner's Claude Code activity on this PC makes it
        # the "last active" one (at most one write a minute; a heartbeat
        # never does).
        mark_node_active(node.pk)
    stored = _bounded(counts.get("stored"), size)
    duplicate = _bounded(counts.get("duplicate"), size - stored)
    detail = {
        key: int(value)
        for key, value in counts.items()
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
    }
    return EventOutcome(
        stored=stored, duplicate=duplicate, dropped=size - stored - duplicate, detail=detail
    )


def _availability_item(item):
    """`(name, version, availability)` of one valid item, else None."""
    if not isinstance(item, dict) or set(item) != _AVAILABILITY_ITEM_KEYS:
        return None
    name, version, availability = item["name"], item["version"], item["availability"]
    if not isinstance(name, str) or not 0 < len(name) <= MAX_NAME_CHARS:
        return None
    if isinstance(version, bool) or not isinstance(version, int) or not 0 < version < 1000:
        return None
    if availability not in AVAILABILITY:
        return None
    return name, version, availability


def _verdict(entry, availability):
    """`(invocable, reason)` of `entry` once its availability is
    `availability`. Only an availability verdict moves; any other reason
    (the entry does not match the server's contract) stays."""
    reason = entry.get("reason")
    if reason not in _MOVABLE_REASONS:
        return False, reason
    if reason is None and not entry.get("invocable"):
        return False, reason
    new_reason = _AVAILABILITY_REASONS.get(availability, "unsupported")
    return new_reason is None, new_reason


def apply_availability_change(node, batch) -> EventOutcome:
    """D10 `grant.availability_changed`: a successor of the node's latest
    snapshot with the named entries' availability changed. `stored` counts
    the entries that changed, `duplicate` those already in that state,
    `dropped` an invalid item or one naming no entry of the snapshot. No
    new row when nothing changed."""
    latest = CapabilitySnapshot.objects.filter(node=node).order_by("-created_at").first()
    if latest is None:
        return EventOutcome(dropped=len(batch), detail={"no_snapshot": len(batch)})
    entries = [dict(entry) for entry in latest.entries or ()]
    index = {(entry.get("name"), entry.get("version")): entry for entry in entries}
    stored = duplicate = dropped = 0
    for item in batch:
        parsed = _availability_item(item)
        entry = index.get(parsed[:2]) if parsed is not None else None
        if entry is None:
            dropped += 1
            continue
        availability = parsed[2]
        if entry.get("availability") == availability:
            duplicate += 1
            continue
        invocable, reason = _verdict(entry, availability)
        entry.update(availability=availability, invocable=invocable, reason=reason)
        stored += 1
    if stored:
        CapabilitySnapshot.objects.create(
            workspace=latest.workspace,
            node=node,
            catalogue_sha256=latest.catalogue_sha256,
            entries=entries,
            node_version=latest.node_version,
            os_version=latest.os_version,
        )
        logger.info("node availability changed node=%s entries=%d", node.pk, stored)
    return EventOutcome(
        stored=stored,
        duplicate=duplicate,
        dropped=dropped,
        detail={"availability_changed": stored},
    )


def _voice_state_item(item):
    """The item when it is exactly a valid `voice.state` item, else None
    (the schema already checked it; this is the defensive second look)."""
    if not isinstance(item, dict) or set(item) != VOICE_STATE_ITEM_KEYS:
        return None
    if item["state"] not in VOICE_STATES or item["mode"] not in VOICE_MODES:
        return None
    if not isinstance(item["session_open"], bool):
        return None
    for key in (*VOICE_STATE_COUNTERS, "at"):
        value = item[key]
        ceiling = MAX_VOICE_STATE_AT if key == "at" else MAX_VOICE_COUNTER
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
            return None
    return item


def apply_voice_state(node, batch, *, now=None) -> EventOutcome:
    """D15 `voice.state`: store the (single) item on the node row, at most
    once per node per second. `stored` 1 when written, `duplicate` 1 when
    coalesced into the write of the same second, `dropped` for an invalid
    item or a batch of more than one (the schema allows exactly one)."""
    if len(batch) != 1:
        return EventOutcome(dropped=len(batch), detail={"voice_state_invalid": len(batch)})
    item = _voice_state_item(batch[0])
    if item is None:
        return EventOutcome(dropped=1, detail={"voice_state_invalid": 1})
    now = now or timezone.now()
    since = now - timedelta(seconds=VOICE_STATE_MIN_INTERVAL_SECONDS)
    written = (
        Node.objects.filter(pk=node.pk)
        .filter(Q(voice_state_at__isnull=True) | Q(voice_state_at__lte=since))
        .update(
            voice_state=item["state"],
            voice_mode=item["mode"],
            voice_session_open=item["session_open"],
            voice_state_at=now,
            voice_wake_accepts_today=item["wake_accepts_today"],
            voice_wake_discards_today=item["wake_discards_today"],
            voice_storm_mutes_today=item["storm_mutes_today"],
        )
    )
    if not written:
        return EventOutcome(duplicate=1, detail={"voice_state_coalesced": 1})
    return EventOutcome(stored=1, detail={"voice_state": 1})


class SpoolThrottle:
    """D10: spooled batches are paced to `rate` per second per node.
    `delay()` returns how long to wait before handling the next spooled
    batch (0 when it may go now) and books its slot."""

    def __init__(self, rate=SPOOLED_BATCHES_PER_SECOND, clock=time.monotonic):
        self.rate = rate
        self.clock = clock
        self._slots: deque = deque()

    def delay(self) -> float:
        now = self.clock()
        while self._slots and self._slots[0] <= now - 1.0:
            self._slots.popleft()
        wait = 0.0
        if len(self._slots) >= self.rate:
            wait = self._slots[0] + 1.0 - now
        self._slots.append(now + wait)
        return max(0.0, wait)
