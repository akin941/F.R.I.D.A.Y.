"""Graph writes (brief D2, D3, D7).

Who writes (D3):
- `propose_fact` -- the learning jobs, through `memory.graph.ports` only:
  born `pending_approval`, confidence capped at 90, a verbatim provenance
  quote required;
- `create_fact` / `edit_fact` / `supersede_fact` -- the owner, a human over
  `CHANNEL_WEB`, in a workspace they own: born `approved`, the owner's own
  words as the quote;
- Graphify `system` rows -- `ports.import_code_graph` (branch 5). They are
  never approvable, editable or trashable here (`SystemRowImmutable`, 405).

Temporal rules (D2): `supersede(old, new)` closes `old`'s window at
`new.valid_from` and links `old.superseded_by = new` atomically under
`select_for_update` of both rows (primary-key order). The supersede order
(`new.valid_from >= old.valid_from`) is checked in code before any write
(`SupersedeOrder`, 409); the DB check is only the backstop. Approved facts
are never edited in place.

Every owner mutation is audited with ids and counts only, never text.
"""

import uuid
from datetime import datetime, time

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.graph.candidates import normalise_name
from memory.ingestion.scrubber import scrub_text
from memory.models import (
    LEARNED_ENTITY_KINDS,
    LEARNED_PREDICATES,
    MemoryEntity,
    MemoryFact,
    ScrubTerm,
)
from memory.provenance import MIN_QUOTE_CHARS, normalise, stored_quote
from memory.services import InvalidTransition, MemoryPermissionDenied, MemoryServiceError
from workspaces.models import WorkspaceMember

Status = MemoryFact.Status
Origin = MemoryFact.Origin


# --- errors ------------------------------------------------------------------


class GraphError(MemoryServiceError):
    """Base of the graph errors; a bad request (400) unless a subclass says
    otherwise."""

    code = "memory.graph.invalid"


class GraphInvalid(GraphError):
    code = "memory.graph.invalid"


class GraphPermissionDenied(MemoryPermissionDenied):
    code = "memory.graph.permission_denied"


class GraphInvalidTransition(InvalidTransition):
    """The row is not in a state this action applies to (409)."""

    code = "memory.invalid_transition"


class SupersedeOrder(GraphError):
    """`new.valid_from < old.valid_from` (D2) -> 409, nothing written."""

    code = "memory.graph.supersede_order"


class SupersedeTargetChanged(GraphError):
    """The fact a proposal would replace is no longer current (D2) -> 409,
    nothing written, the proposal stays pending."""

    code = "memory.graph.supersede_target_changed"


class SystemRowImmutable(GraphError):
    """A Graphify `system` row through an owner write -> 405 (D3)."""

    code = "memory.graph.system_row"


# --- helpers -----------------------------------------------------------------


def _require_owner_web(actor, workspace, channel, action):
    if channel != CHANNEL_WEB or getattr(actor, "is_agent", False):
        raise GraphPermissionDenied(f"{action} is only allowed for a person over the web.")
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or not actor.is_active
        or not getattr(actor, "is_owner", False)
    ):
        raise GraphPermissionDenied(f"{action} requires the installation owner.")
    if not WorkspaceMember.objects.filter(
        workspace=workspace, user=actor, role=WorkspaceMember.Role.OWNER
    ).exists():
        raise GraphPermissionDenied(f"Only the workspace owner may do this: {action}.")


def require_owner_read(actor, workspace, channel):
    _require_owner_web(actor, workspace, channel, "Reading the memory graph")


def _terms(workspace):
    return [(str(t.id), t.term) for t in ScrubTerm.objects.filter(workspace=workspace)]


def _scrubbed(text, terms, limit):
    return normalise(scrub_text(text or "", terms=terms).text)[:limit]


def as_instant(value):
    """A date becomes local midnight; an aware datetime passes through."""
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return timezone.make_aware(datetime.combine(value, time.min))


def _audit(action, *, actor, workspace, resource=None, **metadata):
    from audit.models import AuditLog

    record_audit(
        action=action,
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor else AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        resource=resource,
        metadata=metadata,
    )


def _locked_facts(*facts):
    """Lock the rows in primary-key order (no deadlock between two
    supersedes of overlapping pairs) and return fresh copies, in the
    order given."""
    ids = sorted({f.pk for f in facts}, key=str)
    locked = {
        row.pk: row
        for row in MemoryFact.all_objects.select_for_update().filter(pk__in=ids).order_by("pk")
    }
    return [locked[f.pk] for f in facts]


