"""The morning card (Phase 8A brief D10): last night's learned proposals,
grouped by workspace, and the owner's per-item decisions on them.

Read and decided by the installation owner only, a human over the trusted
web channel, in workspaces they own (`for_actor()` + the owner role). Every
action reuses the existing services -- `approve_memory` / `edit_memory` /
`reject_memory` for memory proposals, `approve_core_block_proposal` /
`reject_core_block_proposal` for core-block proposals -- one item at a time.
Bulk approval exists only for `work_log`-type items (d6): any other type in
a bulk request refuses the whole request.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.models import CoreBlockProposal, Memory
from memory.services import (
    InvalidTransition,
    MemoryPermissionDenied,
    approve_core_block_proposal,
    approve_memory,
    edit_memory,
    reject_core_block_proposal,
    reject_memory,
)
from workspaces.models import WorkspaceMember

MAX_DIGEST_DAYS = 14
MAX_BULK_ITEMS = 50
_AUDIT_ID_CAP = 200

KIND_MEMORY = "memory"
KIND_CORE_BLOCK = "core-block"


def require_owner_web(actor, channel):
    if channel != CHANNEL_WEB or getattr(actor, "is_agent", False):
        raise MemoryPermissionDenied("Learned proposals are decided by a person over the web.")
    if (
        not getattr(actor, "is_authenticated", False)
        or not actor.is_active
        or not getattr(actor, "is_owner", False)
    ):
        raise MemoryPermissionDenied("Only the owner decides learned proposals.")


def _owned(queryset, actor):
    return queryset.for_actor(actor).filter(
        workspace__members__user=actor, workspace__members__role=WorkspaceMember.Role.OWNER
    )


def window_start(days=None, now=None):
    days = days or settings.MEMORY_LEARNING_DIGEST_DAYS
    local = timezone.localtime(now or timezone.now())
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=days - 1)


def learned_memories(actor):
    return _owned(Memory.objects, actor).filter(ingestion_source__isnull=False)


def learned_core_block_proposals(actor):
    return _owned(CoreBlockProposal.objects, actor).filter(ingestion_source__isnull=False)


def build_digest(*, actor, channel, days=None, now=None):
    require_owner_web(actor, channel)
    since = window_start(days, now)
    memories = list(
        learned_memories(actor)
        .filter(status=Memory.Status.PENDING_APPROVAL, created_at__gte=since)
        .select_related("workspace", "ingestion_source")
        .order_by("created_at", "id")
    )
    proposals = list(
        learned_core_block_proposals(actor)
        .filter(status=CoreBlockProposal.Status.PENDING, created_at__gte=since)
        .select_related("workspace", "ingestion_source")
        .order_by("created_at", "id")
    )
    groups = {}
    for row, item in [(m, _memory_item(m)) for m in memories] + [
        (p, _proposal_item(p)) for p in proposals
    ]:
        group = groups.setdefault(
            row.workspace_id,
            {
                "workspace": {
                    "id": str(row.workspace_id),
                    "name": row.workspace.name,
                    "type": row.workspace.type,
                },
                "items": [],
            },
        )
        group["items"].append(item)
    ids = [str(m.id) for m in memories] + [str(p.id) for p in proposals]
    record_audit(
        action="memory.learning.digest_viewed",
        actor=actor,
        metadata={"item_ids": ids[:_AUDIT_ID_CAP], "count": len(ids)},
    )
    return {"since": since.isoformat(), "count": len(ids), "workspaces": list(groups.values())}


def _source_kind(row):
    return row.ingestion_source.kind if row.ingestion_source_id else None


def _memory_item(memory):
    return {
        "kind": KIND_MEMORY,
        "id": str(memory.id),
        "title": memory.title,
        "content": memory.content,
        "quote": memory.provenance_quote,
        "memory_type": memory.memory_type,
        "sensitivity": memory.sensitivity,
        "source_kind": _source_kind(memory),
        "created_at": memory.created_at.isoformat(),
        "bulk_approvable": memory.memory_type == Memory.Type.WORK_LOG,
        "actions": ["approve", "edit", "reject"],
    }


def _proposal_item(proposal):
    return {
        "kind": KIND_CORE_BLOCK,
        "id": str(proposal.id),
        "label": proposal.label,
        "title": proposal.label,
        "content": proposal.proposed_content,
        "diff": proposal.diff,
        "base_version": proposal.base_version,
        "quote": proposal.provenance_quote,
        "inferred": proposal.inferred,
        "source_kind": _source_kind(proposal),
        "created_at": proposal.created_at.isoformat(),
        "bulk_approvable": False,
        "actions": ["approve", "reject"],
    }


# --- per-item decisions ------------------------------------------------------

MEMORY_ACTIONS = {"approve", "edit", "reject"}
CORE_BLOCK_ACTIONS = {"approve", "reject"}


def find_item(actor, kind, item_id):
    """The learned item, scoped (`for_actor()` + owner role), or None."""
    if kind == KIND_MEMORY:
        return learned_memories(actor).filter(pk=item_id).first()
    if kind == KIND_CORE_BLOCK:
        return learned_core_block_proposals(actor).filter(pk=item_id).first()
    return None


def decide(*, actor, channel, kind, item, action, fields=None):
    """One owner decision on one learned item. Returns the updated row."""
    require_owner_web(actor, channel)
    fields = fields or {}
    if kind == KIND_MEMORY:
        if action == "approve":
            return approve_memory(memory=item, actor=actor, **fields)
        if action == "edit":
            return edit_memory(memory=item, actor=actor, **fields)
        if action == "reject":
            return reject_memory(memory=item, actor=actor)
    if kind == KIND_CORE_BLOCK and not fields:
        if action == "approve":
            return approve_core_block_proposal(proposal=item, actor=actor, channel=channel)
        if action == "reject":
            return reject_core_block_proposal(proposal=item, actor=actor, channel=channel)
    raise InvalidTransition(f"{action!r} is not an action on a {kind} proposal.")


def bulk_approve(*, actor, channel, memory_ids):
    """Approve several learned items at once -- only when **every** one is a
    `work_log`-type memory (d6). Anything else (another memory type, a
    core-block proposal, an unknown id) refuses the whole request and
    approves nothing."""
    require_owner_web(actor, channel)
    ids = list(dict.fromkeys(str(i) for i in memory_ids))
    if not 0 < len(ids) <= MAX_BULK_ITEMS:
        raise InvalidTransition(f"Name between 1 and {MAX_BULK_ITEMS} items.")
    with transaction.atomic():
        rows = list(
            _owned(Memory.objects, actor).filter(pk__in=ids, status=Memory.Status.PENDING_APPROVAL)
        )
        if len(rows) != len(ids):
            raise InvalidTransition("Only pending items you own can be bulk-approved.")
        if any(row.memory_type != Memory.Type.WORK_LOG for row in rows):
            raise MemoryPermissionDenied(
                "Bulk approval exists only for work_log items; approve the others one by one."
            )
        return [approve_memory(memory=row, actor=actor) for row in rows]
