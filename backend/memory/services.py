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

import difflib
import hashlib
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.core_blocks import (
    CORE_BLOCK_CHAR_BUDGETS,
    OWNER_EDITABLE_LABELS,
    labels_for_workspace,
)
from memory.jobs import dispatch_memory_embedding
from memory.models import (
    CoreBlockProposal,
    CoreMemoryBlock,
    CoreMemoryBlockVersion,
    Memory,
    compute_fingerprint,
)
from workspaces.models import Workspace, WorkspaceMember

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

    code = "memory.invalid_transition"


class DuplicateMemory(MemoryServiceError):
    """Fingerprint collision inside the workspace → 400."""

    code = "memory.duplicate"


class MemoryPermissionDenied(MemoryServiceError):
    """Actor's role does not allow the action → 403."""

    code = "memory.permission_denied"


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


def _source_conversation_is_private(memory):
    """Lock + re-check the source conversation's live `private` flag,
    closing a race against a concurrent `mark_conversation_private()`
    (Codex round-7 P1): a plain unlocked read of `memory.source_
    conversation.private` can see a stale `False` while the other
    transaction's write is uncommitted (READ COMMITTED never sees dirty
    writes) — this forces the caller to wait for that transaction to
    finish first, then re-reads the actually-committed value.
    `filter(source_conversation_id__isnull=False)` is not just a null
    guard: it also makes Django emit an INNER JOIN instead of the default
    LEFT OUTER JOIN for this nullable FK, which is what makes `FOR UPDATE
    OF` legal at all (Postgres refuses it on the nullable side of an outer
    join). Returns `False` when there is no source conversation. A
    lock-order deadlock against `mark_conversation_private()`'s
    conversation-then-memory-rows ordering is an accepted, non-corrupting
    outcome (round-6 precedent) — Postgres cleanly aborts one side for an
    application-level retry, not a defect to eliminate here.
    """
    if memory.source_conversation_id is None:
        return False
    return (
        Memory.objects.select_for_update(of=("source_conversation",))
        .select_related("source_conversation")
        .get(pk=memory.pk, source_conversation_id__isnull=False)
        .source_conversation.private
    )


