"""The Graphify code graph in memory (Phase 8B branch 5, brief D3, D7, D10).

Graphify facts are not learned about the owner: they are a deterministic,
model-free index of a repository's own code. They are written only here,
through `memory.graph.ports.import_code_graph`, as `origin=graphify` rows
with the status `system` (D3). They are never approvable, editable or
trashable by the owner, get no embedding and take no part in retrieval.

- `import_code_graph` -- the **temporal replace** (D10 step 7). In one
  transaction, serialised per repo on its `CodeGraphImport` row: facts of
  the new snapshot that match a current `system` fact (subject, predicate,
  object) keep it, so their `valid_from` survives; new ones start at the
  snapshot's `committed_at`; current facts absent from the snapshot close
  at `committed_at`. A refused or failed import changes nothing.
- `retire_code_graph` -- the repo's `graphify_enabled` switch turned off
  (D7): every current `system` fact of the repo is closed at `now` and its
  entities drop out of every read. Nothing is deleted.
- `purge_code_graph` -- the gate-class `purge_trash` key
  `graphify_repo_ids`: permanently deletes a **retired** repo's rows.

For a Graphify entity `deleted_at` means "not in the repo's current
snapshot" (or retired), never the owner trash: the trash and restore APIs
refuse Graphify rows, and `purgeable_entities` covers learned rows only.

The snapshot handed in is already filtered and mapped by the producer
(`harness.graphify`), but memory owns the rows, so it is validated again
here (closed key sets, the Graphify kinds and predicates, bounded strings,
the caps) and every name is scrubbed and fence-neutralised before it is
stored. Audit rows carry counts and the sha only, never a label.
"""

import hashlib
import re
from datetime import datetime

from django.db import connection, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from memory.context import strip_invisible_formatting
from memory.graph.services import render_statement
from memory.ingestion.scrubber import scrub_text
from memory.models import (
    GRAPHIFY_ENTITY_KINDS,
    GRAPHIFY_PREDICATES,
    CodeGraphImport,
    MemoryEntity,
    MemoryFact,
    ScrubTerm,
)

MAX_ENTITIES = 20_000
MAX_FACTS = 60_000
MAX_KEY_CHARS = 512
MAX_LABEL_CHARS = 2048
# `normalised_name` and `external_ref` both hold the entity's ref.
MAX_REF_CHARS = MemoryEntity.MAX_NAME_CHARS
_REPO_REF = re.compile(r"\A[A-Za-z0-9_.\-]{1,64}\Z")
_SHA = re.compile(r"\A[0-9a-f]{7,64}\Z")
_WHITESPACE = re.compile(r"\s+")
_BATCH = 1000

GRAPH_KEYS = frozenset({"entities", "facts"})
ENTITY_KEYS = frozenset({"key", "kind", "name"})
FACT_KEYS = frozenset({"subject", "predicate", "object"})

GRAPHIFY = MemoryFact.Origin.GRAPHIFY
SYSTEM = MemoryFact.Status.SYSTEM


class CodeGraphInvalid(ValueError):
    """The snapshot does not match the import contract, or breaks a cap.
    `reason` is a stable key (`schema`, `entity_cap`, `fact_cap`,
    `unknown_entity`, `stale_snapshot`, ...); the message never quotes a
    label."""

    def __init__(self, reason, message=""):
        super().__init__(message or reason)
        self.reason = reason


# --- refs and names -----------------------------------------------------------


def repo_prefix(repo_ref):
    return f"{repo_ref}:"


def entity_ref(repo_ref, key):
    """The `external_ref` (and `normalised_name`) of a Graphify entity: the
    repo id plus the Graphify node key, hashed when it would not fit."""
    ref = f"{repo_ref}:{key}"
    if len(ref) <= MAX_REF_CHARS:
        return ref
    return f"{repo_ref}:#{hashlib.sha256(key.encode('utf-8')).hexdigest()[:40]}"


def clean_name(text, terms):
    """A label made safe to store and render: scrubbed (identifiers can
    embed tokens), invisible format characters dropped, tag brackets
    neutralised (a label shaped like a fence tag can never open or close a
    block wherever it is rendered), whitespace collapsed, bounded."""
    scrubbed = scrub_text(str(text or ""), terms=terms).text
    visible = strip_invisible_formatting(scrubbed)
    neutral = visible.replace("<", "‹").replace(">", "›")
    name = _WHITESPACE.sub(" ", neutral).strip()[: MemoryEntity.MAX_NAME_CHARS]
    return name or "?"


