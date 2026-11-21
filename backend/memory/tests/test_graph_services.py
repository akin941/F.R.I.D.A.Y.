"""Phase 8B branch 1 (brief D2, D3): the temporal write rules -- supersede,
the supersede order, the pending `supersedes` link, approve/reject, owner
edits as versions, trash/restore, the port."""

import threading
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from memory.graph import ports, services
from memory.graph.candidates import EntityRef
from memory.models import MemoryEntity, MemoryFact
from memory.tests.graph_helpers import QUOTE, T0, T1, T2, entity, fact, refreshed, system_fact

pytestmark = pytest.mark.django_db

Status = MemoryFact.Status
WEB = CHANNEL_WEB


@pytest.fixture
def api(personal):
    return entity(personal, "api")


@pytest.fixture
def hetzner(personal):
    return entity(personal, "hetzner", kind=MemoryEntity.Kind.TOOL)


@pytest.fixture
def aws(personal):
    return entity(personal, "aws", kind=MemoryEntity.Kind.TOOL)


# --- supersede ---------------------------------------------------------------


def test_supersede_sets_window_and_link(personal, api, hetzner, aws):
    old = fact(personal, api, obj=hetzner, valid_from=T0)
    new = fact(personal, api, obj=aws, valid_from=T1)
    services.supersede(old, new)
    old.refresh_from_db()
    assert old.valid_to == T1 and old.superseded_by_id == new.pk
    assert list(MemoryFact.objects.current().filter(subject=api)) == [new]


def test_supersede_order_is_409_and_writes_nothing(personal, api, hetzner, aws):
    old = fact(personal, api, obj=hetzner, valid_from=T1)
    new = fact(personal, api, obj=aws, valid_from=T0)
    with pytest.raises(services.SupersedeOrder):
        services.supersede(old, new)
    old.refresh_from_db()
    assert old.valid_to is None and old.superseded_by_id is None


def test_supersede_with_equal_valid_from_is_allowed(personal, api, hetzner, aws):
    old = fact(personal, api, obj=hetzner, valid_from=T1)
    new = fact(personal, api, obj=aws, valid_from=T1)
    services.supersede(old, new)
    old.refresh_from_db()
    assert old.valid_to == old.valid_from == T1


def test_supersede_needs_same_subject_and_predicate(personal, api, hetzner, aws):
    old = fact(personal, api, obj=hetzner)
    other = fact(personal, api, MemoryFact.Predicate.USES, obj=aws, valid_from=T1)
    with pytest.raises(services.GraphInvalid):
        services.supersede(old, other)


def test_supersede_of_a_non_current_fact_is_refused(personal, api, hetzner, aws):
    old = fact(personal, api, obj=hetzner, valid_to=T1)
    new = fact(personal, api, obj=aws, valid_from=T1)
    with pytest.raises(services.SupersedeTargetChanged):
        services.supersede(old, new)


