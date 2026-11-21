"""The transcript pull (Phase 8B branch 8, brief D13): owner turns of Claude
Code and Codex sessions, read from the owner's node and handed to the
memory port `memory.ingestion.transcripts.ingest_owner_turns`.

It lives in `tools`, the app allowed to reach both the node layer and
`memory`; `memory.ingestion` reaches neither (an import-linter contract).
Run by the worker beat every `MEMORY_TRANSCRIPT_PULL_MINUTES` (15).

For each tool (`claude`, `codex`) it runs only when the tool's transcript
source is accepting **and** the owner's node advertises the capability as
invocable and granted. Then, for each workspace the owner owns that has at
least one hooks `path_prefix` rule (the one path registry, Phase 9 D9), it
makes **one** read invoke with exactly that workspace's prefixes:

- **Actor: the system-owner form, never None.** `actor` = the node's
  owner, `channel` = `CHANNEL_SYSTEM`, and the call is refused before
  anything is sent (`server_refused:actor_not_owner`) unless the actor is
  active, owns the node and owns the target workspace; `CHANNEL_SYSTEM` is
  accepted only for `SYSTEM_READ_CAPABILITIES` (`server_refused:
  not_invocable` otherwise). Every invoke -- sent or refused -- writes one
  `AuditLog` row (`memory.transcript_pull.invoked`, `system: true`, the job
  name, counts only).
- **The `since` clamp.** The contract refuses a `since` older than now - 7
  days, so `since = max(cursor, now - 7 days + 5 minutes)`; the first run
  (no cursor) starts at `max(source.enabled_at, now - 7 days + 5 minutes)`.
  A clamped stale cursor counts `since_clamped`, sets `last_clamped_at`
  and audits the skipped window: it is lost by design.
- **The cursor** (`memory.TranscriptCursor`) advances only after the
  episodes commit, to the latest returned `at` (server-derived: a hostile
  `next_since` cannot skip ahead). An empty result leaves it unchanged; a
  failed or refused invoke leaves it unchanged and is not retried in the
  same beat. A truncated result whose last `at` equals `since` (one
  timestamp filled the cap) moves it on by 1 ms and counts `tie_overflow`.

**The node seam.** Phase 9 branches 3-5 (`nodes.services.invoke_read`, the
gateway and the reference node) have not merged yet, so the node layer is
reached through `NodeReadPort`. The default port (`UnavailableNodePort`)
has no node, so the beat is a no-op; tests install the Python reference
node double (`tools/tests/transcript_nodes.py`). When Phase 9 lands, a
`nodes`-backed port (signed, chain-audited `invoke_read` with the
`channel` keyword and the actor check of the recorded D7/D9 amendment)
replaces the default -- see the brief's owner steps.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_SYSTEM, CHANNEL_WEB
from memory.ingestion import transcripts
from memory.ingestion.mapping import owner_owns, prefixes_for_workspace
from memory.ingestion.services import owner_workspaces
from memory.models import IngestionSource, TranscriptCursor
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

JOB_NAME = "transcript_pull"
AUDIT_ACTION = "memory.transcript_pull.invoked"
CAPABILITIES = {
    transcripts.TOOL_CLAUDE: "claude.transcripts.owner_turns",
    transcripts.TOOL_CODEX: "codex.transcripts.owner_turns",
}
# The only capabilities a `CHANNEL_SYSTEM` read may name (D13 amendment).
SYSTEM_READ_CAPABILITIES = frozenset(CAPABILITIES.values())
# Personal-class capabilities: the actor check applies (D13 amendment).
PERSONAL_READ_CAPABILITIES = SYSTEM_READ_CAPABILITIES | {
    "claude.sessions.list",
    "claude.sessions.read",
    "codex.threads.list",
    "codex.threads.read",
}

MAX_SINCE_AGE = timedelta(days=7)
SINCE_MARGIN = timedelta(minutes=5)
TIE_STEP = timedelta(milliseconds=1)
MAX_SESSIONS = 50
MAX_TURNS = 200

SENT = "ok"
FAILED = "failed"
SERVER_REFUSED = "server_refused"
NODE_REFUSED = "node_refused"
ACTOR_NOT_OWNER = "actor_not_owner"
NOT_INVOCABLE = "not_invocable"


# --- the node seam -------------------------------------------------------------


@dataclass(frozen=True)
class NodeHandle:
    """The owner's node: `ref` is its stable reference string (the cursor
    key) and `owner` the user of its `TrustedDevice`."""

    ref: str
    owner: object


@dataclass(frozen=True)
class ReadOutcome:
    status: str  # "ok" | "node_refused" | "failed" | "server_refused"
    output: dict | None = None
    code: str = ""
    invocation_id: str | None = None


class NodeReadPort(Protocol):
    def owner_node(self, owner) -> NodeHandle | None: ...

    def capability_granted(self, node: NodeHandle, capability: str) -> bool: ...

    def invoke_read(
        self, *, node, capability, args, workspace, actor, channel, trace_id
    ) -> ReadOutcome: ...


class UnavailableNodePort:
    """The default until Phase 9 branches 3-5 merge: no node is reachable,
    so nothing is invoked."""

    def owner_node(self, owner):
        return None

    def capability_granted(self, node, capability):
        return False

    def invoke_read(self, **kwargs):  # pragma: no cover - never reached
        return ReadOutcome(FAILED, code="no_node_layer")


_port: NodeReadPort = UnavailableNodePort()


def get_node_port() -> NodeReadPort:
    return _port


def set_node_port(port: NodeReadPort) -> NodeReadPort:
    """Install `port`; returns the previous one (tests restore it)."""
    global _port
    previous, _port = _port, port
    return previous


def refusal_for(*, node, capability, actor, workspace, channel) -> str | None:
    """The D13 amendment checks, run before anything is signed or sent:
    `not_invocable` for a system-channel read outside
    `SYSTEM_READ_CAPABILITIES`; `actor_not_owner` for a personal-class
    capability whose actor is missing, inactive, not the node's owner, or
    not an owner of the target workspace. None when the call may go out.
    The `nodes`-backed port re-runs the same checks in `invoke_read`."""
    if channel not in (CHANNEL_SYSTEM, CHANNEL_WEB):
        return NOT_INVOCABLE
    if channel == CHANNEL_SYSTEM and capability not in SYSTEM_READ_CAPABILITIES:
        return NOT_INVOCABLE
    if capability in PERSONAL_READ_CAPABILITIES:
        if actor is None or not getattr(actor, "is_active", False):
            return ACTOR_NOT_OWNER
        if node is None or getattr(node.owner, "pk", None) != actor.pk:
            return ACTOR_NOT_OWNER
        if workspace is None or not owner_owns(actor, workspace):
            return ACTOR_NOT_OWNER
    return None


# --- the pull ------------------------------------------------------------------


@dataclass
class PullReport:
    invokes: int = 0
    refused: int = 0
    failed: int = 0
    stored: int = 0
    skipped: list = None

    def __post_init__(self):
        self.skipped = self.skipped or []


def _floor(now):
    return now - MAX_SINCE_AGE + SINCE_MARGIN


def compute_since(cursor, source, now):
    """`(since, clamped_from)`: the instant to send and, when the 7-day
    bound moved a stale cursor forward, the cursor's own value (the start of
    the skipped window)."""
    floor = _floor(now)
    if cursor is None:
        start = source.enabled_at or floor
        return max(start, floor), None
    if cursor.since < floor:
        return floor, cursor.since
    return cursor.since, None


def _returned_turns(output, since, now):
    """Every turn of a result whose `at` parses, is `>= since` and not in
    the future (server-derived; used for the cursor and the counts)."""
    instants = []
    if not isinstance(output, dict) or not isinstance(output.get("sessions"), list):
        return instants
    latest = now + SINCE_MARGIN
    for session in output["sessions"]:
        turns = session.get("turns") if isinstance(session, dict) else None
        for turn in turns if isinstance(turns, list) else []:
            at = transcripts.parse_instant(turn.get("at")) if isinstance(turn, dict) else None
            if at is not None and since <= at <= latest:
                instants.append(at)
    return instants


def _bounded_sessions(output):
    """Only the contract's keys survive: a hostile node's aggregates or
    extra fields are dropped (counts are derived server-side, Phase 9 D7)."""
    sessions = output.get("sessions") if isinstance(output, dict) else None
    if not isinstance(sessions, list):
        return [], 0
    extra = len(set(output) - {"sessions", "next_since", "truncated"})
    kept = []
    for session in sessions[:MAX_SESSIONS]:
        if not isinstance(session, dict):
            continue
        extra += len(set(session) - {"session_id", "cwd", "turns"})
        turns = session.get("turns") if isinstance(session.get("turns"), list) else []
        kept.append(
            {
                "session_id": session.get("session_id"),
                "cwd": session.get("cwd"),
                "turns": [
                    {k: t.get(k) for k in ("turn_id", "at", "text")}
                    for t in turns[:MAX_TURNS]
                    if isinstance(t, dict)
                ],
            }
        )
    return kept, extra


def _audit(*, owner, workspace, capability, node, trace_id, metadata):
    record_audit(
        action=AUDIT_ACTION,
        actor=owner,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        metadata={
            "system": True,
            "job": JOB_NAME,
            "channel": CHANNEL_SYSTEM,
            "capability": capability,
            "node": node.ref,
            "trace_id": trace_id,
            **metadata,
        },
    )


def _pull_one(port, *, tool, source, node, workspace, prefixes, now, report):
    # The actor is the transcript source's owner (the installation owner);
    # `refusal_for` requires the node to be theirs too.
    owner = source.workspace.owner
    capability = CAPABILITIES[tool]
    cursor = TranscriptCursor.objects.filter(
        source=source, target_workspace=workspace, node_ref=node.ref
    ).first()
    since, clamped_from = compute_since(cursor, source, now)
    args = {
        "since": transcripts.format_instant(since),
        "cwd_prefixes": prefixes,
        "max_sessions": MAX_SESSIONS,
        "max_turns": MAX_TURNS,
    }
    # The sent value is millisecond-precise; compare against what was sent.
    since = transcripts.parse_instant(args["since"])
    trace_id = uuid.uuid4().hex
    meta = {"since": args["since"], "prefixes": len(prefixes), "since_clamped": False}
    if clamped_from is not None:
        meta["since_clamped"] = True
        meta["skipped_window"] = {
            "from": transcripts.format_instant(clamped_from),
            "to": args["since"],
        }
        TranscriptCursor.objects.filter(pk=cursor.pk).update(
            since_clamped=cursor.since_clamped + 1, last_clamped_at=now
        )

    refusal = refusal_for(
        node=node, capability=capability, actor=owner, workspace=workspace, channel=CHANNEL_SYSTEM
    )
    if refusal is not None:
        report.refused += 1
        _audit(
            owner=owner,
            workspace=workspace,
            capability=capability,
            node=node,
            trace_id=trace_id,
            metadata={**meta, "status": SERVER_REFUSED, "code": refusal},
        )
        return
    report.invokes += 1
    try:
        outcome = port.invoke_read(
            node=node,
            capability=capability,
            args=args,
            workspace=workspace,
            actor=owner,
            channel=CHANNEL_SYSTEM,
            trace_id=trace_id,
        )
    except Exception:
        logger.exception("transcript pull invoke failed (%s)", capability)
        outcome = ReadOutcome(FAILED, code="exception")
    if outcome.status != SENT:
        report.failed += 1
        _audit(
            owner=owner,
            workspace=workspace,
            capability=capability,
            node=node,
            trace_id=trace_id,
            metadata={**meta, "status": outcome.status, "code": outcome.code},
        )
        return

    sessions, extra_keys = _bounded_sessions(outcome.output)
    instants = _returned_turns({"sessions": sessions}, since, now)
    truncated = bool(isinstance(outcome.output, dict) and outcome.output.get("truncated") is True)
    result = transcripts.ingest_owner_turns(
        user=owner, tool=tool, workspace=workspace, sessions=sessions, now=now
    )
    # The episodes are committed (the port's own atomic block, run outside
    # any outer transaction by the beat); only now does the cursor move.
    tie_overflow = False
    advanced = None
    if instants:
        advanced = max(instants)
        if truncated and advanced <= since:
            advanced = since + TIE_STEP
            tie_overflow = True
        with transaction.atomic():
            row, _ = TranscriptCursor.objects.select_for_update().get_or_create(
                source=source,
                target_workspace=workspace,
                node_ref=node.ref,
                defaults={"workspace": source.workspace, "since": advanced},
            )
            row.since = max(row.since, advanced)
            if tie_overflow:
                row.tie_overflow += 1
            row.save(update_fields=["since", "tie_overflow", "updated_at"])
    report.stored += result.stored
    _audit(
        owner=owner,
        workspace=workspace,
        capability=capability,
        node=node,
        trace_id=trace_id,
        metadata={
            **meta,
            "status": SENT,
            "invocation": outcome.invocation_id,
            "turns": len(instants),
            "truncated": truncated,
            "tie_overflow": tie_overflow,
            "extra_keys_dropped": extra_keys,
            "cursor": transcripts.format_instant(advanced) if advanced else None,
            **result.as_counts(),
        },
    )


def pull_transcripts(*, now=None, port=None) -> PullReport:
    """One beat of the pull (see the module docstring)."""
    now = now or timezone.now()
    port = port or get_node_port()
    report = PullReport()
    for tool in CAPABILITIES:
        _pull_tool(port, tool=tool, now=now, report=report)
    return report


def _pull_tool(port, *, tool, now, report):
    capability = CAPABILITIES[tool]
    for source in _accepting_sources(tool):
        owner = source.workspace.owner
        node = port.owner_node(owner)
        if node is None:
            report.skipped.append((tool, "no_node"))
            continue
        if not port.capability_granted(node, capability):
            report.skipped.append((tool, "not_granted"))
            continue
        for workspace in owner_workspaces(owner).order_by("created_at"):
            prefixes = prefixes_for_workspace(owner, workspace)
            if not prefixes:
                continue
            try:
                _pull_one(
                    port,
                    tool=tool,
                    source=source,
                    node=node,
                    workspace=workspace,
                    prefixes=prefixes,
                    now=now,
                    report=report,
                )
            except Exception:
                # One workspace's failure never stops the others; the
                # cursor of the failed one is unchanged (next beat retries).
                logger.exception("transcript pull failed for one workspace (%s)", tool)
                report.failed += 1


def _accepting_sources(tool):
    sources = IngestionSource.objects.filter(
        kind=transcripts.SOURCE_KINDS[tool],
        enabled=True,
        paused_at__isnull=True,
        workspace__type=Workspace.Type.PERSONAL,
        workspace__owner__is_owner=True,
        workspace__owner__is_active=True,
    ).select_related("workspace__owner")
    return list(sources)


def pull_interval_seconds():
    return max(1, int(getattr(settings, "MEMORY_TRANSCRIPT_PULL_MINUTES", 15))) * 60
