"""`purge_trash` -- the gate-class permanent delete of trashed rows (ADR-009).

Deleting a knowledge document or a task only moves it to the trash
(reversible, confirm-class). Making that permanent before
TRASH_RETENTION_DAYS elapse is irreversible, so it is gate-class: the trash
view's "Kalıcı olarak sil" creates an `ApprovalRequest` for this tool, and
only the owner's separate, explicit decision over the web channel runs it.

Phase 8A branch 5 (brief D11) adds two payload keys for forget-source:
`episode_ids` (episodes a forget operation moved to the trash) and
`forgotten_memory_ids` (memories a forget operation archived or rejected).
Phase 8B branch 1 (brief D7) adds `fact_ids` (learned facts in the trash or
archived/rejected by a forget operation; a pending proposal that supersedes
one is rejected first, so the PROTECT link never blocks the purge) and
`entity_ids` (trashed learned entities, with their non-live facts).
Forget-source itself stays a reversible confirm-class move; only this
approved request deletes them for good.

The purge itself happens in `create_execution`, i.e. inside the approval
decision's own transaction: a handful of row deletes needs no worker, and
the decision and its effect commit (or roll back) together. There is no
separate execution row -- the per-row `task.purged` / `knowledge.purged` /
`memory.episode_purged` / `memory.purged` audit entries (carrying the
approval request id) are the execution record. Rows restored between
request and decision are left alone.

Human-only (`proposable=False`): the chat router and the agent planner
never see it, and a machine origin cannot request it.
"""

import uuid

from django.db import transaction

from knowledge.models import KnowledgeDocument
from knowledge.services import purge_trashed_document
from memory.graph.services import (
    purge_entity,
    purge_fact,
    purgeable_entities,
    purgeable_facts,
)
from memory.ingestion.services import (
    forgotten_memories,
    purge_forgotten_memory,
    purge_trashed_episode,
    trashed_episodes,
)
from tasks.models import Task
from tasks.services import purge_trashed_task
from tools.registry import ToolSpec, ToolValidationError, register_tool

TOOL_NAME = "purge_trash"
MAX_ITEMS = 200
_TITLE_MAX = 120
_DISPLAY_TITLES_MAX = 10


def _trashed_tasks(workspace):
    return Task.all_objects.filter(workspace=workspace, deleted_at__isnull=False)


def _trashed_documents(workspace):
    return KnowledgeDocument.all_objects.filter(workspace=workspace, deleted_at__isnull=False)


def _purge_task(row, actor, request_id):
    return purge_trashed_task(row, reason="approval", actor=actor, approval_request_id=request_id)


def _purge_document(row, actor, request_id):
    return purge_trashed_document(
        row, reason="approval", actor=actor, approval_request_id=request_id
    )


def _purge_episode(row, actor, request_id):
    return purge_trashed_episode(row, actor=actor, approval_request_id=request_id)


def _purge_memory(row, actor, request_id):
    return purge_forgotten_memory(row, actor=actor, approval_request_id=request_id)


def _purge_fact(row, actor, request_id):
    return purge_fact(row, actor=actor, approval_request_id=request_id)


def _purge_entity(row, actor, request_id):
    return purge_entity(row, actor=actor, approval_request_id=request_id)


# key -> (purgeable queryset for a workspace, purge one row, display field)
_KEYS = {
    "task_ids": (_trashed_tasks, _purge_task, "title"),
    "document_ids": (_trashed_documents, _purge_document, "title"),
    "episode_ids": (trashed_episodes, _purge_episode, "kind"),
    "forgotten_memory_ids": (forgotten_memories, _purge_memory, "title"),
    # Facts before entities: purging an entity deletes its non-live facts,
    # and a fact named in both keys is then already gone.
    "fact_ids": (purgeable_facts, _purge_fact, "statement"),
    "entity_ids": (purgeable_entities, _purge_entity, "name"),
}
_COUNT_NAMES = {
    "task_ids": "task_count",
    "document_ids": "document_count",
    "episode_ids": "episode_count",
    "forgotten_memory_ids": "forgotten_memory_count",
    "fact_ids": "fact_count",
    "entity_ids": "entity_count",
}


def _ids(payload, key):
    raw = payload.get(key, [])
    if not isinstance(raw, list):
        raise ToolValidationError(f"`{key}` must be a list of ids.")
    ids = []
    for value in raw:
        try:
            ids.append(str(uuid.UUID(str(value))))
        except ValueError as exc:
            raise ToolValidationError(f"`{key}` contains an invalid id.") from exc
    return sorted(set(ids))


def validate_purge_trash_request(workspace, payload):
    unknown = set(payload) - set(_KEYS)
    if unknown:
        raise ToolValidationError(f"Unknown payload keys: {sorted(unknown)}.")
    total = 0
    for key, (purgeable, _, _) in _KEYS.items():
        ids = _ids(payload, key)
        total += len(ids)
        if purgeable(workspace).filter(pk__in=ids).count() != len(ids):
            raise ToolValidationError("Only items in this workspace's trash can be purged.")
    if not 0 < total <= MAX_ITEMS:
        raise ToolValidationError(f"Name between 1 and {MAX_ITEMS} trashed items.")


def create_purge_trash_execution(approval_request):
    payload = approval_request.payload or {}
    actor = approval_request.decided_by
    request_id = str(approval_request.pk)
    with transaction.atomic():
        for key, (purgeable, purge, _) in _KEYS.items():
            rows = (
                purgeable(approval_request.workspace)
                .select_for_update(of=("self",))
                .filter(pk__in=_ids(payload, key))
                .select_related("workspace")
            )
            for row in rows:
                purge(row, actor, request_id)
    return request_id


def dispatch_purge_trash(execution_id):
    """Nothing to dispatch: the purge already committed with the decision."""
    return None


def describe_purge_trash_request(approval_request):
    payload = approval_request.payload or {}
    description = {"kind": TOOL_NAME}
    titles = []
    for key, (purgeable, _, field) in _KEYS.items():
        ids = _safe_ids(payload, key)
        description[_COUNT_NAMES[key]] = len(ids)
        titles.extend(
            str(value)[:_TITLE_MAX]
            for value in purgeable(approval_request.workspace)
            .filter(pk__in=ids)
            .values_list(field, flat=True)
        )
    description["titles"] = titles[:_DISPLAY_TITLES_MAX]
    return description


def _safe_ids(payload, key):
    try:
        return _ids(payload, key)
    except ToolValidationError:
        return []


def _register():
    register_tool(
        ToolSpec(
            name=TOOL_NAME,
            gated=True,
            create_execution=create_purge_trash_execution,
            dispatch=dispatch_purge_trash,
            validate_request=validate_purge_trash_request,
            describe_request=describe_purge_trash_request,
            proposable=False,
        )
    )