def _is_current(fact):
    return (
        fact.status in (Status.APPROVED, Status.SYSTEM)
        and fact.valid_to is None
        and fact.deleted_at is None
    )


def _refuse_system(fact):
    if fact.origin == Origin.GRAPHIFY or fact.status == Status.SYSTEM:
        raise SystemRowImmutable("Graphify facts are replaced by the next import, not edited.")


PREDICATE_LABELS = {
    "decided": "kararı verdi:",
    "uses": "kullanıyor:",
    "depends_on": "bağımlı:",
    "deployed_to": "deploy edildiği yer:",
    "owned_by": "sahibi:",
    "works_on": "üzerinde çalışıyor:",
    "prefers": "tercih ediyor:",
    "blocked_by": "engelleyen:",
    "replaced_by": "yerini alan:",
    "has_status": "durumu:",
    "contains": "içeriyor:",
    "imports": "import ediyor:",
    "calls": "çağırıyor:",
    "inherits": "kalıtım alıyor:",
}


def render_statement(subject_name, predicate, object_name):
    """The default Turkish sentence when the writer gives none."""
    return f"{subject_name} {PREDICATE_LABELS.get(predicate, predicate)} {object_name}"[
        : MemoryFact.MAX_STATEMENT_CHARS
    ]


# --- entities ----------------------------------------------------------------


def find_entity(*, workspace, kind, name, terms=None, include_trashed=True):
    """The learned entity `resolve_entity` would pick for `(kind, name)`,
    without creating or reviving anything; None when it would create one
    (branch 2: reconciliation's subject lookup and the nightly new-entity
    cap). Read-only."""
    if kind not in LEARNED_ENTITY_KINDS:
        return None
    terms = _terms(workspace) if terms is None else terms
    normalised = normalise_name(_scrubbed(name, terms, MemoryEntity.MAX_NAME_CHARS))
    if not normalised:
        return None
    manager = MemoryEntity.all_objects if include_trashed else MemoryEntity.objects
    base = manager.filter(workspace=workspace, kind=kind, origin=MemoryEntity.Origin.LEARNED)
    return (
        base.filter(normalised_name=normalised).first()
        or base.filter(aliases__contains=[normalised]).order_by("created_at").first()
    )


def resolve_entity(*, workspace, kind, name, terms=None):
    """Entity resolution is code, not model (D4): an exact `normalised_name`
    or alias match within the workspace and kind (learned origin), else a
    new entity. A trashed match is revived (the writer named it again);
    `with_privacy()` and retrieval still hide it until an approved fact
    makes it visible. Caller holds the transaction."""
    if kind not in LEARNED_ENTITY_KINDS:
        raise GraphInvalid("Not a learned entity kind.")
    terms = _terms(workspace) if terms is None else terms
    clean = _scrubbed(name, terms, MemoryEntity.MAX_NAME_CHARS)
    normalised = normalise_name(clean)
    if not normalised:
        raise GraphInvalid("An entity needs a name.")
    base = MemoryEntity.all_objects.select_for_update().filter(
        workspace=workspace, kind=kind, origin=MemoryEntity.Origin.LEARNED
    )
    entity = base.filter(normalised_name=normalised).first()
    if entity is None:
        entity = base.filter(aliases__contains=[normalised]).order_by("created_at").first()
    if entity is None:
        entity, _ = MemoryEntity.all_objects.get_or_create(
            workspace=workspace,
            kind=kind,
            normalised_name=normalised,
            origin=MemoryEntity.Origin.LEARNED,
            defaults={"name": clean},
        )
    if entity.deleted_at is not None:
        entity.deleted_at = None
        entity.forget_operation_id = None
        entity.save(update_fields=["deleted_at", "forget_operation_id", "updated_at"])
    return entity


# --- the core temporal operation ----------------------------------------------


def _check_supersede_pair(old, new):
    if old.pk == new.pk:
        raise GraphInvalid("A fact cannot supersede itself.")
    if (old.workspace_id, old.subject_id, old.predicate) != (
        new.workspace_id,
        new.subject_id,
        new.predicate,
    ):
        raise GraphInvalid("Only a fact of the same workspace, subject and predicate.")
    if new.valid_from < old.valid_from:
        raise SupersedeOrder("The new fact starts before the fact it replaces.")


