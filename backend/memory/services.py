"""Memory curation services — the only sanctioned write path for Memory rows.

State machine (illegal transitions raise InvalidTransition → 400):

    pending_approval → approved | rejected
    approved         → archived            (forget)
    archived         → approved            (restore / re-remember)

Edit is a separate action, not a transition: it mutates fields of a
`pending_approval` or `approved` row without changing status. Repeating a
transition the row is already in is an idempotent no-op (no write, no audit).
Hard delete exists only for never-approved suggestions
(`pending_approval` / `rejected`).

Every mutating service re-fetches its row under `select_for_update` inside
its transaction, so concurrent transitions serialize and a stale instance can
never approve-then-delete or double-transition a row.

Role policy (identical for personal and company workspaces; "own rows" is the
exact predicate `source=user_created AND created_by=actor` — AI-suggested rows
are admin-managed at every stage):

    action                    owner/admin   member       viewer
    read/list/export          yes           yes          yes
    create (user_created)     yes           yes          no
    edit                      yes           own rows     no
    approve/reject/restore    yes           no           no
    forget (archive)          yes           own rows     no
    hard-delete never-approved yes          no           no
"""

from django.db import IntegrityError, transaction

from audit.services import record_audit
from memory.models import Memory, compute_fingerprint
from workspaces.models import WorkspaceMember

# Audit metadata carries ids/statuses/classifications only — never
# title/content (private or not). Reads larger than the cap emit multiple
# chunked audit rows so every accessed id stays identifiable (ADR-005).
_AUDIT_ID_CAP = 200

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}
_WRITER_ROLES = _CURATOR_ROLES | {WorkspaceMember.Role.MEMBER}

EDITABLE_FIELDS = ("title", "content", "memory_type", "sensitivity")


class MemoryServiceError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class InvalidTransition(MemoryServiceError):
    """Illegal state-machine move → 400."""


class DuplicateMemory(MemoryServiceError):
    """Fingerprint collision inside the workspace → 400."""


class MemoryPermissionDenied(MemoryServiceError):
    """Actor's role does not allow the action → 403."""


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _is_own_user_created(memory, actor):
    return (
        memory.source == Memory.Source.USER_CREATED
        and memory.created_by_id is not None
        and memory.created_by_id == actor.id
    )


def _require(condition, action):
    if not condition:
        raise MemoryPermissionDenied(f"Role does not allow {action}.")


def _locked(memory, actor):
    """Re-fetch the row under lock, scoped to the actor, inside the caller's
    transaction.

    The lock query itself enforces workspace scope (membership join) so a
    forged or stale instance carrying a foreign row's pk can never reach the
    mutation path — authorization must run against the row this returns,
    never against the caller-supplied instance. `for_actor()` cannot be used
    directly because its DISTINCT is incompatible with FOR UPDATE; the
    membership join is duplicate-free anyway ((workspace, user) is unique),
    and `of=("self",)` locks only the memory row, not the joined membership.
    A row that vanished or is out of scope is reported as an invalid
    transition (no existence leak beyond what the caller's 404 path already
    established).
    """
    locked = (
        Memory.objects.filter(pk=memory.pk, workspace__members__user=actor)
        .select_for_update(of=("self",))
        .first()
    )
    if locked is None:
        raise InvalidTransition("Memory no longer exists.")
    return locked


def _check_fingerprint_free(actor, workspace, fingerprint, exclude_pk=None):
    qs = Memory.objects.for_actor(actor, workspace=workspace).filter(fingerprint=fingerprint)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if qs.exists():
        raise DuplicateMemory("An equivalent memory already exists in this workspace.")


def _apply_curated_fields(memory, actor, fields):
    changed = []
    for name in EDITABLE_FIELDS:
        if name in fields and fields[name] != getattr(memory, name):
            setattr(memory, name, fields[name])
            changed.append(name)
    if "content" in changed:
        new_fingerprint = compute_fingerprint(memory.content)
        if new_fingerprint != memory.fingerprint:
            _check_fingerprint_free(actor, memory.workspace, new_fingerprint, exclude_pk=memory.pk)
            memory.fingerprint = new_fingerprint
            changed.append("fingerprint")
    return changed


@transaction.atomic
def create_memory(*, workspace, actor, title, content, memory_type, sensitivity):
    """User-authored memory: born approved (the user IS the curator of their
    own statement). `status`, `source`, `created_by` and provenance are
    server-controlled — serializers never accept them; `workspace` is
    resolved through `for_actor()` before this service runs.
    """
    _require(get_role(actor, workspace) in _WRITER_ROLES, "creating memories")
    fingerprint = compute_fingerprint(content)
    _check_fingerprint_free(actor, workspace, fingerprint)
    try:
        memory = Memory.objects.create(
            workspace=workspace,
            memory_type=memory_type,
            sensitivity=sensitivity,
            status=Memory.Status.APPROVED,
            source=Memory.Source.USER_CREATED,
            title=title,
            content=content,
            fingerprint=fingerprint,
            created_by=actor,
        )
    except IntegrityError:
        # Constraint backstop for a concurrent create that won the race
        # between the pre-check above and this insert.
        raise DuplicateMemory("An equivalent memory already exists in this workspace.") from None
    _record(memory, "memory.created", actor)
    return memory


def _save_curated(memory, update_fields):
    """Persist a curated change, mapping a lost fingerprint race (two rows
    concurrently edited to the same content — the row locks don't serialize
    across different rows) to DuplicateMemory instead of a 500."""
    try:
        memory.save(update_fields=update_fields)
    except IntegrityError:
        raise DuplicateMemory("An equivalent memory already exists in this workspace.") from None