def _apply_curated_fields(memory, actor, fields):
    # `memory_type` is editable here (edit_memory, and approve_memory's
    # curate-on-approval step) — this keeps the `work_log`/`system_generated`
    # pairing symmetric in both directions:
    if "memory_type" in fields:
        new_type = fields["memory_type"]
        if new_type == Memory.Type.WORK_LOG and memory.source != Memory.Source.SYSTEM_GENERATED:
            # Codex round-5 P2: without this, an ai_suggested/user_created
            # row could be retyped to `work_log` after the fact — the same
            # choke-point bypass create_memory() is guarded against, just
            # via the edit path instead of creation.
            raise InvalidTransition("work_log memories can only be system-generated.")
        if memory.source == Memory.Source.SYSTEM_GENERATED and new_type != Memory.Type.WORK_LOG:
            # Codex round-6 P2: the reverse direction — a curator retyping
            # an existing system_generated row AWAY from work_log would
            # leave an auto-approved row outside the one pairing that's
            # allowed to be auto-approved, breaking the invariant's
            # reliability as an audit-trail filter even though no new
            # approval event occurs.
            raise InvalidTransition("system-generated memories must stay type work_log.")
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
def create_memory(*, workspace, actor, title, content, memory_type, sensitivity, proposal=False):
    """User-authored memory: born approved (the user IS the curator of their
    own statement). `status`, `source`, `created_by` and provenance are
    server-controlled — serializers never accept them; `workspace` is
    resolved through `for_actor()` before this service runs.

    `proposal=True` (Phase 6 branch 3, ADR-015 / brief A6) births the same
    `user_created` row as `pending_approval` instead: the voice route
    "hatırla ki …" files what the microphone heard as a proposal the owner
    approves by click on the memory card, never as an approved row (a
    recognised utterance is not the owner's explicit statement — STT errors,
    a TV or a guest can produce it). A proposal is never embedded or used as
    context until it is approved (`_reconcile_embedding` /
    `memory.context` both require `approved`). The flag can only make a row
    weaker, never stronger: there is no way to request any other status.
    """
    _require(get_role(actor, workspace) in _WRITER_ROLES, "creating memories")
    if memory_type == Memory.Type.WORK_LOG:
        # Codex round-5 P2 on feat/p1-work-log: this function always births
        # `source=user_created` rows — `work_log` is reserved for
        # `record_work_log()`'s `system_generated` pairing (ADR-008 k.4).
        # Without this, any writer-role user could create an
        # indistinguishable-in-type "work_log" memory through the ordinary
        # public API, defeating the single-choke-point guarantee. Checked
        # AFTER the role gate: an unauthorized actor gets the ordinary 403,
        # not a 400 that leaks which memory_type values are "special".
        raise InvalidTransition("work_log memories can only be system-generated.")
    fingerprint = compute_fingerprint(content)
    _check_fingerprint_free(actor, workspace, fingerprint)
    try:
        memory = Memory.objects.create(
            workspace=workspace,
            memory_type=memory_type,
            sensitivity=sensitivity,
            status=Memory.Status.PENDING_APPROVAL if proposal else Memory.Status.APPROVED,
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
    _record(memory, "memory.created", actor, **({"proposal": True} if proposal else {}))
    _reconcile_embedding(memory)
    return memory


@transaction.atomic
def record_work_log(*, workspace, title, content, sensitivity=Memory.Sensitivity.NORMAL):
    """The ONLY creation path for `system_generated` `work_log` rows
    (ADR-008 k.4's deliberately narrow ADR-005 exception, Phase 1
    p1-work-log). Auto-approved because the row IS a machine-produced
    activity summary, not conversation-derived content a human hasn't
    reviewed — no `actor`/role check, mirroring how `tools.export`'s
    vault-export finalization writes system-actor audit rows without a
    human actor. `memory_type` and `source` are hardcoded, not parameters:
    this is the single choke point that guarantees `type=work_log AND
    source=system_generated` is the only auto-approved pairing — a future
    producer (Phase 2's harness, a gated-tool execution, an agent run
    reaching a terminal state) calls this directly, never
    `Memory.objects.create()`.

    `sensitivity` (Phase 2 branch 3, Codex plan-review round-1 HIGH) is
    the one thing NOT hardcoded here, unlike `memory_type`/`source`/
    `status` above: unlike those three (which define the single
    auto-approved pairing this function exists to gate), a work_log
    producer's own source material can legitimately be sensitive (e.g. a
    harness run against a repo the owner flagged sensitive) -- callers
    that never had a sensitivity concept default to the original
    `normal` behavior unchanged. A `private` result is kept out of
    `cloud_eligible()` (`memory/models.py:41-36`) exactly like every
    other `private` memory; `_reconcile_embedding` below already handles
    not enqueuing it, no special-casing needed.
    """
    fingerprint = compute_fingerprint(content)
    try:
        memory = Memory.objects.create(
            workspace=workspace,
            memory_type=Memory.Type.WORK_LOG,
            sensitivity=sensitivity,
            status=Memory.Status.APPROVED,
            source=Memory.Source.SYSTEM_GENERATED,
            title=title,
            content=content,
            fingerprint=fingerprint,
        )
    except IntegrityError:
        raise DuplicateMemory("An equivalent memory already exists in this workspace.") from None
    record_audit(
        action="memory.system_recorded",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        resource=memory,
        metadata={"status": memory.status, "source": memory.source},
    )
    _reconcile_embedding(memory)
    return memory


@transaction.atomic
def privatize_conversation_memories(*, conversation, actor):
    """Cascade half of marking a conversation private (Phase 1
    p1-work-log, closes MASTER_PLAN §O): reject every still-
    `pending_approval` candidate and archive every already-`approved`
    memory extracted from `conversation`, so nothing born from it stays
    `cloud_eligible()` after the flag is set — the retroactive-cleanup
    gap a flag-only change would leave open (Codex round-3 MEDIUM on the
    embeddings branch's design). Archiving triggers `_reconcile_embedding`,
    clearing any stored embedding too.

    No curator-role check here: this runs as a direct, same-transaction
    consequence of an already-authorized privacy decision (ordinary
    workspace-membership access to the conversation, checked by the
    caller), not an independent curation act — mirrors how
    `finalize_extraction_success` creates `Memory` rows directly rather
    than through `create_memory()`'s role-gated path.

    ONE unified, unfiltered-by-status lock (Codex round-6 P1), not three
    separate status-filtered passes: the original three-pass version left
    a genuine TOCTOU window — a concurrent `restore_memory()` racing this
    function could lock and restore an archived row to `approved` in
    between the "approved" pass (which had already run and missed it,
    since it was still archived) and the "already-archived" pass (which,
    by the time it queried, no longer saw the row as archived either,
    since it just became approved). Locking every `source_conversation=
    conversation` row up front, regardless of current status, closes this:
    whichever of the two transactions (this cascade, or a concurrent
    restore) acquires a given row's lock first forces the other to wait,
    and PostgreSQL re-evaluates this queryset's `WHERE` clause against the
    row's freshly-committed state once unblocked (`source_conversation` is
    the only filter here, never invalidated by a status change) — so a
    restore that raced ahead is still caught and re-locked/re-archived
    right after, and a restore that arrives second sees the already-set
    `privacy_locked`/`private` state and is correctly rejected.
    """
    memories = list(Memory.objects.select_for_update().filter(source_conversation=conversation))
    for memory in memories:
        if memory.status == Memory.Status.PENDING_APPROVAL:
            memory.status = Memory.Status.REJECTED
            memory.save(update_fields=["status", "updated_at"])
            _record(memory, "memory.rejected", actor, cascade="conversation_privatized")
        elif memory.status == Memory.Status.APPROVED:
            memory.status = Memory.Status.ARCHIVED
            # Durable, never-cleared marker (Codex round-3 P2): the
            # `source_conversation` FK is nullable (SET_NULL on delete), so
            # a later conversation deletion would silently defeat a check
            # based on `source_conversation.private` alone. `privacy_locked`
            # survives that — see `restore_memory()`'s guard and the
            # field's own docstring in memory/models.py.
            memory.privacy_locked = True
            memory.save(update_fields=["status", "privacy_locked", "updated_at"])
            _record(memory, "memory.forgotten", actor, cascade="conversation_privatized")
            _reconcile_embedding(memory)
        elif memory.status == Memory.Status.ARCHIVED and not memory.privacy_locked:
            # Codex round-4 P2: a memory manually forgotten BEFORE the
            # conversation was marked private is already archived — no
            # status change needed, just the lock (an archived row's
            # embedding is already cleared by whatever transition archived
            # it).
            memory.privacy_locked = True
            memory.save(update_fields=["privacy_locked", "updated_at"])
            _record(memory, "memory.privacy_locked", actor, cascade="conversation_privatized")
        # REJECTED rows: already a dead end (no path back to approved),
        # nothing to do.


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
    _reconcile_embedding(memory, text_changed=bool({"title", "content"} & set(changed)))
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
    # privatize_conversation_memories() already cascades a reject over
    # every pending_approval row when a conversation turns private, but a
    # concurrent approval could still race it — _source_conversation_is_
    # private() (Codex round-7 P1) locks and re-checks the live flag
    # rather than trusting a plain read, which could see a stale `False`
    # while the other transaction's write is still uncommitted.
    if _source_conversation_is_private(memory):
        raise InvalidTransition("Cannot approve a memory from a private conversation.")
    changed = _apply_curated_fields(memory, actor, curated_fields)
    memory.status = Memory.Status.APPROVED
    _save_curated(memory, [*changed, "status", "updated_at"])
    _record(memory, "memory.approved", actor, fields_changed=sorted(changed))
    _count_learning_decision(memory.ingestion_source_id, "proposals_accepted")
    _reconcile_embedding(memory, text_changed=bool({"title", "content"} & set(changed)))
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
    _count_learning_decision(memory.ingestion_source_id, "proposals_rejected")
    # Defense-in-depth: a pending row's embedding is always None already.
    _reconcile_embedding(memory)
    return memory


@transaction.atomic
def restore_memory(*, memory, actor):
    memory = _locked(memory, actor)
    _require(get_role(actor, memory.workspace) in _CURATOR_ROLES, "restoring memories")
    if memory.status == Memory.Status.APPROVED:
        return memory  # idempotent repeat
    if memory.status != Memory.Status.ARCHIVED:
        raise InvalidTransition(f"Cannot restore a {memory.status} memory.")
    # Restore is the OTHER door back into cloud_eligible() from archived —
    # privatize_conversation_memories() archives an approved memory as part
    # of its cascade specifically to stop it being retrievable. TWO checks,
    # covering two different gaps a single one would miss:
    # (1) the durable `privacy_locked` marker (Codex round-3 P2) — survives
    # `source_conversation` going NULL on conversation delete (SET_NULL),
    # which a live-FK-only check would silently stop catching; and
    # (2) a locked re-check of the LIVE flag (Codex round-7 P1) — closes a
    # race against a conversation that is BEING marked private right now,
    # concurrently, before the cascade has had a chance to set
    # `privacy_locked` on this row yet. Neither alone is sufficient: (1)
    # only fires after the cascade has already run; (2) stops firing the
    # instant the conversation is deleted.
    if memory.privacy_locked or _source_conversation_is_private(memory):
        raise InvalidTransition("Cannot restore a memory locked by a private conversation.")
    memory.status = Memory.Status.APPROVED
    memory.save(update_fields=["status", "updated_at"])
    _record(memory, "memory.restored", actor)
    _reconcile_embedding(memory)
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
    _reconcile_embedding(memory)
    return memory


@transaction.atomic
def delete_memory(*, memory, actor, channel):
    """Hard delete — only for never-approved suggestions; frees the
    fingerprint slot. Confirm-class under ADR-009 (approved memories are
    archived by `forget_memory` instead, restorable): a human actor over the
    web channel only -- `channel` is required, never defaulted."""
    if getattr(actor, "is_agent", False) or channel != CHANNEL_WEB:
        raise MemoryPermissionDenied("Deleting a memory is only allowed for a person over the web.")
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


def _reconcile_embedding(memory, *, text_changed=False):
    """Keep `Memory.embedding` in sync with `cloud_eligible()` membership and
    embedded-text freshness, in the same transaction as the write that may
    have changed either (branch p1-memory-embeddings). This is the ONE
    reconciliation rule every eligibility/text-affecting write goes
    through — not a per-function ad hoc check — so a future write path
    can't accidentally skip it. Not a substitute for the periodic recovery
    scan (`memory.tasks.recover_stale_memory_embeddings`), which only
    covers rows that predate this call ever running for them (e.g.
    pre-Phase-1 history); every live transition reconciles itself
    immediately rather than waiting for a scan to notice.

    `text_changed` covers title/content edits on an already-eligible row:
    the embedded text (title + content) is now stale even though
    eligibility itself didn't change, so the stored vector must be
    invalidated and re-enqueued exactly like a fresh approval would be.
    """
    eligible = (
        memory.status == Memory.Status.APPROVED and memory.sensitivity == Memory.Sensitivity.NORMAL
    )
    if eligible and (memory.embedding is None or text_changed):
        memory.embedding = None
        memory.embedding_provider = None
        # Orphans any claim_token an in-flight embed attempt was fenced
        # against — that attempt's result is now for stale text (or the row
        # just became newly eligible) and must be discarded when it tries
        # to finalize, mirroring how memory.jobs._fenced_memory discards a
        # write against a row that changed underneath it.
        memory.claim_token = None
        memory.lease_expires_at = None
        memory.retry_count = 0
        memory.save(
            update_fields=[
                "embedding",
                "embedding_provider",
                "claim_token",
                "lease_expires_at",
                "retry_count",
            ]
        )
        transaction.on_commit(lambda memory_id=memory.id: dispatch_memory_embedding(memory_id))
    elif not eligible and memory.embedding is not None:
        # A vector derived from now-private or now-archived/rejected content
        # must not persist anywhere, matching ADR-005's negative contract in
        # spirit, not just "invisible to retrieval."
        memory.embedding = None
        memory.embedding_provider = None
        memory.save(update_fields=["embedding", "embedding_provider"])


def _count_learning_decision(source_id, field):
    """Acceptance metric (D10): an owner decision on a learned proposal
    bumps its source's daily counter. Counts only."""
    if source_id is None:
        return
    from memory.ingestion.services import bump_counter
    from memory.models import IngestionSource

    source = IngestionSource.objects.filter(pk=source_id).first()
    if source is not None:
        bump_counter(source, field)


def _record(memory, action, actor, **metadata):
    record_audit(
        action=action,
        actor=actor,
        workspace=memory.workspace,
        resource=memory,
        metadata={"status": memory.status, "source": memory.source, **metadata},
    )


# --- Core memory blocks (Phase 8A D7, D8) ---
#
# Who writes what (D7): `self` only through `set_assembled_self_block` (the
# deterministic assembler, D8); `developer`, `preferences`,
# `current_focus`, `active_projects` through an owner edit
# (`update_core_block` / `rollback_core_block`: the installation owner, a
# human, over CHANNEL_WEB, in a workspace they own) or an approved
# `CoreBlockProposal`. Agents never write a block; they may only file a
# proposal (branch 5's producer). Every change appends a version and leaves
# a `memory.core_block.updated` audit row with label, version and writer
# kind -- never content.

# At most this many approved-or-pending core-block proposals per owner per
# ISO week (D7, D10), enforced here, not in a prompt.
CORE_BLOCK_PROPOSALS_PER_WEEK = 3


class CoreBlockPermissionDenied(MemoryPermissionDenied):
    """Not the owner, not a human, not the web channel, or a label the
    caller may not write (`self`) -> 403."""

    code = "memory.core_block.permission_denied"


class CoreBlockInvalid(MemoryServiceError):
    """Unknown label for this workspace, over the char budget, or an
    unknown version -> 400."""

    code = "memory.core_block.invalid"


class CoreBlockStale(InvalidTransition):
    """The block moved on since the caller's base version -> 400 (a stale
    proposal is refused at approval and re-proposed next night)."""

    code = "memory.core_block.stale_version"


class CoreBlockProposalCapReached(MemoryServiceError):
    """The weekly core-block proposal cap is used up: the proposal is
    discarded (the caller counts it), never queued."""

    code = "memory.core_block.weekly_cap"


def _content_hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


def _require_owner_web(*, actor, workspace, channel):
    """The owner-edit predicate: the installation owner, a human, over the
    trusted web channel, acting in a workspace they own."""
    if channel != CHANNEL_WEB:
        raise CoreBlockPermissionDenied("Core blocks are changed only from a web session.")
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or getattr(actor, "is_agent", False)
        or not getattr(actor, "is_owner", False)
        or not actor.is_active
    ):
        raise CoreBlockPermissionDenied("Only the owner can change core blocks.")
    if workspace.owner_id != actor.id or get_role(actor, workspace) != WorkspaceMember.Role.OWNER:
        raise CoreBlockPermissionDenied("Only the workspace owner can change core blocks.")


