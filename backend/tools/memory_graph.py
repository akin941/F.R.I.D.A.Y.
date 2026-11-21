"""`memory_graph_lookup` -- the read tool behind "ne zamandan beri?" and
"hangi modül `memory.graph`'ı import ediyor?" (Phase 8B brief D8, branch 4).

An ungated `ReadToolSpec` (`data_class=firm`, `CHANNEL_WEB` only) over the
temporal graph of the conversation's workspace. `tools` may import `memory`;
`memory` never imports `tools`. The read:

- runs over the web channel for a human installation owner who is a member
  of the workspace (`tools.owner_reads.require_owner_web_reader`), anything
  else is a `ReadToolError`;
- resolves `entity` (<= 200 chars) by normalised name or alias among the
  workspace's live entities, through `for_actor()` -- another workspace's
  entities are never matched;
- walks `neighbourhood(..., hops, as_of, include_system=True,
  include_private=False)`: Graphify `system` facts are included (this tool
  and the deterministic `self` summary are the only model paths that read
  them), `private` facts never are;
- answers exactly "not found" for an entity that `entity_is_private` (D4),
  the same text as a missing entity, so the name's existence is never
  confirmed;
- returns plain lines (`statement (valid_from–valid_to)`) that the chat
  turn fences as untrusted data (`assistant.context.render_connector_block`)
  and that go to whatever provider serves the turn;
- writes one `memory.graph.read` audit row per call (every outcome), ids
  and counts only.
"""

from datetime import timedelta

from django.db.models import Q
from rest_framework import serializers

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.context import render_fact_line
from memory.graph.candidates import normalise_name
from memory.graph.selectors import neighbourhood
from memory.graph.services import as_instant
from memory.models import MemoryEntity, MemoryFact
from tools.owner_reads import MAX_RESULT_CHARS, require_owner_web_reader, validate_args
from tools.registry import (
    DATA_CLASS_FIRM,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    register_read_tool,
)

TOOL_NAME = "memory_graph_lookup"
SOURCE_LABEL = "friday_graph"
AUDIT_ACTION = "memory.graph.read"
NOT_FOUND_TEXT = "No facts found for that entity in this workspace."
MAX_ENTITY_CHARS = 200
MAX_SEED_ENTITIES = 10


class MemoryGraphLookupArgs(serializers.Serializer):
    """Strict arguments: an entity name, an optional date, 1 or 2 hops."""

    entity = serializers.CharField(max_length=MAX_ENTITY_CHARS, trim_whitespace=True)
    as_of = serializers.DateField(required=False, allow_null=True, default=None)
    hops = serializers.IntegerField(required=False, min_value=1, max_value=2, default=1)


def _as_of_instant(day):
    """The end of `day` (local time): facts valid at any point that day."""
    if day is None:
        return None
    return as_instant(day + timedelta(days=1)) - timedelta(microseconds=1)


def matching_entities(*, workspace, actor, name):
    """Live, non-private entities of the workspace whose normalised name or
    alias equals `name` (learned and Graphify alike)."""
    normalised = normalise_name(name)
    if not normalised:
        return []
    return list(
        MemoryEntity.objects.for_actor(actor, workspace=workspace)
        .filter(Q(normalised_name=normalised) | Q(aliases__contains=[normalised]))
        .with_privacy()
        .filter(is_private=False)
        .order_by("kind", "normalised_name", "id")[:MAX_SEED_ENTITIES]
    )


def _line(fact):
    marker = " [code]" if fact.origin == MemoryFact.Origin.GRAPHIFY else ""
    return f"- {render_fact_line(fact)}{marker}"


def _bounded(lines):
    full = "\n".join(lines)
    if len(full) <= MAX_RESULT_CHARS:
        return full, len(full), False
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > MAX_RESULT_CHARS:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept), len(full), True


def read_memory_graph(*, workspace, actor, args, channel):
    audit = {"outcome": "ok", "count": 0, "size": 0, "entity_ids": [], "fact_ids": []}
    try:
        require_owner_web_reader(workspace=workspace, actor=actor, channel=channel)
        data = validate_args(MemoryGraphLookupArgs, args)
        entities = matching_entities(workspace=workspace, actor=actor, name=data["entity"])
        facts = []
        if entities:
            facts = [
                fact
                for fact in neighbourhood(
                    [e.pk for e in entities],
                    workspace=workspace,
                    hops=data["hops"],
                    as_of=_as_of_instant(data["as_of"]),
                    include_system=True,
                    include_private=False,
                )
                # Defense in depth behind the CTE's own predicate.
                if fact.sensitivity == MemoryFact.Sensitivity.NORMAL
            ]
        if not facts:
            audit["outcome"] = "not_found"
            return ReadResult(text=NOT_FOUND_TEXT, size=len(NOT_FOUND_TEXT))
        text, size, truncated = _bounded([_line(f) for f in facts])
        audit.update(
            {
                "count": len(facts),
                "size": size,
                "truncated": truncated,
                "entity_ids": [str(e.pk) for e in entities],
                "fact_ids": [str(f.pk) for f in facts],
                "hops": data["hops"],
                "as_of": data["as_of"].isoformat() if data["as_of"] else None,
            }
        )
        return ReadResult(text=text, size=size, truncated=truncated)
    except ReadToolError as exc:
        audit["outcome"] = exc.code
        raise
    finally:
        record_audit(
            action=AUDIT_ACTION,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            workspace=workspace,
            metadata={"tool": TOOL_NAME, "channel": channel, **audit},
        )


def memory_graph_available(workspace):
    """Offered only where the workspace holds a current fact."""
    workspace_id = getattr(workspace, "id", None)
    if workspace_id is None:
        return False
    return MemoryFact.objects.filter(workspace_id=workspace_id).current().exists()


SPEC = ReadToolSpec(
    name=TOOL_NAME,
    description=(
        "the workspace's knowledge graph: the current (or as-of-date) facts "
        "about one named entity -- a service, repo, firm, project, tool or code "
        "module -- with since-when validity windows, and its neighbours up to two hops"
    ),
    argument_serializer=MemoryGraphLookupArgs,
    data_class=DATA_CLASS_FIRM,
    channels=frozenset({CHANNEL_WEB}),
    execute=read_memory_graph,
    is_available=memory_graph_available,
    source=SOURCE_LABEL,
)


def _register():
    register_read_tool(SPEC)
