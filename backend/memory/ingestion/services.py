"""Ingestion services (Phase 8A branch 1, ADR-012): the owner's control
surface over sources, mapping rules and scrub terms; forget/restore source
(episode side; the memory cascade lands in branch 5); the TTL purge; the
audit counters.

Every owner-facing mutation requires a **human actor over `CHANNEL_WEB`**
who holds the `owner` role in the row's workspace -- `channel` is a
required argument, never defaulted (the ADR-009 shape). Every one is
audited with ids, kinds and counts only, never episode text.
"""

import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from audit.services import record_audit
from core.channels import CHANNEL_WEB
from memory.ingestion.mapping import (
    InvalidMappingKey,
    normalise_pattern,
    owner_owns,
)
from memory.ingestion.scrubber import merge_reports
from memory.models import (
    CoreBlockProposal,
    Episode,
    EpisodeExtractionJob,
    IngestionCounter,
    IngestionSource,
    Memory,
    ScrubTerm,
    WorkspaceMappingRule,
)
from workspaces.models import Workspace, WorkspaceMember


class IngestionError(Exception):
    """Base class; views translate subclasses to HTTP statuses."""


class IngestionPermissionDenied(IngestionError):
    """→ 403. Shares the memory app's stable error code."""

    code = "memory.permission_denied"


class IngestionInvalid(IngestionError):
    """→ 400. Shares the memory app's stable error code."""

    code = "memory.invalid_transition"


# --- authorization -----------------------------------------------------------


def _require_owner(actor, workspace, channel, action):
    if channel != CHANNEL_WEB or getattr(actor, "is_agent", False):
        raise IngestionPermissionDenied(f"{action} is only allowed for a person over the web.")
    if not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise IngestionPermissionDenied(f"{action} requires an active user.")
    if not owner_owns(actor, workspace):
        raise IngestionPermissionDenied(f"Only the workspace owner may do this: {action}.")


def require_owner_read(actor, workspace, channel):
    _require_owner(actor, workspace, channel, "Reading ingestion data")


# --- counters ----------------------------------------------------------------


def _counter(source, day=None):
    day = day or timezone.localdate()
    counter, _ = IngestionCounter.objects.get_or_create(
        source=source, day=day, defaults={"workspace_id": source.workspace_id}
    )
    return counter


def bump_counter(source, field, amount=1):
    """Atomic increment of one integer counter for today."""
    counter = _counter(source)
    IngestionCounter.objects.filter(pk=counter.pk).update(**{field: F(field) + amount})


@transaction.atomic
def record_scrub_hits(source, report):
    if not report:
        return
    counter = IngestionCounter.objects.select_for_update().get(pk=_counter(source).pk)
    counter.scrubber_hits = merge_reports(counter.scrubber_hits, report)
    counter.save(update_fields=["scrubber_hits", "updated_at"])


@transaction.atomic
def record_learning_drops(source, reasons):
    """Add `{reason: count}` to today's learning drop counts (branch 5, D9/
    D10); `no_provenance` also feeds the typed
    `proposals_dropped_no_provenance` counter. Counts only, never text."""
    reasons = {k: int(v) for k, v in (reasons or {}).items() if v}
    if not reasons:
        return
    counter = IngestionCounter.objects.select_for_update().get(pk=_counter(source).pk)
    drops = dict(counter.learning_drops or {})
    for reason, count in reasons.items():
        drops[reason] = int(drops.get(reason, 0)) + count
    counter.learning_drops = drops
    counter.proposals_dropped_no_provenance += reasons.get("no_provenance", 0)
    counter.save(update_fields=["learning_drops", "proposals_dropped_no_provenance", "updated_at"])


def tokens_spent_today(source):
    counter = IngestionCounter.objects.filter(source=source, day=timezone.localdate()).first()
    return counter.tokens_spent if counter else 0


@transaction.atomic
def record_unmapped(source, entry):
    counter = IngestionCounter.objects.select_for_update().get(pk=_counter(source).pk)
    counter.dropped_unmapped += 1
    keys = [k for k in counter.unmapped_keys if k.get("hash") != entry["hash"]]
    keys.insert(0, entry)
    counter.unmapped_keys = keys[: IngestionCounter.UNMAPPED_KEYS_CAP]
    counter.save(update_fields=["dropped_unmapped", "unmapped_keys", "updated_at"])


# --- TTL ---------------------------------------------------------------------


def ttl_days_for(workspace):
    from django.conf import settings

    if workspace.type == Workspace.Type.COMPANY:
        return settings.MEMORY_EPISODE_TTL_DAYS_FIRM
    return settings.MEMORY_EPISODE_TTL_DAYS_PERSONAL