# --- validation ---------------------------------------------------------------


def _is_text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


def validate_snapshot(graph):
    """Strict shape check; returns `(entities, facts)` with facts deduped.
    Raises `CodeGraphInvalid` before anything is written."""
    if not isinstance(graph, dict) or set(graph) != GRAPH_KEYS:
        raise CodeGraphInvalid("schema", "The snapshot must have exactly entities and facts.")
    entities, facts = graph["entities"], graph["facts"]
    if not isinstance(entities, list) or not isinstance(facts, list):
        raise CodeGraphInvalid("schema", "entities and facts must be lists.")
    if len(entities) > MAX_ENTITIES:
        raise CodeGraphInvalid("entity_cap", f"More than {MAX_ENTITIES} entities.")
    if len(facts) > MAX_FACTS:
        raise CodeGraphInvalid("fact_cap", f"More than {MAX_FACTS} facts.")
    keys = set()
    for item in entities:
        if not isinstance(item, dict) or set(item) != ENTITY_KEYS:
            raise CodeGraphInvalid("schema", "An entity has the wrong keys.")
        if not _is_text(item["key"], MAX_KEY_CHARS) or not _is_text(item["name"], MAX_LABEL_CHARS):
            raise CodeGraphInvalid("schema", "An entity key or name is empty or too long.")
        if item["kind"] not in GRAPHIFY_ENTITY_KINDS:
            raise CodeGraphInvalid("kind", "An entity kind is not a Graphify kind.")
        if item["key"] in keys:
            raise CodeGraphInvalid("duplicate_entity", "Two entities share a key.")
        keys.add(item["key"])
    triples = []
    seen = set()
    for item in facts:
        if not isinstance(item, dict) or set(item) != FACT_KEYS:
            raise CodeGraphInvalid("schema", "A fact has the wrong keys.")
        if item["predicate"] not in GRAPHIFY_PREDICATES:
            raise CodeGraphInvalid("predicate", "A fact predicate is not a Graphify predicate.")
        subject, obj = item["subject"], item["object"]
        if subject not in keys or obj not in keys:
            raise CodeGraphInvalid("unknown_entity", "A fact names an entity not in the snapshot.")
        if subject == obj:
            raise CodeGraphInvalid("self_loop", "A fact links an entity to itself.")
        triple = (subject, item["predicate"], obj)
        if triple not in seen:
            seen.add(triple)
            triples.append(triple)
    return entities, triples


def _check_repo_ref(repo_ref):
    repo_ref = str(repo_ref)
    if not _REPO_REF.match(repo_ref):
        raise CodeGraphInvalid("repo_ref", "repo_ref must be a short opaque id.")
    return repo_ref


def _check_commit(commit_sha, committed_at):
    if not isinstance(commit_sha, str) or not _SHA.match(commit_sha):
        raise CodeGraphInvalid("commit_sha", "commit_sha must be a hex commit id.")
    if not isinstance(committed_at, datetime) or timezone.is_naive(committed_at):
        raise CodeGraphInvalid("committed_at", "committed_at must be an aware datetime.")


# --- locking and audit ----------------------------------------------------------


