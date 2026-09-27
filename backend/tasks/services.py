"""Task services — the only sanctioned write path for Task rows.

State machine (illegal transitions raise InvalidTransition -> 400):

    open      -> done | cancelled
    done      -> open                  (reopen)
    cancelled -> open                  (reopen)

Repeating a transition the row is already in is an idempotent no-op (no
write, no audit). Field edits (title/description/due_date/priority) are a
separate action from a transition and are allowed regardless of status.

Every mutating service re-fetches its row under `select_for_update` inside
its transaction (mirrors `memory.services._locked`), so concurrent
transitions serialize and a stale instance can never double-transition a row
or race a delete.

Role policy (identical for personal and company workspaces; "own tasks" is
the exact predicate `created_by = actor` regardless of `source` -- MVP 6 §3
added `Task.source` (user_created/ai_suggested) for provenance, but it plays
no part in this role matrix; ownership is still decided by `created_by`
alone):

    action                     owner/admin   member       viewer
    read/list                  yes           yes          yes
    create                     yes           yes          no
    edit fields                yes           own tasks    no
    complete/cancel/reopen     yes           own tasks    no
    delete                     yes           own tasks    no
"""

from django.db import IntegrityError, transaction

from audit.services import record_audit
from tasks.models import Task
from workspaces.models import WorkspaceMember

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}

EDITABLE_FIELDS = ("title", "description", "due_date", "priority")


class TaskServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class InvalidTransition(TaskServiceError):
    """Illegal state-machine move -> 400."""

    code = "task.invalid_transition"


class TaskPermissionDenied(TaskServiceError):
    """Actor's role does not allow the action -> 403."""

    code = "task.permission_denied"


class DraftAlreadyConfirmed(TaskServiceError):
    """The chat draft already produced a task (Phase 4 branch 4) -> 400.

    Raised by `create_task` when `Task.source_message`'s unique constraint
    rejects a second task for the same draft message -- the constraint, not
    a read-then-write check, is the guard, so two concurrent confirms of one
    draft still create exactly one task."""

    code = "task.draft_already_confirmed"


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _is_own(task, actor):
    return task.created_by_id is not None and task.created_by_id == actor.id


def _require(condition, action):
    if not condition:
        raise TaskPermissionDenied(f"Role does not allow {action}.")


def _locked(task, actor):
    """Re-fetch the row under lock, scoped to the actor, inside the caller's
    transaction.

    The lock query itself enforces workspace scope (membership join) so a
    forged or stale instance carrying a foreign row's pk can never reach the
    mutation path -- authorization must run against the row this returns,
    never against the caller-supplied instance. `for_actor()` cannot be used
    directly because its DISTINCT is incompatible with FOR UPDATE; the
    membership join is duplicate-free anyway ((workspace, user) is unique),
    and `of=("self",)` locks only the task row, not the joined membership.
    A row that vanished or is out of scope is reported as an invalid
    transition (no existence leak beyond what the caller's 404 path already
    established).
    """
    locked = (
        Task.objects.filter(pk=task.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise InvalidTransition("Task no longer exists.")
    return locked


def _apply_edited_fields(task, fields):
    changed = []
    for name in EDITABLE_FIELDS:
        if name in fields and fields[name] != getattr(task, name):
            setattr(task, name, fields[name])
            changed.append(name)
    return changed


def _record(task, action, actor, **metadata):
    # Audit metadata carries ids/status/action/changed-field-names only --
    # never `title`/`description` text. Task content is not marked
    # `private` the way a Memory row can be, but it can still be personal or
    # company-sensitive free text, and the audit log is bookkeeping, not a
    # content mirror.
    record_audit(
        action=action,
        actor=actor,
        workspace=task.workspace,
        resource=task,
        metadata={"status": task.status, **metadata},
    )


@transaction.atomic
def create_task(
    *,
    workspace,
    actor,
    title,
    description="",
    due_date=None,
    priority=Task.Priority.NORMAL,
    source=Task.Source.USER_CREATED,
    source_message=None,
):
    """`source` is never derived from a client-asserted value — the view
    resolves it (via `tasks.serializers.TaskCreateSerializer`'s server-side
    `draft_message_id` verification, MVP 6 §3) and passes the already-decided
    value in, exactly like `status`/`created_by` are already server-decided
    rather than client input.

    `source_message` (Phase 4 branch 4) is that same already-verified draft
    message, never a client value; it is only ever passed together with
    `source=ai_suggested`. A second task for the same message violates
    `Task.source_message`'s unique constraint and raises
    `DraftAlreadyConfirmed` -- no second row, no second audit entry."""
    _require(get_role(actor, workspace) in _WRITER_ROLES, "creating tasks")
    try:
        # A savepoint of its own: the IntegrityError must roll back only the
        # failed INSERT, leaving the caller's transaction usable.
        with transaction.atomic():
            task = Task.objects.create(
                workspace=workspace,
                title=title,
                description=description,
                due_date=due_date,
                priority=priority,
                source=source,
                source_message=source_message,
                status=Task.Status.OPEN,
                created_by=actor,
            )
    except IntegrityError as exc:
        # Only the unique violation on this column means "already confirmed";
        # anything else is a real error and propagates.
        message = str(exc).lower()
        if source_message is not None and "unique" in message and "source_message" in message:
            raise DraftAlreadyConfirmed("This draft has already been confirmed.") from exc
        raise
    _record(task, "task.created", actor, source=source)
    return task


@transaction.atomic
def edit_task(*, task, actor, **fields):
    task = _locked(task, actor)
    role = get_role(actor, task.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(task, actor)),
        "editing this task",
    )
    changed = _apply_edited_fields(task, fields)
    if not changed:
        return task
    task.save(update_fields=[*changed, "updated_at"])
    _record(task, "task.updated", actor, fields_changed=sorted(changed))
    return task


@transaction.atomic
def complete_task(*, task, actor):
    task = _locked(task, actor)
    role = get_role(actor, task.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(task, actor)),
        "completing this task",
    )
    if task.status == Task.Status.DONE:
        return task  # idempotent repeat
    if task.status != Task.Status.OPEN:
        raise InvalidTransition(f"Cannot complete a {task.status} task.")
    task.status = Task.Status.DONE
    task.save(update_fields=["status", "updated_at"])
    _record(task, "task.completed", actor)
    return task


@transaction.atomic
def cancel_task(*, task, actor):
    task = _locked(task, actor)
    role = get_role(actor, task.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(task, actor)),
        "cancelling this task",
    )
    if task.status == Task.Status.CANCELLED:
        return task  # idempotent repeat
    if task.status != Task.Status.OPEN:
        raise InvalidTransition(f"Cannot cancel a {task.status} task.")
    task.status = Task.Status.CANCELLED
    task.save(update_fields=["status", "updated_at"])
    _record(task, "task.cancelled", actor)
    return task


@transaction.atomic
def reopen_task(*, task, actor):
    task = _locked(task, actor)
    role = get_role(actor, task.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(task, actor)),
        "reopening this task",
    )
    if task.status == Task.Status.OPEN:
        return task  # idempotent repeat
    if task.status not in (Task.Status.DONE, Task.Status.CANCELLED):
        raise InvalidTransition(f"Cannot reopen a {task.status} task.")
    task.status = Task.Status.OPEN
    task.save(update_fields=["status", "updated_at"])
    _record(task, "task.reopened", actor)
    return task


@transaction.atomic
def delete_task(*, task, actor):
    task = _locked(task, actor)
    role = get_role(actor, task.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own(task, actor)),
        "deleting this task",
    )
    _record(task, "task.deleted", actor)
    task.delete()