def expires_at_for(workspace, now=None):
    return (now or timezone.now()) + timedelta(days=ttl_days_for(workspace))


def purge_expired_episodes(now=None):
    """Daily beat (d10, ADR-012 decision 4): hard-delete every episode past
    its `expires_at`, trashed or not. A retention rule the owner accepted in
    d10, not an owner delete. Only expired rows are touched."""
    now = now or timezone.now()
    deleted, _ = Episode.all_objects.filter(expires_at__lte=now).delete()
    return deleted


def purge_stale_extraction_jobs(now=None):
    """Branch 5: delete extraction jobs older than their workspace's
    episode TTL -- a job's waiting candidates derive from episode text and
    must not outlive it (d10)."""
    from django.conf import settings

    now = now or timezone.now()
    personal = now - timedelta(days=settings.MEMORY_EPISODE_TTL_DAYS_PERSONAL)
    firm = now - timedelta(days=settings.MEMORY_EPISODE_TTL_DAYS_FIRM)
    deleted, _ = EpisodeExtractionJob.objects.filter(
        Q(workspace__type=Workspace.Type.COMPANY, created_at__lt=firm) | Q(created_at__lt=personal)
    ).delete()
    return deleted


# --- seed --------------------------------------------------------------------


def ensure_default_sources(*, workspace, created_by=None):
    """One disabled source per kind in the owner's personal workspace
    (brief D2). Idempotent; never enables or modifies an existing row."""
    if workspace.type != Workspace.Type.PERSONAL:
        raise IngestionInvalid("Ingestion sources live in the owner's personal workspace.")
    created = []
    for kind in IngestionSource.Kind.values:
        source, was_created = IngestionSource.objects.get_or_create(
            workspace=workspace,
            kind=kind,
            defaults={"enabled": False, "created_by": created_by},
        )
        if was_created:
            created.append(source)
            record_audit(
                action="memory.ingestion.source_created",
                actor=created_by,
                workspace=workspace,
                resource=source,
                metadata={"kind": kind, "enabled": False},
            )
    return created


# --- sources -----------------------------------------------------------------


def _locked_source(source):
    return IngestionSource.objects.select_for_update().get(pk=source.pk)


@transaction.atomic
def update_source(source, *, actor, channel, enabled=None, paused=None, daily_token_budget=None):
    """Enable/disable, pause/resume, set the daily budget. Audited."""
    _require_owner(actor, source.workspace, channel, "Changing an ingestion source")
    source = _locked_source(source)
    changed = []
    if enabled is not None and source.enabled != enabled:
        source.enabled = enabled
        changed.append("enabled")
    if paused is not None:
        if paused and source.paused_at is None:
            source.paused_at = timezone.now()
            changed.append("paused_at")
        elif not paused and source.paused_at is not None:
            source.paused_at = None
            changed.append("paused_at")
    if daily_token_budget is not None and source.daily_token_budget != daily_token_budget:
        source.daily_token_budget = daily_token_budget
        changed.append("daily_token_budget")
    if changed:
        source.save(update_fields=[*changed, "updated_at"])
        record_audit(
            action="memory.ingestion.source_updated",
            actor=actor,
            workspace=source.workspace,
            resource=source,
            metadata={
                "kind": source.kind,
                "fields_changed": sorted(changed),
                "enabled": source.enabled,
                "paused": source.paused_at is not None,
            },
        )
    return source