@transaction.atomic
def supersede(old, new):
    """Close `old`'s window at `new.valid_from` and set
    `old.superseded_by = new`, in one transaction under `select_for_update`
    of both rows. `old` must be current, `new` approved; same workspace,
    subject and predicate; `new.valid_from >= old.valid_from`
    (`SupersedeOrder`, both rows unchanged). Returns the updated `old`."""
    old, new = _locked_facts(old, new)
    _refuse_system(old)
    _check_supersede_pair(old, new)
    if not _is_current(old):
        raise SupersedeTargetChanged("The fact to replace is no longer current.")
    if new.status != Status.APPROVED or new.deleted_at is not None:
        raise GraphInvalidTransition("Only an approved fact can supersede another.")
    old.valid_to = new.valid_from
    old.superseded_by = new
    old.save(update_fields=["valid_to", "superseded_by", "updated_at"])
    return old


# --- learned proposals (the port) --------------------------------------------


@transaction.atomic
def propose_fact(
    *,
    workspace,
    subject,
    predicate,
    valid_from,
    provenance_quote,
    object_entity=None,
    object_text="",
    statement="",
    ingestion_source=None,
    source_episode_id=None,
    sensitivity=MemoryFact.Sensitivity.NORMAL,
    confidence=MemoryFact.MAX_PROPOSED_CONFIDENCE,
    supersedes=None,
):
    """Create one `pending_approval` learned fact (D3). `subject` and
    `object_entity` are `EntityRef`-like (`.kind`, `.name`) and resolved in
    code; texts are scrubbed with the workspace's terms. A `supersedes`
    target must be a current fact of the same workspace, subject and
    predicate, starting no later than this proposal (D2, D4). Called only by
    the learning jobs through `memory.graph.ports`; never approves."""
    if predicate not in LEARNED_PREDICATES:
        raise GraphInvalid("A model may propose only the learned predicate set.")
    if sensitivity not in MemoryFact.Sensitivity.values:
        raise GraphInvalid("Unknown sensitivity.")
    quote = stored_quote(provenance_quote)
    if len(quote) < MIN_QUOTE_CHARS:
        raise GraphInvalid("A learned fact needs a verbatim provenance quote.")
    if (object_entity is None) == (not object_text):
        raise GraphInvalid("A fact names exactly one object: an entity or a text.")
    terms = _terms(workspace)
    subject_row = resolve_entity(
        workspace=workspace, kind=subject.kind, name=subject.name, terms=terms
    )
    object_row = None
    object_clean = ""
    if object_entity is not None:
        object_row = resolve_entity(
            workspace=workspace, kind=object_entity.kind, name=object_entity.name, terms=terms
        )
    else:
        object_clean = _scrubbed(object_text, terms, MemoryFact.MAX_OBJECT_TEXT_CHARS)
        if not object_clean:
            raise GraphInvalid("The object text is empty.")
    statement = _scrubbed(statement, terms, MemoryFact.MAX_STATEMENT_CHARS) or render_statement(
        subject_row.name, predicate, object_row.name if object_row else object_clean
    )
    fact = MemoryFact(
        workspace=workspace,
        subject=subject_row,
        predicate=predicate,
        object_entity=object_row,
        object_text=object_clean,
        statement=statement,
        valid_from=as_instant(valid_from),
        status=Status.PENDING_APPROVAL,
        origin=Origin.LEARNED,
        ingestion_source=ingestion_source,
        source_episode_id=source_episode_id,
        provenance_quote=quote,
        sensitivity=sensitivity,
        confidence=max(0, min(int(confidence), MemoryFact.MAX_PROPOSED_CONFIDENCE)),
    )
    if supersedes is not None:
        target = MemoryFact.all_objects.select_for_update().get(pk=supersedes.pk)
        if (target.workspace_id, target.subject_id, target.predicate) != (
            workspace.pk,
            subject_row.pk,
            predicate,
        ) or target.origin != Origin.LEARNED:
            raise GraphInvalid("supersedes must name a learned fact of the same subject.")
        if not _is_current(target):
            raise SupersedeTargetChanged("The fact to replace is no longer current.")
        if fact.valid_from < target.valid_from:
            raise SupersedeOrder("The proposal starts before the fact it replaces.")
        fact.supersedes = target
    fact.save()
    _audit(
        "memory.graph.fact_proposed",
        actor=None,
        workspace=workspace,
        resource=fact,
        predicate=predicate,
        supersedes=str(fact.supersedes_id) if fact.supersedes_id else "",
        source_id=str(ingestion_source.pk) if ingestion_source else "",
    )
    return fact