def _check_label(workspace, label, *, allow_self=False):
    if label not in CoreMemoryBlock.Label.values:
        raise CoreBlockInvalid(f"Unknown core block label {label!r}.")
    if label == CoreMemoryBlock.Label.SELF and not allow_self:
        raise CoreBlockPermissionDenied("The self block is written only by its assembler.")
    if label not in labels_for_workspace(workspace):
        raise CoreBlockInvalid(f"A {workspace.type} workspace has no {label!r} block.")


def _check_budget(label, content):
    budget = CORE_BLOCK_CHAR_BUDGETS[label]
    if len(content) > budget:
        raise CoreBlockInvalid(f"The {label} block is limited to {budget} characters.")


def _locked_block(workspace, label):
    """Lock (creating if needed) the block row inside the caller's
    transaction. A new row starts at version 0 with no content."""
    block = (
        CoreMemoryBlock.objects.filter(workspace=workspace, label=label).select_for_update().first()
    )
    if block is None:
        try:
            with transaction.atomic():
                CoreMemoryBlock.objects.create(
                    workspace=workspace,
                    label=label,
                    updated_by_kind=CoreMemoryBlock.WriterKind.OWNER,
                )
        except IntegrityError:
            pass  # a concurrent writer created it; lock theirs
        block = CoreMemoryBlock.objects.select_for_update().get(workspace=workspace, label=label)
    return block