@transaction.atomic
def forget_source(source, *, actor, channel):
    """Forget source X (ADR-012 decision 6, brief D11) -- episode side.

    In one transaction: pause the source, move every live episode of it to
    the trash (`deleted_at`), marking them with a fresh operation id. A
    reversible confirm-class move (ADR-009): `restore_source` undoes exactly
    the rows marked by this operation while they are still in the trash;
    permanent purge is the gate-class `purge_trash` (branch 5) or TTL. The
    cascade to derived memories and proposals lands in branch 5."""
    _require_owner(actor, source.workspace, channel, "Forgetting an ingestion source")
    source = _locked_source(source)
    now = timezone.now()
    operation_id = uuid.uuid4()
    episodes = Episode.all_objects.filter(source=source, deleted_at__isnull=True)
    trashed = episodes.update(deleted_at=now, forget_operation_id=operation_id)
    # Branch 5 cascade: every memory derived from the source (found through
    # `ingestion_source`, so it works after the episodes expired) --
    # approved -> archived, pending -> rejected -- and every pending
    # core-block proposal -> rejected, each marked with the operation id and
    # its prior status so restore undoes exactly these rows.
    archived = Memory.objects.filter(ingestion_source=source, status=Memory.Status.APPROVED).update(
        status=Memory.Status.ARCHIVED,
        forget_operation_id=operation_id,
        forget_prior_status=Memory.Status.APPROVED,
        # An archived row's vector must not persist (memory.services'
        # embedding reconciliation rule).
        embedding=None,
        embedding_provider=None,
    )
    rejected = Memory.objects.filter(
        ingestion_source=source, status=Memory.Status.PENDING_APPROVAL
    ).update(
        status=Memory.Status.REJECTED,
        forget_operation_id=operation_id,
        forget_prior_status=Memory.Status.PENDING_APPROVAL,
    )
    proposals = CoreBlockProposal.objects.filter(
        ingestion_source=source, status=CoreBlockProposal.Status.PENDING
    ).update(
        status=CoreBlockProposal.Status.REJECTED,
        forget_operation_id=operation_id,
        forget_prior_status=CoreBlockProposal.Status.PENDING,
    )
    candidates = _strip_waiting_candidates(source)
    source.paused_at = source.paused_at or now
    source.forgotten_at = now
    source.forget_operation_id = operation_id
    source.save(update_fields=["paused_at", "forgotten_at", "forget_operation_id", "updated_at"])
    counts = {
        "episodes": trashed,
        "memories_archived": archived,
        "memories_rejected": rejected,
        "core_block_proposals": proposals,
        "candidates_discarded": candidates,
    }
    record_audit(
        action="memory.source_forgotten",
        actor=actor,
        workspace=source.workspace,
        resource=source,
        metadata={"kind": source.kind, "operation_id": str(operation_id), **counts},
    )
    return {"operation_id": str(operation_id), **counts}


def _strip_waiting_candidates(source):
    """Drop the source's not-yet-consolidated candidates from extraction
    jobs (they quote now-trashed episodes). Not restorable: the episodes
    come back extracted, and the next real item from the source is learned
    normally."""
    removed = 0
    source_id = str(source.id)
    jobs = EpisodeExtractionJob.objects.select_for_update().filter(consolidated_at__isnull=True)
    for job in jobs:
        kept = [c for c in job.candidates if c.get("source_id") != source_id]
        if len(kept) != len(job.candidates):
            removed += len(job.candidates) - len(kept)
            job.candidates = kept
            job.save(update_fields=["candidates", "updated_at"])
    return removed


@transaction.atomic
def restore_source(source, *, actor, channel):
    """Undo the last forget operation: bring back exactly the episodes it
    trashed that are still in the trash (expired ones stay gone -- TTL).
    The source stays paused; the owner resumes it explicitly."""
    _require_owner(actor, source.workspace, channel, "Restoring an ingestion source")
    source = _locked_source(source)
    if source.forget_operation_id is None:
        raise IngestionInvalid("This source has no forget operation to restore.")
    operation_id = source.forget_operation_id
    restored = Episode.all_objects.filter(
        source=source,
        forget_operation_id=operation_id,
        deleted_at__isnull=False,
        expires_at__gt=timezone.now(),
    ).update(deleted_at=None, forget_operation_id=None)
    memories = _restore_memories(source, operation_id)
    proposals = CoreBlockProposal.objects.filter(
        ingestion_source=source, forget_operation_id=operation_id
    ).update(status=F("forget_prior_status"), forget_operation_id=None, forget_prior_status="")
    source.forgotten_at = None
    source.forget_operation_id = None
    source.save(update_fields=["forgotten_at", "forget_operation_id", "updated_at"])
    counts = {"episodes": restored, "memories": memories, "core_block_proposals": proposals}
    record_audit(
        action="memory.source_restored",
        actor=actor,
        workspace=source.workspace,
        resource=source,
        metadata={"kind": source.kind, "operation_id": str(operation_id), **counts},
    )
    return {"operation_id": str(operation_id), **counts}


def _restore_memories(source, operation_id):
    """Put back exactly the memories this forget operation marked, at their
    prior status. A row the private-conversation cascade locked meanwhile
    stays archived (`privacy_locked`, memory.services.restore_memory's
    rule); a restored approved row is re-embedded."""
    from memory.jobs import dispatch_memory_embedding

    marked = Memory.objects.filter(
        ingestion_source=source, forget_operation_id=operation_id, privacy_locked=False
    )
    rows = list(marked.values_list("id", "forget_prior_status"))
    for status in {prior for _, prior in rows}:
        Memory.objects.filter(pk__in=[pk for pk, prior in rows if prior == status]).update(
            status=status, forget_operation_id=None, forget_prior_status=""
        )
    for pk, prior in rows:
        if prior == Memory.Status.APPROVED:
            transaction.on_commit(lambda pk=pk: dispatch_memory_embedding(pk))
    return len(rows)


