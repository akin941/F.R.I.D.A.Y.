"""Phase 8B branch 1 (brief D2): the MemoryEntity / MemoryFact constraints,
through the ORM and through raw SQL (the DB checks and the same-workspace
trigger are the backstop)."""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from memory.models import (
    GRAPHIFY_PREDICATES,
    LEARNED_PREDICATES,
    MemoryEntity,
    MemoryFact,
)
from memory.tests.graph_helpers import QUOTE, T0, T1, entity, fact, system_fact

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(personal):
    return entity(personal, "api")


@pytest.fixture
def hetzner(personal):
    return entity(personal, "hetzner", kind=MemoryEntity.Kind.TOOL)


def _raises_integrity(callable_):
    with pytest.raises(IntegrityError), transaction.atomic():
        callable_()


def test_predicate_split_is_closed_and_disjoint():
    assert LEARNED_PREDICATES | GRAPHIFY_PREDICATES == set(MemoryFact.Predicate.values)
    assert not LEARNED_PREDICATES & GRAPHIFY_PREDICATES
    assert GRAPHIFY_PREDICATES == {"contains", "imports", "calls", "inherits"}


def test_valid_to_before_valid_from_is_refused(personal, api):
    _raises_integrity(lambda: fact(personal, api, valid_from=T1, valid_to=T0))
    # equal is allowed (a zero-length window)
    fact(personal, api, valid_from=T1, valid_to=T1)


def test_object_entity_of_another_workspace_is_refused_in_save(personal, firm, api):
    foreign = entity(firm, "hetzner", kind=MemoryEntity.Kind.TOOL)
    with pytest.raises(ValueError, match="object_entity"):
        fact(personal, api, obj=foreign)
    foreign_subject = entity(firm, "api")
    with pytest.raises(ValueError, match="subject"):
        fact(personal, foreign_subject)


def _insert_sql(**over):
    now = timezone.now()
    values = {
        "id": uuid.uuid4(),
        "created_at": now,
        "updated_at": now,
        "predicate": "uses",
        "object_text": "",
        "statement": "s",
        "valid_from": now,
        "valid_to": None,
        "confidence": 50,
        "status": "approved",
        "origin": "learned",
        "provenance_quote": QUOTE,
        "sensitivity": "normal",
        "forget_prior_status": "",
        "supersedes_id": None,
        "object_entity_id": None,
    }
    values.update(over)
    cols = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO memory_memoryfact ({cols}) VALUES ({marks})", list(values.values())
        )
    return values["id"]


def test_raw_sql_insert_with_a_foreign_object_entity_is_refused(personal, firm, api):
    foreign = entity(firm, "hetzner", kind=MemoryEntity.Kind.TOOL)
    _raises_integrity(
        lambda: _insert_sql(
            workspace_id=personal.id, subject_id=api.id, object_entity_id=foreign.id
        )
    )
    foreign_subject = entity(firm, "api")
    _raises_integrity(
        lambda: _insert_sql(
            workspace_id=personal.id, subject_id=foreign_subject.id, object_text="x"
        )
    )
    # the same row in its own workspace is accepted
    _insert_sql(workspace_id=personal.id, subject_id=api.id, object_text="x")


def test_exactly_one_object_kind(personal, api, hetzner):
    _raises_integrity(
        lambda: MemoryFact.all_objects.create(
            workspace=personal,
            subject=api,
            predicate="uses",
            object_entity=hetzner,
            object_text="also text",
            statement="s",
            valid_from=T0,
            status="approved",
            provenance_quote=QUOTE,
        )
    )
    _raises_integrity(
        lambda: MemoryFact.all_objects.create(
            workspace=personal,
            subject=api,
            predicate="uses",
            statement="s",
            valid_from=T0,
            status="approved",
            provenance_quote=QUOTE,
        )
    )


def test_learned_fact_without_quote_is_refused(personal, api):
    _raises_integrity(lambda: fact(personal, api, quote=""))


def test_system_iff_graphify(personal, api, hetzner):
    _raises_integrity(lambda: fact(personal, api, obj=hetzner, status=MemoryFact.Status.SYSTEM))
    _raises_integrity(
        lambda: fact(
            personal,
            api,
            MemoryFact.Predicate.IMPORTS,
            hetzner,
            origin=MemoryFact.Origin.GRAPHIFY,
            status=MemoryFact.Status.APPROVED,
        )
    )
    system_fact(personal, api, hetzner)


