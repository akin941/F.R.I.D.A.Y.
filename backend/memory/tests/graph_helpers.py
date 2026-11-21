"""Helpers for the Phase 8B temporal-graph tests (fixtures live in conftest)."""

from datetime import UTC, datetime

from memory.models import MemoryEntity, MemoryFact

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 3, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)
QUOTE = "we decided to deploy the api on hetzner"


def entity(workspace, name, kind=MemoryEntity.Kind.SERVICE, origin=MemoryEntity.Origin.LEARNED):
    return MemoryEntity.all_objects.create(
        workspace=workspace,
        kind=kind,
        name=name,
        normalised_name=name.lower(),
        origin=origin,
    )


def fact(
    workspace,
    subject,
    predicate=MemoryFact.Predicate.DEPLOYED_TO,
    obj=None,
    *,
    text="",
    valid_from=T0,
    valid_to=None,
    status=MemoryFact.Status.APPROVED,
    origin=MemoryFact.Origin.LEARNED,
    sensitivity=MemoryFact.Sensitivity.NORMAL,
    source=None,
    supersedes=None,
    quote=QUOTE,
):
    if obj is None and not text:
        text = "somewhere"
    return MemoryFact.all_objects.create(
        workspace=workspace,
        subject=subject,
        predicate=predicate,
        object_entity=obj,
        object_text="" if obj is not None else text,
        statement=f"{subject.name} {predicate} {obj.name if obj else text}",
        valid_from=valid_from,
        valid_to=valid_to,
        status=status,
        origin=origin,
        sensitivity=sensitivity,
        ingestion_source=source,
        supersedes=supersedes,
        provenance_quote=quote if origin == MemoryFact.Origin.LEARNED else "",
        confidence=100 if origin == MemoryFact.Origin.GRAPHIFY else 80,
    )


def system_fact(workspace, subject, obj, predicate=MemoryFact.Predicate.IMPORTS, **kwargs):
    return fact(
        workspace,
        subject,
        predicate,
        obj,
        status=MemoryFact.Status.SYSTEM,
        origin=MemoryFact.Origin.GRAPHIFY,
        **kwargs,
    )


def refreshed(*rows):
    for row in rows:
        row.refresh_from_db()
    return rows