@pytest.mark.django_db(transaction=True)
def test_concurrent_supersede_of_one_fact_leaves_one_winner(owner, personal):
    api = entity(personal, "api")
    old = fact(personal, api, valid_from=T0)
    a = fact(personal, api, text="aws", valid_from=T1)
    b = fact(personal, api, text="gcp", valid_from=T2)
    barrier = threading.Barrier(2)
    results, errors = {}, []

    def worker(key, new):
        try:
            barrier.wait(timeout=5)
            try:
                services.supersede(MemoryFact.objects.get(pk=old.pk), new)
                results[key] = "won"
            except services.SupersedeTargetChanged:
                results[key] = "lost"
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(k, n)) for k, n in (("a", a), ("b", b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
    assert sorted(results.values()) == ["lost", "won"]
    old.refresh_from_db()
    winner = a if results["a"] == "won" else b
    assert old.superseded_by_id == winner.pk and old.valid_to == winner.valid_from


# --- the port (learned proposals) ---------------------------------------------


def _propose(personal, **over):
    kwargs = {
        "workspace": personal,
        "subject": EntityRef("service", "API"),
        "predicate": "deployed_to",
        "object_entity": EntityRef("tool", "Hetzner"),
        "valid_from": T1.date(),
        "provenance_quote": QUOTE,
        "confidence": 99,
    }
    kwargs.update(over)
    return ports.propose_fact(**kwargs)


def test_propose_fact_is_pending_capped_and_resolves_entities(personal, api, hetzner):
    proposal = _propose(personal)
    assert proposal.status == Status.PENDING_APPROVAL
    assert proposal.confidence == MemoryFact.MAX_PROPOSED_CONFIDENCE
    assert proposal.subject_id == api.pk and proposal.object_entity_id == hetzner.pk
    assert proposal.statement
    assert AuditLog.objects.filter(action="memory.graph.fact_proposed").count() == 1


def test_propose_fact_resolves_by_alias_and_creates_new_entities(personal, api):
    MemoryEntity.all_objects.filter(pk=api.pk).update(aliases=["backend api"])
    proposal = _propose(personal, subject=EntityRef("service", "Backend  API"))
    assert proposal.subject_id == api.pk
    assert MemoryEntity.objects.filter(normalised_name="hetzner", kind="tool").exists()


def test_propose_fact_refuses_graphify_predicates_and_missing_quote(personal):
    with pytest.raises(services.GraphInvalid):
        _propose(personal, predicate="imports")
    with pytest.raises(services.GraphInvalid):
        _propose(personal, provenance_quote="short")
    assert not MemoryFact.all_objects.exists()


def test_propose_fact_scrubs_names(personal):
    proposal = _propose(
        personal,
        object_entity=None,
        object_text="token ghp_" + "a" * 36,
    )
    assert "ghp_" not in proposal.object_text


def test_propose_with_supersedes_checks_target(personal, api, hetzner):
    current = fact(personal, api, obj=hetzner, valid_from=T0 + timedelta(days=2))
    proposal = _propose(personal, object_entity=EntityRef("tool", "aws"), supersedes=current)
    assert proposal.supersedes_id == current.pk
    with pytest.raises(services.SupersedeOrder):
        _propose(
            personal,
            object_entity=EntityRef("tool", "aws"),
            valid_from=T0.date(),
            supersedes=current,
        )


def test_import_code_graph_port_refuses_a_malformed_snapshot(personal):
    # Implemented in branch 5 (memory/tests/test_code_graph.py covers it).
    with pytest.raises(ports.CodeGraphInvalid):
        ports.import_code_graph(
            workspace=personal,
            repo_ref="r",
            commit_sha="a" * 40,
            committed_at=T0,
            graph={},
        )


# --- approve / reject -------------------------------------------------------------


def test_approve_plain_proposal(owner, personal, api):
    proposal = fact(personal, api, status=Status.PENDING_APPROVAL)
    services.approve_fact(fact=proposal, actor=owner, channel=WEB)
    proposal.refresh_from_db()
    assert proposal.status == Status.APPROVED and proposal.decided_by == owner


def test_approve_with_supersedes_closes_target_and_clears_link(owner, personal, api, hetzner):
    target = fact(personal, api, obj=hetzner, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    services.approve_fact(fact=proposal, actor=owner, channel=WEB)
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.APPROVED and proposal.supersedes_id is None
    assert target.valid_to == T1 and target.superseded_by_id == proposal.pk


def test_approve_failure_after_status_write_leaves_both_rows_unchanged(
    owner, personal, api, hetzner, monkeypatch
):
    target = fact(personal, api, obj=hetzner, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )

    def boom(old, new):
        raise RuntimeError("injected")

    monkeypatch.setattr(services, "supersede", boom)
    with pytest.raises(RuntimeError):
        services.approve_fact(fact=proposal, actor=owner, channel=WEB)
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.PENDING_APPROVAL and proposal.supersedes_id == target.pk
    assert target.valid_to is None and target.superseded_by_id is None


def test_approve_supersede_order_conflict_keeps_everything(owner, personal, api, hetzner):
    target = fact(personal, api, obj=hetzner, valid_from=T1)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    # the target's start moved later (an owner fix) after reconciliation
    MemoryFact.all_objects.filter(pk=target.pk).update(valid_from=T2)
    with pytest.raises(services.SupersedeOrder):
        services.approve_fact(fact=proposal, actor=owner, channel=WEB)
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.PENDING_APPROVAL and proposal.supersedes_id == target.pk
    assert target.valid_to is None


@pytest.mark.parametrize("change", ["superseded", "trashed", "archived"])
def test_approve_when_target_changed_is_409(owner, personal, api, hetzner, change):
    target = fact(personal, api, obj=hetzner, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T2, status="pending_approval", supersedes=target
    )
    if change == "superseded":
        services.supersede(target, fact(personal, api, text="gcp", valid_from=T1))
    elif change == "trashed":
        services.trash_fact(fact=target, actor=owner, channel=WEB)
    else:
        MemoryFact.all_objects.filter(pk=target.pk).update(status=Status.ARCHIVED)
    before = MemoryFact.all_objects.get(pk=target.pk)
    with pytest.raises(services.SupersedeTargetChanged):
        services.approve_fact(fact=proposal, actor=owner, channel=WEB)
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.PENDING_APPROVAL and proposal.supersedes_id == target.pk
    assert (target.valid_to, target.superseded_by_id, target.status) == (
        before.valid_to,
        before.superseded_by_id,
        before.status,
    )


def test_reject_clears_link_and_leaves_target_current(owner, personal, api, hetzner):
    target = fact(personal, api, obj=hetzner)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    services.reject_fact(fact=proposal, actor=owner, channel=WEB)
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.REJECTED and proposal.supersedes_id is None
    assert target.valid_to is None and target.status == Status.APPROVED


def test_decisions_need_owner_over_web(owner, personal, api):
    proposal = fact(personal, api, status=Status.PENDING_APPROVAL)
    with pytest.raises(services.GraphPermissionDenied):
        services.approve_fact(fact=proposal, actor=owner, channel=CHANNEL_CLI)
    owner.is_agent = True
    with pytest.raises(services.GraphPermissionDenied):
        services.approve_fact(fact=proposal, actor=owner, channel=WEB)


def test_approve_of_a_non_pending_fact_is_invalid(owner, personal, api):
    row = fact(personal, api)
    with pytest.raises(services.GraphInvalidTransition):
        services.approve_fact(fact=row, actor=owner, channel=WEB)


def test_system_rows_are_immutable(owner, personal, api, hetzner):
    row = system_fact(personal, api, hetzner)
    for call in (
        services.approve_fact,
        services.reject_fact,
        services.edit_fact,
        services.trash_fact,
    ):
        with pytest.raises(services.SystemRowImmutable):
            call(fact=row, actor=owner, channel=WEB)
    graphify_entity = entity(personal, "x", origin=MemoryEntity.Origin.GRAPHIFY)
    with pytest.raises(services.SystemRowImmutable):
        services.trash_entity(entity=graphify_entity, actor=owner, channel=WEB)


@pytest.mark.django_db(transaction=True)
def test_race_approve_vs_trash_target_has_exactly_one_outcome(owner, personal):
    api = entity(personal, "api")
    target = fact(personal, api, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    barrier = threading.Barrier(2)
    results, errors = {}, []

    def approve():
        try:
            barrier.wait(timeout=5)
            try:
                services.approve_fact(
                    fact=MemoryFact.objects.get(pk=proposal.pk), actor=owner, channel=WEB
                )
                results["approve"] = "ok"
            except services.SupersedeTargetChanged:
                results["approve"] = "conflict"
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    def trash():
        try:
            barrier.wait(timeout=5)
            try:
                services.trash_fact(
                    fact=MemoryFact.objects.get(pk=target.pk), actor=owner, channel=WEB
                )
                results["trash"] = "ok"
            except services.GraphInvalidTransition:
                results["trash"] = "refused"
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=approve), threading.Thread(target=trash)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
    assert results in (
        {"approve": "ok", "trash": "refused"},
        {"approve": "conflict", "trash": "ok"},
    )
    target, proposal = refreshed(target, proposal)
    # never a closed window on a trashed target
    assert not (target.deleted_at is not None and target.valid_to is not None)
    if results["approve"] == "ok":
        assert target.superseded_by_id == proposal.pk and target.deleted_at is None
    else:
        assert proposal.status == Status.PENDING_APPROVAL and target.valid_to is None


# --- owner-authored facts and edits --------------------------------------------------


def test_create_fact_is_approved_in_owner_words(owner, personal):
    row = services.create_fact(
        workspace=personal,
        actor=owner,
        channel=WEB,
        subject_kind="service",
        subject_name="API",
        predicate="deployed_to",
        object_kind="tool",
        object_name="Hetzner",
        statement="API Hetzner'de çalışıyor",
        valid_from=T0,
    )
    assert row.status == Status.APPROVED and row.provenance_quote == "API Hetzner'de çalışıyor"
    assert row.created_by == owner and row.confidence == 100
    audit = AuditLog.objects.get(action="memory.graph.fact_created")
    assert "API" not in str(audit.metadata)


def test_edit_of_an_approved_fact_creates_a_version(owner, personal, api, hetzner):
    old = fact(personal, api, obj=hetzner, valid_from=T0)
    version = services.edit_fact(
        fact=old, actor=owner, channel=WEB, object_kind="tool", object_name="aws", valid_from=T1
    )
    old.refresh_from_db()
    assert version.pk != old.pk and version.status == Status.APPROVED
    assert old.status == Status.APPROVED and old.valid_to == T1
    assert old.superseded_by_id == version.pk
    assert old.object_entity_id == hetzner.pk  # never edited in place


def test_edit_with_an_earlier_valid_from_is_409_and_writes_nothing(owner, personal, api):
    old = fact(personal, api, valid_from=T1)
    count = MemoryFact.all_objects.count()
    with pytest.raises(services.SupersedeOrder):
        services.edit_fact(fact=old, actor=owner, channel=WEB, statement="x", valid_from=T0)
    assert MemoryFact.all_objects.count() == count
    old.refresh_from_db()
    assert old.valid_to is None


def test_edit_of_a_pending_proposal_applies_at_once(owner, personal, api, hetzner):
    target = fact(personal, api, obj=hetzner, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    version = services.edit_fact(fact=proposal, actor=owner, channel=WEB, object_text="gcp")
    target, proposal = refreshed(target, proposal)
    assert proposal.status == Status.REJECTED and proposal.supersedes_id is None
    assert version.status == Status.APPROVED and version.object_text == "gcp"
    assert target.superseded_by_id == version.pk and target.valid_to == T1


def test_edit_of_a_pending_proposal_follows_the_target_chain(owner, personal, api, hetzner):
    target = fact(personal, api, obj=hetzner, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T2, status="pending_approval", supersedes=target
    )
    head = fact(personal, api, text="gcp", valid_from=T1)
    services.supersede(target, head)
    version = services.edit_fact(fact=proposal, actor=owner, channel=WEB, statement="edited")
    head.refresh_from_db()
    assert head.superseded_by_id == version.pk


def test_supersede_fact_api_service(owner, personal, api):
    old = fact(personal, api, valid_from=T0)
    version = services.supersede_fact(
        fact=old, actor=owner, channel=WEB, valid_from=T1, object_text="aws"
    )
    old.refresh_from_db()
    assert old.superseded_by_id == version.pk


def test_superseded_history_cannot_be_edited_or_trashed(owner, personal, api):
    old = fact(personal, api, valid_from=T0, valid_to=T1)
    with pytest.raises(services.GraphInvalidTransition):
        services.edit_fact(fact=old, actor=owner, channel=WEB, statement="x")
    with pytest.raises(services.GraphInvalidTransition):
        services.trash_fact(fact=old, actor=owner, channel=WEB)


# --- trash / restore ---------------------------------------------------------------------


def test_trash_and_restore_fact(owner, personal, api):
    row = fact(personal, api)
    services.trash_fact(fact=row, actor=owner, channel=WEB)
    assert not MemoryFact.objects.filter(pk=row.pk).exists()
    services.restore_fact(fact=MemoryFact.all_objects.get(pk=row.pk), actor=owner, channel=WEB)
    assert MemoryFact.objects.filter(pk=row.pk).exists()


def test_trash_entity_trashes_its_learned_facts_and_restore_undoes_exactly_them(
    owner, personal, api, hetzner
):
    a = fact(personal, api, obj=hetzner)
    b = fact(personal, entity(personal, "web"), obj=hetzner, valid_from=T1)
    earlier = fact(personal, api, MemoryFact.Predicate.USES)
    services.trash_fact(fact=earlier, actor=owner, channel=WEB)
    result = services.trash_entity(entity=hetzner, actor=owner, channel=WEB)
    assert result["facts"] == 2
    assert not MemoryFact.objects.filter(pk__in=[a.pk, b.pk]).exists()
    services.restore_entity(
        entity=MemoryEntity.all_objects.get(pk=hetzner.pk), actor=owner, channel=WEB
    )
    assert MemoryFact.objects.filter(pk__in=[a.pk, b.pk]).count() == 2
    assert not MemoryFact.objects.filter(pk=earlier.pk).exists()  # its own trash stays


def test_owner_delete_needs_web(owner, personal, api):
    row = fact(personal, api)
    with pytest.raises(services.GraphPermissionDenied):
        services.trash_fact(fact=row, actor=owner, channel=CHANNEL_CLI)


def test_db_check_is_only_a_backstop(owner, personal, api):
    # A supersede-order violation never surfaces as IntegrityError.
    old = fact(personal, api, valid_from=T1)
    new = fact(personal, api, text="later", valid_from=T1 - timedelta(days=1))
    try:
        services.supersede(old, new)
    except IntegrityError:  # pragma: no cover
        pytest.fail("IntegrityError reached the caller")
    except services.SupersedeOrder:
        pass


def test_as_of_boundaries_are_half_open(personal, api):
    old = fact(personal, api, valid_from=T0, valid_to=T1)
    new = fact(personal, api, text="aws", valid_from=T1)
    qs = MemoryFact.objects.filter(subject=api)
    assert list(qs.as_of(T0)) == [old]
    assert list(qs.as_of(T1 - timedelta(microseconds=1))) == [old]
    assert list(qs.as_of(T1)) == [new]
    assert list(qs.as_of(T0 - timedelta(microseconds=1))) == []
    assert list(qs.as_of(timezone.now())) == [new]