def _write_block_version(block, *, content, sensitivity, kind, actor):
    block.content = content
    block.content_hash = _content_hash(content)
    block.sensitivity = sensitivity
    block.version += 1
    block.updated_by_kind = kind
    block.updated_by = actor
    block.save()
    CoreMemoryBlockVersion.objects.create(
        workspace=block.workspace,
        block=block,
        version=block.version,
        content=content,
        content_hash=block.content_hash,
        sensitivity=sensitivity,
        updated_by_kind=kind,
        updated_by=actor,
    )
    is_system = kind == CoreMemoryBlock.WriterKind.ASSEMBLER
    record_audit(
        action="memory.core_block.updated",
        actor=actor,
        actor_type=AuditLog.ActorType.SYSTEM if is_system else AuditLog.ActorType.HUMAN,
        workspace=block.workspace,
        resource=block,
        metadata={"label": block.label, "version": block.version, "writer_kind": kind},
    )
    return block


def set_assembled_self_block(*, workspace, content):
    """The ONLY writer of `label=self` (D8). Called by the deterministic
    assembler (`assistant.self_block`), never by a view or an agent. Writes
    a new version only when the content hash changes; returns
    `(block, changed)`."""
    if workspace.type != Workspace.Type.PERSONAL:
        raise CoreBlockInvalid("The self block lives in the owner's personal workspace.")
    _check_budget(CoreMemoryBlock.Label.SELF, content)
    with transaction.atomic():
        block = _locked_block(workspace, CoreMemoryBlock.Label.SELF)
        if block.version > 0 and block.content_hash == _content_hash(content):
            return block, False
        block = _write_block_version(
            block,
            content=content,
            sensitivity=Memory.Sensitivity.NORMAL,
            kind=CoreMemoryBlock.WriterKind.ASSEMBLER,
            actor=None,
        )
    return block, True


