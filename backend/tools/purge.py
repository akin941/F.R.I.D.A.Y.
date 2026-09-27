"""`purge_trash` -- the gate-class permanent delete of trashed rows (ADR-009).

Deleting a knowledge document or a task only moves it to the trash
(reversible, confirm-class). Making that permanent before
TRASH_RETENTION_DAYS elapse is irreversible, so it is gate-class: the trash
view's "Kalıcı olarak sil" creates an `ApprovalRequest` for this tool, and
only the owner's separate, explicit decision over the web channel runs it.

The purge itself happens in `create_execution`, i.e. inside the approval
decision's own transaction: a handful of row deletes needs no worker, and
the decision and its effect commit (or roll back) together. There is no
separate execution row -- the per-row `task.purged` / `knowledge.purged`
audit entries (carrying the approval request id) are the execution record.
Rows restored between request and decision are left alone.

Human-only (`proposable=False`): the chat router and the agent planner
never see it, and a machine origin cannot request it.
"""

import uuid

from django.db import transaction

from knowledge.models import KnowledgeDocument
from knowledge.services import purge_trashed_document
from tasks.models import Task
from tasks.services import purge_trashed_task
from tools.registry import ToolSpec, ToolValidationError, register_tool

TOOL_NAME = "purge_trash"
MAX_ITEMS = 200
_TITLE_MAX = 120
_DISPLAY_TITLES_MAX = 10
_KEYS = {"task_ids": Task, "document_ids": KnowledgeDocument}


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
    for key, model in _KEYS.items():
        ids = _ids(payload, key)
        total += len(ids)
        trashed = model.all_objects.filter(
            workspace=workspace, pk__in=ids, deleted_at__isnull=False
        ).count()
        if trashed != len(ids):
            raise ToolValidationError("Only items in this workspace's trash can be purged.")
    if not 0 < total <= MAX_ITEMS:
        raise ToolValidationError(f"Name between 1 and {MAX_ITEMS} trashed items.")


def create_purge_trash_execution(approval_request):
    payload = approval_request.payload or {}
    actor = approval_request.decided_by
    request_id = str(approval_request.pk)
    with transaction.atomic():
        for model, purge in (
            (Task, purge_trashed_task),
            (KnowledgeDocument, purge_trashed_document),
        ):
            key = "task_ids" if model is Task else "document_ids"
            rows = (
                model.all_objects.select_for_update(of=("self",))
                .filter(
                    workspace=approval_request.workspace,
                    pk__in=_ids(payload, key),
                    deleted_at__isnull=False,
                )
                .select_related("workspace")
            )
            for row in rows:
                purge(row, reason="approval", actor=actor, approval_request_id=request_id)
    return request_id


def dispatch_purge_trash(execution_id):
    """Nothing to dispatch: the purge already committed with the decision."""
    return None


def describe_purge_trash_request(approval_request):
    payload = approval_request.payload or {}
    task_ids = _safe_ids(payload, "task_ids")
    document_ids = _safe_ids(payload, "document_ids")
    titles = [
        title[:_TITLE_MAX]
        for model, ids in ((Task, task_ids), (KnowledgeDocument, document_ids))
        for title in model.all_objects.filter(
            workspace=approval_request.workspace, pk__in=ids
        ).values_list("title", flat=True)
    ]
    return {
        "kind": TOOL_NAME,
        "task_count": len(task_ids),
        "document_count": len(document_ids),
        "titles": titles[:_DISPLAY_TITLES_MAX],
    }


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