@transaction.atomic
def edit_memory(*, memory, actor, **fields):
    memory = _locked(memory, actor)
    role = get_role(actor, memory.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own_user_created(memory, actor)),
        "editing this memory",
    )
    if memory.status not in (Memory.Status.PENDING_APPROVAL, Memory.Status.APPROVED):
        raise InvalidTransition(f"A {memory.status} memory cannot be edited.")
    changed = _apply_curated_fields(memory, actor, fields)
    if not changed:
        return memory
    _save_curated(memory, [*changed, "updated_at"])
    _record(memory, "memory.updated", actor, fields_changed=sorted(changed))
    return memory


@transaction.atomic
def approve_memory(*, memory, actor, **curated_fields):
    """Approve accepts curated fields (title/content/type — and critically
    `sensitivity`, since extraction defaults to normal) applied atomically
    with the transition, so a reviewer can approve a suggestion as private
    in one step.
    """
    memory = _locked(memory, actor)
    _require(get_role(actor, memory.workspace) in _CURATOR_ROLES, "approving memories")
    if memory.status == Memory.Status.APPROVED:
        return memory  # idempotent repeat
    if memory.status != Memory.Status.PENDING_APPROVAL:
        raise InvalidTransition(f"Cannot approve a {memory.status} memory.")
    changed = _apply_curated_fields(memory, actor, curated_fields)
    memory.status = Memory.Status.APPROVED
    _save_curated(memory, [*changed, "status", "updated_at"])
    _record(memory, "memory.approved", actor, fields_changed=sorted(changed))
    return memory


@transaction.atomic
def reject_memory(*, memory, actor):
    memory = _locked(memory, actor)
    _require(get_role(actor, memory.workspace) in _CURATOR_ROLES, "rejecting memories")
    if memory.status == Memory.Status.REJECTED:
        return memory  # idempotent repeat
    if memory.status != Memory.Status.PENDING_APPROVAL:
        raise InvalidTransition(f"Cannot reject a {memory.status} memory.")
    memory.status = Memory.Status.REJECTED
    memory.save(update_fields=["status", "updated_at"])
    _record(memory, "memory.rejected", actor)
    return memory


@transaction.atomic
def restore_memory(*, memory, actor):
    memory = _locked(memory, actor)
    _require(get_role(actor, memory.workspace) in _CURATOR_ROLES, "restoring memories")
    if memory.status == Memory.Status.APPROVED:
        return memory  # idempotent repeat
    if memory.status != Memory.Status.ARCHIVED:
        raise InvalidTransition(f"Cannot restore a {memory.status} memory.")
    memory.status = Memory.Status.APPROVED
    memory.save(update_fields=["status", "updated_at"])
    _record(memory, "memory.restored", actor)
    return memory


@transaction.atomic
def forget_memory(*, memory, actor):
    memory = _locked(memory, actor)
    role = get_role(actor, memory.workspace)
    _require(
        role in _CURATOR_ROLES or (role in _WRITER_ROLES and _is_own_user_created(memory, actor)),
        "forgetting this memory",
    )
    if memory.status == Memory.Status.ARCHIVED:
        return memory  # idempotent repeat
    if memory.status != Memory.Status.APPROVED:
        raise InvalidTransition(f"Cannot forget a {memory.status} memory.")
    memory.status = Memory.Status.ARCHIVED
    memory.save(update_fields=["status", "updated_at"])
    _record(memory, "memory.forgotten", actor)
    return memory


@transaction.atomic
def delete_memory(*, memory, actor):
    """Hard delete — only for never-approved suggestions; frees the
    fingerprint slot."""
    memory = _locked(memory, actor)
    _require(get_role(actor, memory.workspace) in _CURATOR_ROLES, "deleting memories")
    if memory.status not in (Memory.Status.PENDING_APPROVAL, Memory.Status.REJECTED):
        raise InvalidTransition(
            f"Only never-approved suggestions can be hard-deleted, not {memory.status} rows."
        )
    _record(memory, "memory.deleted", actor)
    memory.delete()


def record_read(*, actor, workspace, memories):
    """Read audit (ADR-005: every memory access is auditable).

    Large reads emit multiple chunked events so every accessed id appears in
    exactly one audit row; `count` on each chunk is the full read size.
    """
    all_ids = [str(m.id) for m in memories]
    chunks = [all_ids[i : i + _AUDIT_ID_CAP] for i in range(0, len(all_ids), _AUDIT_ID_CAP)] or [[]]
    for index, chunk in enumerate(chunks):
        metadata = {"memory_ids": chunk, "count": len(all_ids)}
        if len(chunks) > 1:
            metadata["chunk"] = index + 1
            metadata["chunks"] = len(chunks)
        record_audit(
            action="memory.read",
            actor=actor,
            workspace=workspace,
            metadata=metadata,
        )


def record_export(*, actor, workspace, memories):
    statuses = {}
    for m in memories:
        statuses[m.status] = statuses.get(m.status, 0) + 1
    record_audit(
        action="memory.exported",
        actor=actor,
        workspace=workspace,
        metadata={"count": len(memories), "statuses": statuses},
    )


def _record(memory, action, actor, **metadata):
    record_audit(
        action=action,
        actor=actor,
        workspace=memory.workspace,
        resource=memory,
        metadata={"status": memory.status, "source": memory.source, **metadata},
    )