def update_core_block(
    *, workspace, label, content, actor, channel, sensitivity=None, base_version=None
):
    """Owner edit of an editable block (D7): `developer`/`preferences` in the
    personal workspace, `current_focus`/`active_projects` anywhere.
    `base_version`, when given, must match the current version (optimistic
    concurrency for the editor). A no-op edit writes nothing."""
    _require_owner_web(actor=actor, workspace=workspace, channel=channel)
    _check_label(workspace, label)
    if label not in OWNER_EDITABLE_LABELS:
        raise CoreBlockPermissionDenied("This block is not owner-editable.")
    _check_budget(label, content)
    with transaction.atomic():
        block = _locked_block(workspace, label)
        if base_version is not None and base_version != block.version:
            raise CoreBlockStale("The block changed since you opened it.")
        new_sensitivity = sensitivity or block.sensitivity
        if block.version > 0 and block.content == content and block.sensitivity == new_sensitivity:
            return block
        return _write_block_version(
            block,
            content=content,
            sensitivity=new_sensitivity,
            kind=CoreMemoryBlock.WriterKind.OWNER,
            actor=actor,
        )


def rollback_core_block(*, workspace, label, version, actor, channel):
    """Confirm-class in-app edit (D7): restore an earlier version's content
    as a NEW version (history is never rewritten)."""
    _require_owner_web(actor=actor, workspace=workspace, channel=channel)
    _check_label(workspace, label)
    with transaction.atomic():
        block = _locked_block(workspace, label)
        target = CoreMemoryBlockVersion.objects.filter(block=block, version=version).first()
        if target is None:
            raise CoreBlockInvalid("No such version.")
        if target.version == block.version:
            return block
        return _write_block_version(
            block,
            content=target.content,
            sensitivity=target.sensitivity,
            kind=CoreMemoryBlock.WriterKind.OWNER,
            actor=actor,
        )