# --- owner decisions on proposals ---------------------------------------------


def _decide(fact, status, actor):
    fact.status = status
    fact.decided_by = actor
    fact.decided_at = timezone.now()


def _count_decision(fact, field):
    from memory.services import _count_learning_decision

    _count_learning_decision(fact.ingestion_source_id, field)


@transaction.atomic
def approve_fact(*, fact, actor, channel):
    """Approve one pending proposal (D2, D3). With `supersedes` set, in one
    transaction: lock the proposal and its target (primary-key order),
    re-check that the target is still current, approve, clear the link and
    `supersede(old=target, new=proposal)`. A target no longer current ->
    `SupersedeTargetChanged`; an earlier `valid_from` -> `SupersedeOrder`;
    both leave every row unchanged and the proposal pending."""
    _require_owner_web(actor, fact.workspace, channel, "Approving a fact")
    _refuse_system(fact)
    if fact.supersedes_id is not None:
        target = MemoryFact.all_objects.get(pk=fact.supersedes_id)
        fact, target = _locked_facts(fact, target)
    else:
        (fact,) = _locked_facts(fact)
        target = None
    if fact.status != Status.PENDING_APPROVAL or fact.deleted_at is not None:
        raise GraphInvalidTransition("Only a pending proposal can be approved.")
    if fact.supersedes_id is not None and fact.supersedes_id != getattr(target, "pk", None):
        # The link changed between the read and the lock.
        raise SupersedeTargetChanged("The fact this proposal replaces has changed.")
    if target is not None:
        if not _is_current(target):
            raise SupersedeTargetChanged("The fact this proposal replaces has changed.")
        if fact.valid_from < target.valid_from:
            raise SupersedeOrder("The proposal starts before the fact it replaces.")
    _decide(fact, Status.APPROVED, actor)
    fact.supersedes = None
    fact.save(update_fields=["status", "decided_by", "decided_at", "supersedes", "updated_at"])
    if target is not None:
        supersede(target, fact)
    _count_decision(fact, "proposals_accepted")
    _audit(
        "memory.graph.fact_approved",
        actor=actor,
        workspace=fact.workspace,
        resource=fact,
        superseded=str(target.pk) if target else "",
    )
    return fact


@transaction.atomic
def reject_fact(*, fact, actor, channel):
    """Reject one pending proposal: the `supersedes` link is cleared and
    its target untouched (D2)."""
    _require_owner_web(actor, fact.workspace, channel, "Rejecting a fact")
    _refuse_system(fact)
    (fact,) = _locked_facts(fact)
    if fact.status != Status.PENDING_APPROVAL or fact.deleted_at is not None:
        raise GraphInvalidTransition("Only a pending proposal can be rejected.")
    _decide(fact, Status.REJECTED, actor)
    fact.supersedes = None
    fact.save(update_fields=["status", "decided_by", "decided_at", "supersedes", "updated_at"])
    _count_decision(fact, "proposals_rejected")
    _audit("memory.graph.fact_rejected", actor=actor, workspace=fact.workspace, resource=fact)
    return fact


# --- owner-authored facts -------------------------------------------------------


def _owner_object(workspace, terms, object_kind, object_name, object_text):
    if object_text and (object_kind or object_name):
        raise GraphInvalid("Name either an object entity or an object text, not both.")
    if object_text:
        clean = _scrubbed(object_text, terms, MemoryFact.MAX_OBJECT_TEXT_CHARS)
        if not clean:
            raise GraphInvalid("The object text is empty.")
        return None, clean
    if not (object_kind and object_name):
        raise GraphInvalid("A fact names exactly one object: an entity or a text.")
    return (
        resolve_entity(workspace=workspace, kind=object_kind, name=object_name, terms=terms),
        "",
    )


def _owner_statement(statement, terms, subject, predicate, object_row, object_text):
    clean = _scrubbed(statement, terms, MemoryFact.MAX_STATEMENT_CHARS)
    return clean or render_statement(
        subject.name, predicate, object_row.name if object_row else object_text
    )