def _lock_repo(workspace, repo_ref):
    """Serialise every import, retire and purge of one repo's graph (a
    transaction-scoped advisory lock: the `CodeGraphImport` row may not
    exist yet)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"memory.code_graph:{workspace.pk}:{repo_ref}"],
        )


def _audit(action, *, workspace, actor=None, resource=None, **metadata):
    record_audit(
        action=action,
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor else AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        resource=resource,
        metadata=metadata,
    )


def _terms(workspace):
    return [(str(t.id), t.term) for t in ScrubTerm.objects.filter(workspace=workspace)]


def _repo_entities(workspace, repo_ref):
    return MemoryEntity.all_objects.filter(
        workspace=workspace,
        origin=MemoryEntity.Origin.GRAPHIFY,
        external_ref__startswith=repo_prefix(repo_ref),
    )


def _repo_facts(workspace, repo_ref):
    return MemoryFact.all_objects.filter(
        workspace=workspace,
        origin=GRAPHIFY,
        subject__external_ref__startswith=repo_prefix(repo_ref),
    )


def _close(fact_ids, when, now):
    """Close windows at `when`, never before a row's own `valid_from` (the
    window check)."""
    closed = 0
    for start in range(0, len(fact_ids), 5 * _BATCH):
        closed += MemoryFact.all_objects.filter(pk__in=fact_ids[start : start + 5 * _BATCH]).update(
            valid_to=Greatest(Value(when), F("valid_from")), updated_at=now
        )
    return closed


# --- import -----------------------------------------------------------------------


def import_code_graph(*, workspace, repo_ref, commit_sha, committed_at, graph):
    """Replace one repo's code graph temporally (D10). Returns the counts.
    Raises `CodeGraphInvalid` with nothing changed."""
    repo_ref = _check_repo_ref(repo_ref)
    _check_commit(commit_sha, committed_at)
    entities, triples = validate_snapshot(graph)
    with transaction.atomic():
        return _import(workspace, repo_ref, commit_sha, committed_at, entities, triples)


def _import(workspace, repo_ref, commit_sha, committed_at, entities, triples):
    _lock_repo(workspace, repo_ref)
    record = CodeGraphImport.objects.filter(workspace=workspace, repo_ref=repo_ref).first()
    if record is not None and record.retired_at is None and committed_at < record.committed_at:
        raise CodeGraphInvalid("stale_snapshot", "The snapshot is older than the current one.")
    now = timezone.now()
    terms = _terms(workspace)

    existing = {row.external_ref: row for row in _repo_entities(workspace, repo_ref)}
    by_key, to_create, to_update = {}, [], []
    for item in entities:
        ref = entity_ref(repo_ref, item["key"])
        name = clean_name(item["name"], terms)
        row = existing.get(ref)
        if row is None:
            row = MemoryEntity(
                workspace=workspace,
                kind=item["kind"],
                name=name,
                normalised_name=ref,
                origin=MemoryEntity.Origin.GRAPHIFY,
                external_ref=ref,
            )
            to_create.append(row)
        elif (row.kind, row.name, row.deleted_at) != (item["kind"], name, None):
            row.kind, row.name, row.deleted_at, row.updated_at = item["kind"], name, None, now
            to_update.append(row)
        by_key[item["key"]] = row
    MemoryEntity.all_objects.bulk_create(to_create, batch_size=_BATCH)
    if to_update:
        MemoryEntity.all_objects.bulk_update(
            to_update, ["kind", "name", "deleted_at", "updated_at"], batch_size=_BATCH
        )
    snapshot_refs = {row.external_ref for row in by_key.values()}
    hidden_ids = [
        pk
        for ref, pk in ((r.external_ref, r.pk) for r in existing.values() if r.deleted_at is None)
        if ref not in snapshot_refs
    ]
    entities_hidden = MemoryEntity.all_objects.filter(pk__in=hidden_ids).update(
        deleted_at=now, updated_at=now
    )

    current = {
        (subject_id, predicate, object_id): pk
        for pk, subject_id, predicate, object_id in _repo_facts(workspace, repo_ref)
        .filter(status=SYSTEM, valid_to__isnull=True, deleted_at__isnull=True)
        .values_list("pk", "subject_id", "predicate", "object_entity_id")
    }
    wanted = {}
    for subject, predicate, obj in triples:
        subject_row, object_row = by_key[subject], by_key[obj]
        wanted[(subject_row.pk, predicate, object_row.pk)] = (subject_row, object_row)
    kept = current.keys() & wanted.keys()
    closed = _close([pk for triple, pk in current.items() if triple not in kept], committed_at, now)
    new_facts = [
        MemoryFact(
            workspace=workspace,
            subject=subject_row,
            predicate=predicate,
            object_entity=object_row,
            statement=render_statement(subject_row.name, predicate, object_row.name),
            valid_from=committed_at,
            confidence=100,
            status=SYSTEM,
            origin=GRAPHIFY,
            sensitivity=MemoryFact.Sensitivity.NORMAL,
        )
        for (_, predicate, _), (subject_row, object_row) in wanted.items()
        if (subject_row.pk, predicate, object_row.pk) not in kept
    ]
    MemoryFact.all_objects.bulk_create(new_facts, batch_size=_BATCH)

    if record is None:
        record = CodeGraphImport(workspace=workspace, repo_ref=repo_ref)
    record.commit_sha = commit_sha
    record.committed_at = committed_at
    record.imported_at = now
    record.entity_count = len(by_key)
    record.fact_count = len(wanted)
    record.retired_at = None
    record.save()
    counts = {
        "entities": len(by_key),
        "entities_added": len(to_create),
        "entities_hidden": entities_hidden,
        "facts": len(wanted),
        "facts_kept": len(kept),
        "facts_added": len(new_facts),
        "facts_closed": closed,
    }
    _audit(
        "memory.graph.imported",
        workspace=workspace,
        resource=record,
        repo_ref=repo_ref,
        commit_sha=commit_sha,
        **counts,
    )
    return counts


# --- retire and purge (D7) ----------------------------------------------------------


def retire_code_graph(*, workspace, repo_ref, actor=None):
    """Close every current `system` fact of the repo at `now` and hide its
    entities; nothing is deleted. A repo with no import (or one already
    retired) is a no-op. Returns the counts."""
    repo_ref = _check_repo_ref(repo_ref)
    with transaction.atomic():
        _lock_repo(workspace, repo_ref)
        record = (
            CodeGraphImport.objects.select_for_update()
            .filter(workspace=workspace, repo_ref=repo_ref)
            .first()
        )
        if record is None or record.retired_at is not None:
            return {"retired": False, "facts_closed": 0, "entities_hidden": 0}
        now = timezone.now()
        current = list(
            _repo_facts(workspace, repo_ref)
            .filter(status=SYSTEM, valid_to__isnull=True, deleted_at__isnull=True)
            .values_list("pk", flat=True)
        )
        closed = _close(current, now, now)
        hidden = (
            _repo_entities(workspace, repo_ref)
            .filter(deleted_at__isnull=True)
            .update(deleted_at=now, updated_at=now)
        )
        record.retired_at = now
        record.save(update_fields=["retired_at", "updated_at"])
        counts = {"facts_closed": closed, "entities_hidden": hidden}
        _audit(
            "memory.graph.code_graph_retired",
            workspace=workspace,
            actor=actor,
            resource=record,
            repo_ref=repo_ref,
            **counts,
        )
        return {"retired": True, **counts}


def purgeable_code_graphs(workspace):
    """`purge_trash`'s `graphify_repo_ids` scope: retired code graphs."""
    return CodeGraphImport.objects.filter(workspace=workspace, retired_at__isnull=False)