def _render_diff(old, new):
    return "\n".join(
        difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "proposed", lineterm="")
    )


def _iso_week_start(now):
    local = timezone.localtime(now)
    start = local - timedelta(days=local.isoweekday() - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def create_core_block_proposal(
    *,
    workspace,
    label,
    proposed_content,
    provenance_quote,
    source_episode_id=None,
    ingestion_source=None,
):
    """File a model-made proposal (D7, D9) -- system code only (branch 5's
    consolidation). Never applies anything; `self` is never a target.
    Enforces the weekly cap (<= 3 approved-or-pending per owner per ISO
    week, across the owner's workspaces): over it, raises
    `CoreBlockProposalCapReached` and nothing is stored."""
    _check_label(workspace, label)
    _check_budget(label, proposed_content)
    quote = (provenance_quote or "").strip()
    if not quote:
        raise CoreBlockInvalid("A core block proposal needs a provenance quote.")
    with transaction.atomic():
        # Serialize proposal creation per owner so two concurrent producers
        # cannot both slip under the cap.
        list(Workspace.objects.select_for_update().filter(owner_id=workspace.owner_id))
        start = _iso_week_start(timezone.now())
        used = CoreBlockProposal.objects.filter(
            workspace__owner_id=workspace.owner_id,
            created_at__gte=start,
            created_at__lt=start + timedelta(days=7),
            status__in=[CoreBlockProposal.Status.PENDING, CoreBlockProposal.Status.APPROVED],
        ).count()
        if used >= CORE_BLOCK_PROPOSALS_PER_WEEK:
            raise CoreBlockProposalCapReached("Weekly core block proposal cap reached.")
        current = CoreMemoryBlock.objects.filter(workspace=workspace, label=label).first()
        base_content = current.content if current else ""
        proposal = CoreBlockProposal.objects.create(
            workspace=workspace,
            label=label,
            proposed_content=proposed_content,
            diff=_render_diff(base_content, proposed_content),
            base_version=current.version if current else 0,
            provenance_quote=quote[:300],
            source_episode_id=source_episode_id,
            ingestion_source=ingestion_source,
            inferred=True,
        )
        record_audit(
            action="memory.core_block.proposed",
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=workspace,
            resource=proposal,
            metadata={"label": label, "base_version": proposal.base_version},
        )
    return proposal


def _locked_proposal(proposal):
    locked = CoreBlockProposal.objects.select_for_update().filter(pk=proposal.pk).first()
    if locked is None:
        raise InvalidTransition("Proposal no longer exists.")
    return locked


def _decide_proposal(proposal, status, actor):
    proposal.status = status
    proposal.decided_by = actor
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])