def _new_owner_fact(
    *,
    workspace,
    actor,
    subject,
    predicate,
    object_row,
    object_text,
    statement,
    valid_from,
    sensitivity,
):
    # The owner's typed statement is the provenance quote (D3): the owner's
    # own words, and the audit row names the writer.
    fact = MemoryFact(
        workspace=workspace,
        subject=subject,
        predicate=predicate,
        object_entity=object_row,
        object_text=object_text,
        statement=statement,
        valid_from=valid_from,
        status=Status.APPROVED,
        origin=Origin.LEARNED,
        provenance_quote=stored_quote(statement),
        sensitivity=sensitivity,
        confidence=100,
        created_by=actor,
        decided_by=actor,
        decided_at=timezone.now(),
    )
    fact.save()
    return fact


@transaction.atomic
def create_fact(
    *,
    workspace,
    actor,
    channel,
    subject_kind,
    subject_name,
    predicate,
    valid_from=None,
    object_kind="",
    object_name="",
    object_text="",
    statement="",
    sensitivity=MemoryFact.Sensitivity.NORMAL,
):
    """An owner-typed fact (D3): born `approved`."""
    _require_owner_web(actor, workspace, channel, "Adding a fact")
    if predicate not in LEARNED_PREDICATES:
        raise GraphInvalid("Owner facts use the learned predicate set.")
    if sensitivity not in MemoryFact.Sensitivity.values:
        raise GraphInvalid("Unknown sensitivity.")
    terms = _terms(workspace)
    subject = resolve_entity(workspace=workspace, kind=subject_kind, name=subject_name, terms=terms)
    object_row, object_clean = _owner_object(
        workspace, terms, object_kind, object_name, object_text
    )
    fact = _new_owner_fact(
        workspace=workspace,
        actor=actor,
        subject=subject,
        predicate=predicate,
        object_row=object_row,
        object_text=object_clean,
        statement=_owner_statement(statement, terms, subject, predicate, object_row, object_clean),
        valid_from=as_instant(valid_from) if valid_from else timezone.now(),
        sensitivity=sensitivity,
    )
    _audit(
        "memory.graph.fact_created", actor=actor, workspace=workspace, resource=fact, writer="owner"
    )
    return fact


def _current_head(fact):
    """Follow `superseded_by` to the current version, or None."""
    seen = set()
    row = fact
    while row is not None and row.pk not in seen:
        if _is_current(row):
            return row
        seen.add(row.pk)
        row = (
            MemoryFact.all_objects.filter(pk=row.superseded_by_id).first()
            if row.superseded_by_id
            else None
        )
    return None


_UNSET = object()


@transaction.atomic
def edit_fact(
    *,
    fact,
    actor,
    channel,
    statement=_UNSET,
    valid_from=_UNSET,
    object_kind=_UNSET,
    object_name=_UNSET,
    object_text=_UNSET,
    sensitivity=_UNSET,
):
    """An owner edit is applied at once and never stays pending (D2):

    - an **approved current** fact gets a new approved version that
      supersedes it (`valid_from` defaults to now; earlier than the old
      row's -> `SupersedeOrder`, nothing written);
    - a **pending** proposal is rejected and replaced by the owner's
      approved version; if it carried `supersedes`, the version supersedes
      the current head of that target's chain (a target trashed or
      archived meanwhile is left alone and the version stands as new).

    Subject and predicate never change through an edit (a different subject
    is a different fact)."""
    _require_owner_web(actor, fact.workspace, channel, "Editing a fact")
    _refuse_system(fact)
    (fact,) = _locked_facts(fact)
    if fact.deleted_at is not None:
        raise GraphInvalidTransition("A trashed fact cannot be edited.")
    pending = fact.status == Status.PENDING_APPROVAL
    if not pending and not _is_current(fact):
        raise GraphInvalidTransition("Only a current or pending fact can be edited.")
    workspace = fact.workspace
    terms = _terms(workspace)
    if sensitivity is not _UNSET and sensitivity not in MemoryFact.Sensitivity.values:
        raise GraphInvalid("Unknown sensitivity.")
    new_sensitivity = fact.sensitivity if sensitivity is _UNSET else sensitivity
    if object_kind is _UNSET and object_name is _UNSET and object_text is _UNSET:
        object_row, object_clean = fact.object_entity, fact.object_text
    else:
        object_row, object_clean = _owner_object(
            workspace,
            terms,
            "" if object_kind is _UNSET else object_kind,
            "" if object_name is _UNSET else object_name,
            "" if object_text is _UNSET else object_text,
        )
    if statement is _UNSET:
        new_statement = (
            fact.statement
            if (object_row, object_clean) == (fact.object_entity, fact.object_text)
            else ""
        )
    else:
        new_statement = statement
    new_statement = _owner_statement(
        new_statement, terms, fact.subject, fact.predicate, object_row, object_clean
    )
    if valid_from is _UNSET:
        new_valid_from = fact.valid_from if pending else timezone.now()
    else:
        new_valid_from = as_instant(valid_from)

    if pending:
        target = (
            _current_head(MemoryFact.all_objects.get(pk=fact.supersedes_id))
            if fact.supersedes_id
            else None
        )
    else:
        target = fact
    if target is not None and new_valid_from < target.valid_from:
        raise SupersedeOrder("The edited fact would start before the fact it replaces.")

    if pending:
        _decide(fact, Status.REJECTED, actor)
        fact.supersedes = None
        fact.save(update_fields=["status", "decided_by", "decided_at", "supersedes", "updated_at"])
        _count_decision(fact, "proposals_accepted")
    version = _new_owner_fact(
        workspace=workspace,
        actor=actor,
        subject=fact.subject,
        predicate=fact.predicate,
        object_row=object_row,
        object_text=object_clean,
        statement=new_statement,
        valid_from=new_valid_from,
        sensitivity=new_sensitivity,
    )
    if target is not None:
        supersede(target, version)
    _audit(
        "memory.graph.fact_edited",
        actor=actor,
        workspace=workspace,
        resource=version,
        edited=str(fact.pk),
        superseded=str(target.pk) if target else "",
        was_pending=pending,
    )
    return version