def trashed_episodes(workspace):
    """Episodes in this workspace's trash (a forget-source move), for the
    gate-class `purge_trash` (ADR-009)."""
    return Episode.all_objects.filter(workspace=workspace, deleted_at__isnull=False)


def forgotten_memories(workspace):
    """Memories a forget-source operation archived/rejected and that are
    still in that state -- `purge_trash`'s `forgotten_memory_ids` scope."""
    return Memory.objects.filter(
        workspace=workspace,
        forget_operation_id__isnull=False,
        status__in=[Memory.Status.ARCHIVED, Memory.Status.REJECTED],
    )


def _purge_audit(action, row, *, actor, approval_request_id):
    from audit.models import AuditLog

    record_audit(
        action=action,
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor else AuditLog.ActorType.SYSTEM,
        workspace=row.workspace,
        resource=row,
        metadata={"reason": "approval", "approval_request_id": approval_request_id or ""},
    )


def purge_trashed_episode(episode, *, actor=None, approval_request_id=None):
    """Permanently delete one trashed episode -- only from an approved
    `purge_trash` request (or the TTL beat). Caller holds the transaction;
    a row restored meanwhile is left alone."""
    if episode.deleted_at is None:
        return False
    _purge_audit(
        "memory.episode_purged", episode, actor=actor, approval_request_id=approval_request_id
    )
    episode.delete()
    return True


def purge_forgotten_memory(memory, *, actor=None, approval_request_id=None):
    """Permanently delete one memory a forget-source operation archived or
    rejected -- only from an approved `purge_trash` request. A row restored
    meanwhile is left alone."""
    if memory.forget_operation_id is None or memory.status not in (
        Memory.Status.ARCHIVED,
        Memory.Status.REJECTED,
    ):
        return False
    _purge_audit("memory.purged", memory, actor=actor, approval_request_id=approval_request_id)
    memory.delete()
    return True


def forget_preview(source):
    """Counts the forget confirmation names (ADR-009 conditions)."""
    return {
        "episodes": Episode.all_objects.filter(source=source, deleted_at__isnull=True).count(),
        "memories": Memory.objects.filter(
            ingestion_source=source,
            status__in=[Memory.Status.APPROVED, Memory.Status.PENDING_APPROVAL],
        ).count(),
        "core_block_proposals": CoreBlockProposal.objects.filter(
            ingestion_source=source, status=CoreBlockProposal.Status.PENDING
        ).count(),
    }


# --- mapping rules -----------------------------------------------------------


def _validate_target(actor, source, target_workspace):
    # The target must be a workspace the source's owner -- the actor --
    # owns: a firm repo or calendar therefore lands only in the firm
    # workspace (D4).
    if source.workspace.owner_id != actor.id or not owner_owns(actor, target_workspace):
        raise IngestionPermissionDenied("A rule may only target a workspace you own.")


@transaction.atomic
def create_mapping_rule(source, *, actor, channel, rule_type, pattern, target_workspace):
    _require_owner(actor, source.workspace, channel, "Adding a mapping rule")
    _validate_target(actor, source, target_workspace)
    if rule_type not in WorkspaceMappingRule.RuleType.values:
        raise IngestionInvalid("Unknown rule type.")
    try:
        pattern = normalise_pattern(rule_type, pattern)
    except InvalidMappingKey as exc:
        raise IngestionInvalid(f"Invalid pattern: {exc}.") from None
    try:
        with transaction.atomic():
            rule = WorkspaceMappingRule.objects.create(
                workspace=source.workspace,
                source=source,
                rule_type=rule_type,
                pattern=pattern,
                target_workspace=target_workspace,
                created_by=actor,
            )
    except IntegrityError:
        raise IngestionInvalid("An identical rule already exists for this source.") from None
    _audit_rule(rule, actor, "memory.ingestion.rule_created")
    return rule