def approve_core_block_proposal(*, proposal, actor, channel):
    """Per-item owner approval over the web channel (D7, D10): applies the
    proposed content as a new version (`approved_proposal`). A proposal
    whose base version is stale is refused and left untouched."""
    _require_owner_web(actor=actor, workspace=proposal.workspace, channel=channel)
    with transaction.atomic():
        proposal = _locked_proposal(proposal)
        if proposal.status != CoreBlockProposal.Status.PENDING:
            raise InvalidTransition("Proposal is not pending.")
        block = _locked_block(proposal.workspace, proposal.label)
        if block.version != proposal.base_version:
            raise CoreBlockStale("The block changed since this proposal was made.")
        _write_block_version(
            block,
            content=proposal.proposed_content,
            sensitivity=block.sensitivity,
            kind=CoreMemoryBlock.WriterKind.APPROVED_PROPOSAL,
            actor=actor,
        )
        _decide_proposal(proposal, CoreBlockProposal.Status.APPROVED, actor)
        _count_learning_decision(proposal.ingestion_source_id, "proposals_accepted")
        record_audit(
            action="memory.core_block.proposal_approved",
            actor=actor,
            workspace=proposal.workspace,
            resource=proposal,
            metadata={"label": proposal.label, "version": block.version},
        )
    return proposal


def reject_core_block_proposal(*, proposal, actor, channel):
    _require_owner_web(actor=actor, workspace=proposal.workspace, channel=channel)
    with transaction.atomic():
        proposal = _locked_proposal(proposal)
        if proposal.status != CoreBlockProposal.Status.PENDING:
            raise InvalidTransition("Proposal is not pending.")
        _decide_proposal(proposal, CoreBlockProposal.Status.REJECTED, actor)
        _count_learning_decision(proposal.ingestion_source_id, "proposals_rejected")
        record_audit(
            action="memory.core_block.proposal_rejected",
            actor=actor,
            workspace=proposal.workspace,
            resource=proposal,
            metadata={"label": proposal.label},
        )
    return proposal


def record_core_block_view(*, actor, workspace, labels):
    """Audit an owner's API read of core blocks (ADR-005: every memory
    access is auditable) -- labels only, never content."""
    record_audit(
        action="memory.core_block.viewed",
        actor=actor,
        workspace=workspace,
        metadata={"labels": sorted(labels)},
    )