@transaction.atomic
def supersede_fact(
    *,
    fact,
    actor,
    channel,
    valid_from,
    statement="",
    object_kind="",
    object_name="",
    object_text="",
):
    """The owner replaces a current fact with a new value from
    `valid_from` (the API's `facts/<id>/supersede`): an `edit_fact` of an
    approved current fact with an explicit date and object."""
    _refuse_system(fact)
    if fact.status != Status.APPROVED:
        raise GraphInvalidTransition("Only an approved current fact can be superseded.")
    return edit_fact(
        fact=fact,
        actor=actor,
        channel=channel,
        statement=statement,
        valid_from=valid_from,
        object_kind=object_kind,
        object_name=object_name,
        object_text=object_text,
    )


# --- owner trash (ADR-009) ---------------------------------------------------------


@transaction.atomic
def trash_fact(*, fact, actor, channel):
    """Owner delete of one fact: a reversible trash move (ADR-009). A
    pending proposal that supersedes it keeps its link and is refused at
    approval (`SupersedeTargetChanged`)."""
    _require_owner_web(actor, fact.workspace, channel, "Deleting a fact")
    _refuse_system(fact)
    (fact,) = _locked_facts(fact)
    if fact.deleted_at is not None:
        raise GraphInvalidTransition("The fact is already in the trash.")
    if fact.status == Status.APPROVED and fact.valid_to is not None:
        # Superseded history stays readable (D2); trashing it would also
        # race an approval that just closed its window (a closed window on
        # a trashed row). Trash the current version or the entity instead.
        raise GraphInvalidTransition("A superseded version is history and stays readable.")
    fact.deleted_at = timezone.now()
    fact.embedding = None
    fact.embedding_provider = None
    fact.save(update_fields=["deleted_at", "embedding", "embedding_provider", "updated_at"])
    _audit("memory.graph.fact_trashed", actor=actor, workspace=fact.workspace, resource=fact)
    return fact


def _revive_entities(*entities):
    for entity in entities:
        if entity is not None and entity.deleted_at is not None:
            entity.deleted_at = None
            entity.forget_operation_id = None
            entity.save(update_fields=["deleted_at", "forget_operation_id", "updated_at"])


@transaction.atomic
def restore_fact(*, fact, actor, channel):
    """Undo an owner trash of one fact (and revive its entities)."""
    _require_owner_web(actor, fact.workspace, channel, "Restoring a fact")
    _refuse_system(fact)
    (fact,) = _locked_facts(fact)
    if fact.deleted_at is None:
        raise GraphInvalidTransition("The fact is not in the trash.")
    _revive_entities(fact.subject, fact.object_entity)
    fact.deleted_at = None
    fact.save(update_fields=["deleted_at", "updated_at"])
    _audit("memory.graph.fact_restored", actor=actor, workspace=fact.workspace, resource=fact)
    return fact