def test_graphify_rows_use_only_graphify_predicates(personal, api, hetzner):
    _raises_integrity(
        lambda: system_fact(personal, api, hetzner, predicate=MemoryFact.Predicate.USES)
    )


def test_code_kinds_are_graphify_only(personal):
    _raises_integrity(lambda: entity(personal, "memory.graph", kind=MemoryEntity.Kind.CODE_MODULE))
    entity(
        personal,
        "memory.graph",
        kind=MemoryEntity.Kind.CODE_MODULE,
        origin=MemoryEntity.Origin.GRAPHIFY,
    )


def test_entity_name_unique_per_workspace_kind_origin(personal, firm):
    entity(personal, "api")
    _raises_integrity(lambda: entity(personal, "api"))
    entity(firm, "api")  # another workspace may reuse it
    entity(personal, "api", kind=MemoryEntity.Kind.REPO)


@pytest.mark.parametrize("status", ["approved", "rejected", "archived"])
def test_supersedes_is_refused_outside_pending_orm(personal, api, status):
    old = fact(personal, api)
    _raises_integrity(lambda: fact(personal, api, valid_from=T1, status=status, supersedes=old))


def test_supersedes_on_a_system_row_is_refused(personal, api, hetzner):
    old = system_fact(personal, api, hetzner)
    _raises_integrity(lambda: system_fact(personal, api, hetzner, valid_from=T1, supersedes=old))


def test_supersedes_is_refused_outside_pending_raw_update(personal, api):
    old = fact(personal, api)
    proposal = fact(personal, api, valid_from=T1, status="pending_approval", supersedes=old)
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memoryfact SET status = 'approved' WHERE id = %s", [proposal.id]
        )
    plain = fact(personal, api, valid_from=T1)
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memoryfact SET supersedes_id = %s WHERE id = %s", [old.id, plain.id]
        )


def test_supersedes_target_of_another_subject_predicate_or_self_is_refused(
    personal, firm, api, hetzner
):
    other_subject = entity(personal, "web")
    old = fact(personal, api)
    with pytest.raises(ValueError, match="supersedes"):
        fact(personal, other_subject, valid_from=T1, status="pending_approval", supersedes=old)
    with pytest.raises(ValueError, match="supersedes"):
        fact(
            personal,
            api,
            MemoryFact.Predicate.USES,
            valid_from=T1,
            status="pending_approval",
            supersedes=old,
        )
    proposal = fact(personal, api, valid_from=T1, status="pending_approval")
    proposal.supersedes = proposal
    with pytest.raises(ValueError, match="itself"):
        proposal.save()


def test_trigger_refuses_bad_supersedes_targets_by_raw_sql(personal, firm, api):
    old = fact(personal, api)
    other_subject = entity(personal, "web")
    other_predicate = fact(personal, api, MemoryFact.Predicate.USES)
    firm_api = entity(firm, "api")
    firm_old = fact(firm, firm_api)
    proposal = fact(personal, api, valid_from=T1, status="pending_approval")
    for target in (other_predicate, firm_old):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE memory_memoryfact SET supersedes_id = %s WHERE id = %s",
                [target.id, proposal.id],
            )
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memoryfact SET supersedes_id = id WHERE id = %s", [proposal.id]
        )
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memoryfact SET subject_id = %s, supersedes_id = %s WHERE id = %s",
            [other_subject.id, old.id, proposal.id],
        )
    # the valid link is accepted
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE memory_memoryfact SET supersedes_id = %s WHERE id = %s", [old.id, proposal.id]
        )


def test_managers_hide_trashed_rows(personal, api):
    row = fact(personal, api)
    MemoryFact.all_objects.filter(pk=row.pk).update(deleted_at=timezone.now())
    MemoryEntity.all_objects.filter(pk=api.pk).update(deleted_at=timezone.now())
    assert not MemoryFact.objects.filter(pk=row.pk).exists()
    assert MemoryFact.all_objects.filter(pk=row.pk).exists()
    assert not MemoryEntity.objects.filter(pk=api.pk).exists()


def test_model_eligible_is_approved_learned_normal_current_only(personal, api, hetzner):
    good = fact(personal, api)
    fact(personal, api, sensitivity="private")
    fact(personal, api, status="pending_approval")
    fact(personal, api, valid_to=T1)
    system_fact(personal, api, hetzner)
    trashed = fact(personal, api)
    MemoryFact.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    assert list(MemoryFact.objects.model_eligible()) == [good]