@transaction.atomic
def update_mapping_rule(rule, *, actor, channel, enabled=None, target_workspace=None):
    _require_owner(actor, rule.workspace, channel, "Changing a mapping rule")
    rule = WorkspaceMappingRule.objects.select_for_update().get(pk=rule.pk)
    changed = []
    if target_workspace is not None and target_workspace.pk != rule.target_workspace_id:
        _validate_target(actor, rule.source, target_workspace)
        rule.target_workspace = target_workspace
        changed.append("target_workspace")
    if enabled is not None and enabled != rule.enabled:
        rule.enabled = enabled
        changed.append("enabled")
    if changed:
        rule.save(update_fields=[*changed, "updated_at"])
        _audit_rule(rule, actor, "memory.ingestion.rule_updated", fields_changed=sorted(changed))
    return rule


def _audit_rule(rule, actor, action, **extra):
    record_audit(
        action=action,
        actor=actor,
        workspace=rule.workspace,
        resource=rule,
        metadata={
            "rule_type": rule.rule_type,
            "target_workspace": str(rule.target_workspace_id),
            "enabled": rule.enabled,
            **extra,
        },
    )


# --- scrub terms -------------------------------------------------------------


def scrub_terms_for(workspace):
    """`(id, term)` pairs for the scrubber."""
    return [(str(t.id), t.term) for t in ScrubTerm.objects.filter(workspace=workspace)]


@transaction.atomic
def create_scrub_term(*, workspace, actor, channel, term):
    _require_owner(actor, workspace, channel, "Adding a scrub term")
    term = (term or "").strip()
    if len(term) < 2 or len(term) > 200:
        raise IngestionInvalid("A scrub term must be 2-200 characters.")
    try:
        with transaction.atomic():
            row = ScrubTerm.objects.create(workspace=workspace, term=term, created_by=actor)
    except IntegrityError:
        raise IngestionInvalid("This scrub term already exists.") from None
    # The term itself is the sensitive literal: never in audit metadata.
    record_audit(
        action="memory.ingestion.scrub_term_created",
        actor=actor,
        workspace=workspace,
        resource=row,
        metadata={"length": len(term)},
    )
    return row


@transaction.atomic
def delete_scrub_term(term, *, actor, channel):
    """Removing a term only stops future replacement; already-stored
    episodes keep their placeholders. Configuration, not owner data, so a
    plain delete (audited)."""
    _require_owner(actor, term.workspace, channel, "Removing a scrub term")
    record_audit(
        action="memory.ingestion.scrub_term_deleted",
        actor=actor,
        workspace=term.workspace,
        resource=term,
        metadata={},
    )
    term.delete()


# --- audit view --------------------------------------------------------------

AUDIT_VIEW_DAYS = 30
AUDIT_VIEW_EPISODES = 20


def source_audit(source, *, actor, channel):
    """The D11 audit view of one source. Episodes are listed through
    `for_actor()` (they live in their target workspaces). Audited
    `memory.ingestion.viewed` with ids and counts only."""
    require_owner_read(actor, source.workspace, channel)
    since = timezone.localdate() - timedelta(days=AUDIT_VIEW_DAYS - 1)
    counters = list(IngestionCounter.objects.filter(source=source, day__gte=since).order_by("day"))
    episodes = list(
        Episode.objects.for_actor(actor)
        .filter(source=source)
        .select_related("workspace")
        .order_by("-occurred_at", "-id")[:AUDIT_VIEW_EPISODES]
    )
    today = next((c for c in counters if c.day == timezone.localdate()), None)
    created = sum(c.proposals_created for c in counters)
    accepted = sum(c.proposals_accepted for c in counters)
    rejected = sum(c.proposals_rejected for c in counters)
    unmapped = []
    seen = set()
    for counter in reversed(counters):
        for entry in counter.unmapped_keys:
            if entry.get("hash") not in seen:
                seen.add(entry.get("hash"))
                unmapped.append(entry)
    record_audit(
        action="memory.ingestion.viewed",
        actor=actor,
        workspace=source.workspace,
        resource=source,
        metadata={
            "kind": source.kind,
            "episode_ids": [str(e.id) for e in episodes],
            "counter_days": len(counters),
        },
    )
    return {
        "source": source,
        "counters": counters,
        "episodes": episodes,
        "scrubber_hits": merge_reports(*(c.scrubber_hits for c in counters)),
        "unmapped_keys": unmapped[: IngestionCounter.UNMAPPED_KEYS_CAP],
        "tokens_spent_today": today.tokens_spent if today else 0,
        "acceptance": {
            "created": created,
            "accepted": accepted,
            "rejected": rejected,
            "rate": (accepted / (accepted + rejected)) if (accepted + rejected) else None,
        },
        "forget_preview": forget_preview(source),
    }


def owner_workspaces(actor):
    return Workspace.objects.filter(
        members__user=actor, members__role=WorkspaceMember.Role.OWNER
    ).distinct()