def _entity_facts(entity):
    return MemoryFact.all_objects.filter(
        Q(subject=entity) | Q(object_entity=entity), origin=Origin.LEARNED
    )


@transaction.atomic
def trash_entity(*, entity, actor, channel):
    """Owner delete of one entity: it and its learned facts move to the
    trash together (D7, ADR-009), sharing one `deleted_at` instant, which
    is how `restore_entity` finds exactly them (a fact's
    `forget_operation_id` belongs to forget-source and is left alone).
    Graphify entities -> 405."""
    _require_owner_web(actor, entity.workspace, channel, "Deleting an entity")
    entity = MemoryEntity.all_objects.select_for_update().get(pk=entity.pk)
    if entity.origin == MemoryEntity.Origin.GRAPHIFY:
        raise SystemRowImmutable("Graphify entities are replaced by the next import.")
    if entity.deleted_at is not None:
        raise GraphInvalidTransition("The entity is already in the trash.")
    operation_id = uuid.uuid4()
    now = timezone.now()
    facts = _entity_facts(entity).filter(deleted_at__isnull=True)
    trashed = facts.update(deleted_at=now, embedding=None, embedding_provider=None)
    entity.deleted_at = now
    entity.forget_operation_id = operation_id
    entity.save(update_fields=["deleted_at", "forget_operation_id", "updated_at"])
    _audit(
        "memory.graph.entity_trashed",
        actor=actor,
        workspace=entity.workspace,
        resource=entity,
        operation_id=str(operation_id),
        facts=trashed,
    )
    return {"operation_id": str(operation_id), "facts": trashed}


@transaction.atomic
def restore_entity(*, entity, actor, channel):
    """Undo `trash_entity`: the entity and exactly the facts that operation
    trashed come back."""
    _require_owner_web(actor, entity.workspace, channel, "Restoring an entity")
    entity = MemoryEntity.all_objects.select_for_update().get(pk=entity.pk)
    if entity.deleted_at is None:
        raise GraphInvalidTransition("The entity is not in the trash.")
    facts = _entity_facts(entity).filter(deleted_at=entity.deleted_at)
    for fact in facts.select_related("subject", "object_entity"):
        _revive_entities(
            *(e for e in (fact.subject, fact.object_entity) if e and e.pk != entity.pk)
        )
    restored = facts.update(deleted_at=None)
    entity.deleted_at = None
    entity.forget_operation_id = None
    entity.save(update_fields=["deleted_at", "forget_operation_id", "updated_at"])
    _audit(
        "memory.graph.entity_restored",
        actor=actor,
        workspace=entity.workspace,
        resource=entity,
        facts=restored,
    )
    return {"facts": restored}


# --- forget-source cascade (D7), called by memory.ingestion.services -------------


def forget_source_facts(source, operation_id):
    """Archive the source's approved learned facts and reject its pending
    proposals (clearing their `supersedes` links; the targets stay
    current), marked with `operation_id` and their prior status. An entity
    left with no live fact is trashed with the same id. Caller holds the
    transaction. Graphify rows are never touched."""
    facts = MemoryFact.all_objects.filter(
        ingestion_source=source, origin=Origin.LEARNED, deleted_at__isnull=True
    )
    touched_entities = set()
    for subject_id, object_id in facts.filter(
        status__in=[Status.APPROVED, Status.PENDING_APPROVAL]
    ).values_list("subject_id", "object_entity_id"):
        touched_entities.update(pk for pk in (subject_id, object_id) if pk)
    archived = facts.filter(status=Status.APPROVED).update(
        status=Status.ARCHIVED,
        forget_operation_id=operation_id,
        forget_prior_status=Status.APPROVED,
        embedding=None,
        embedding_provider=None,
    )
    rejected = facts.filter(status=Status.PENDING_APPROVAL).update(
        status=Status.REJECTED,
        forget_operation_id=operation_id,
        forget_prior_status=Status.PENDING_APPROVAL,
        supersedes=None,
    )
    live = Q(status__in=[Status.APPROVED, Status.PENDING_APPROVAL, Status.SYSTEM]) & Q(
        deleted_at__isnull=True
    )
    orphaned = [
        pk
        for pk in touched_entities
        if not MemoryFact.all_objects.filter(live)
        .filter(Q(subject_id=pk) | Q(object_entity_id=pk))
        .exists()
    ]
    entities = MemoryEntity.all_objects.filter(
        pk__in=orphaned, origin=MemoryEntity.Origin.LEARNED, deleted_at__isnull=True
    ).update(deleted_at=timezone.now(), forget_operation_id=operation_id)
    return {"facts_archived": archived, "facts_rejected": rejected, "entities_trashed": entities}