def purge_code_graph(record, *, actor=None, approval_request_id=None):
    """Permanently delete a retired repo's Graphify facts and entities and
    its import row -- only from an approved `purge_trash` request (ADR-009).
    A graph re-enabled (re-imported) meanwhile is left alone. Caller holds
    the transaction."""
    _lock_repo(record.workspace, record.repo_ref)
    record = CodeGraphImport.objects.select_for_update().filter(pk=record.pk).first()
    if record is None or record.retired_at is None:
        return False
    workspace, repo_ref = record.workspace, record.repo_ref
    prefix = repo_prefix(repo_ref)
    facts = MemoryFact.all_objects.filter(workspace=workspace, origin=GRAPHIFY).filter(
        Q(subject__external_ref__startswith=prefix)
        | Q(object_entity__external_ref__startswith=prefix)
    )
    fact_ids = list(facts.values_list("pk", flat=True))
    for start in range(0, len(fact_ids), 5 * _BATCH):
        MemoryFact.all_objects.filter(pk__in=fact_ids[start : start + 5 * _BATCH]).delete()
    entities = _repo_entities(workspace, repo_ref)
    entity_count = entities.count()
    entities.delete()
    _audit(
        "memory.graph.code_graph_purged",
        workspace=workspace,
        actor=actor,
        resource=record,
        repo_ref=repo_ref,
        facts=len(fact_ids),
        entities=entity_count,
        reason="approval",
        approval_request_id=approval_request_id or "",
    )
    record.delete()
    return True