def restore_source_facts(source, operation_id):
    """Bring back exactly the facts and entities `forget_source_facts`
    marked. A restored proposal comes back pending without `supersedes`
    (the target may have changed meanwhile, D7)."""
    entities = MemoryEntity.all_objects.filter(
        forget_operation_id=operation_id, deleted_at__isnull=False
    ).update(deleted_at=None, forget_operation_id=None)
    marked = MemoryFact.all_objects.filter(
        ingestion_source=source, forget_operation_id=operation_id, deleted_at__isnull=True
    )
    rows = list(marked.values_list("id", "forget_prior_status"))
    for status in {prior for _, prior in rows}:
        MemoryFact.all_objects.filter(pk__in=[pk for pk, prior in rows if prior == status]).update(
            status=status, forget_operation_id=None, forget_prior_status=""
        )
    return {"facts": len(rows), "entities": entities}


def forget_preview_facts(source):
    return MemoryFact.all_objects.filter(
        ingestion_source=source,
        origin=Origin.LEARNED,
        deleted_at__isnull=True,
        status__in=[Status.APPROVED, Status.PENDING_APPROVAL],
    ).count()


# --- purge (gate-class `purge_trash`, ADR-009) --------------------------------------


def purgeable_facts(workspace):
    """`purge_trash`'s `fact_ids` scope: learned facts in the owner trash,
    or archived/rejected by a forget operation and still so."""
    return MemoryFact.all_objects.filter(workspace=workspace, origin=Origin.LEARNED).filter(
        Q(deleted_at__isnull=False)
        | Q(forget_operation_id__isnull=False, status__in=[Status.ARCHIVED, Status.REJECTED])
    )


def purgeable_entities(workspace):
    """`purge_trash`'s `entity_ids` scope: trashed learned entities."""
    return MemoryEntity.all_objects.filter(
        workspace=workspace, origin=MemoryEntity.Origin.LEARNED, deleted_at__isnull=False
    )


def _purge_audit(action, row, *, actor, approval_request_id):
    _audit(
        action,
        actor=actor,
        workspace=row.workspace,
        resource=row,
        reason="approval",
        approval_request_id=approval_request_id or "",
    )


def _release_pending_supersessions(fact):
    """Reject every pending proposal whose `supersedes` names `fact`
    (clearing the link), so the PROTECT FK never blocks a gated purge."""
    return MemoryFact.all_objects.filter(supersedes=fact, status=Status.PENDING_APPROVAL).update(
        status=Status.REJECTED, supersedes=None, decided_at=timezone.now()
    )


def _delete_fact(fact, *, actor, approval_request_id):
    _release_pending_supersessions(fact)
    _purge_audit(
        "memory.graph.fact_purged", fact, actor=actor, approval_request_id=approval_request_id
    )
    fact.delete()


def purge_fact(fact, *, actor=None, approval_request_id=None):
    """Permanently delete one purgeable fact -- only from an approved
    `purge_trash` request. A row restored meanwhile is left alone. Caller
    holds the transaction."""
    if not purgeable_facts(fact.workspace).filter(pk=fact.pk).exists():
        return False
    _delete_fact(fact, actor=actor, approval_request_id=approval_request_id)
    return True


def purge_entity(entity, *, actor=None, approval_request_id=None):
    """Permanently delete one trashed learned entity and the non-live facts
    that still reference it -- only from an approved `purge_trash` request.
    An entity restored meanwhile, or one a live fact references again, is
    left alone."""
    if entity.deleted_at is None or entity.origin != MemoryEntity.Origin.LEARNED:
        return False
    referencing = MemoryFact.all_objects.filter(Q(subject=entity) | Q(object_entity=entity))
    live = referencing.filter(
        status__in=[Status.APPROVED, Status.PENDING_APPROVAL, Status.SYSTEM],
        deleted_at__isnull=True,
    )
    if live.exists():
        return False
    for fact in referencing.select_related("workspace"):
        _delete_fact(fact, actor=actor, approval_request_id=approval_request_id)
    _purge_audit(
        "memory.graph.entity_purged", entity, actor=actor, approval_request_id=approval_request_id
    )
    entity.delete()
    return True
