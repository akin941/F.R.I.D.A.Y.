"""Phase 8B branch 1 (brief D2, D4): the recursive-CTE neighbourhood, the
as-of reads, entity timelines and derived entity privacy; plus the fact
object validator branch 2 builds on."""

import uuid

import pytest
from django.utils import timezone

from memory.graph import selectors, services
from memory.graph.candidates import FactCandidateInvalid, normalise_name, validate_fact_object
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, MemoryEntity, MemoryFact
from memory.tests.graph_helpers import T0, T1, T2, entity, fact, system_fact

pytestmark = pytest.mark.django_db


@pytest.fixture
def graph(personal):
    """a -> b -> c -> d, plus b -> e (a text leaf off c), a cycle c -> a."""
    names = "abcde"
    nodes = {n: entity(personal, n) for n in names}
    edges = {
        "ab": fact(personal, nodes["a"], obj=nodes["b"]),
        "bc": fact(personal, nodes["b"], obj=nodes["c"]),
        "cd": fact(personal, nodes["c"], obj=nodes["d"]),
        "be": fact(personal, nodes["b"], obj=nodes["e"]),
        "ca": fact(personal, nodes["c"], MemoryFact.Predicate.USES, obj=nodes["a"]),
        "c_text": fact(personal, nodes["c"], MemoryFact.Predicate.HAS_STATUS, text="live"),
    }
    return nodes, edges


def _ids(facts):
    return {f.pk for f in facts}


def test_one_hop(personal, graph):
    nodes, edges = graph
    out = selectors.neighbourhood([nodes["a"].pk], workspace=personal, hops=1)
    assert _ids(out) == {edges["ab"].pk, edges["ca"].pk}
    assert all(f.hop == 1 for f in out)


def test_two_hops(personal, graph):
    nodes, edges = graph
    out = selectors.neighbourhood([nodes["a"].pk], workspace=personal, hops=2)
    assert _ids(out) == {edges[k].pk for k in ("ab", "ca", "bc", "be", "cd", "c_text")}
    hop = {f.pk: f.hop for f in out}
    assert hop[edges["ab"].pk] == 1 and hop[edges["cd"].pk] == 2


def test_cycle_terminates(personal):
    x, y = entity(personal, "x"), entity(personal, "y")
    fact(personal, x, obj=y)
    fact(personal, y, obj=x)
    out = selectors.neighbourhood([x.pk], workspace=personal, hops=2)
    assert len(out) == 2


@pytest.mark.parametrize("hops", [0, 3, True, "1"])
def test_bad_hops_are_refused(personal, graph, hops):
    nodes, _ = graph
    with pytest.raises(selectors.InvalidHops):
        selectors.neighbourhood([nodes["a"].pk], workspace=personal, hops=hops)


def test_edge_cap_holds(personal):
    hub = entity(personal, "hub")
    for i in range(selectors.MAX_EDGES + 15):
        fact(personal, hub, text=f"t{i}")
    out = selectors.neighbourhood([hub.pk], workspace=personal, hops=1)
    assert len(out) == selectors.MAX_EDGES


def test_other_workspace_never_appears_even_with_colliding_names(personal, firm, graph):
    nodes, edges = graph
    firm_a, firm_b = entity(firm, "a"), entity(firm, "b")
    firm_edge = fact(firm, firm_a, obj=firm_b)
    out = selectors.neighbourhood(
        [nodes["a"].pk, firm_a.pk],
        workspace=personal,
        hops=2,
        include_system=True,
        include_private=True,
    )
    assert firm_edge.pk not in _ids(out)
    assert all(f.workspace_id == personal.pk for f in out)
    # a firm seed under the personal workspace yields nothing
    assert selectors.neighbourhood([firm_a.pk], workspace=personal, hops=2) == []


def test_flags_gate_system_and_private_facts(personal):
    seed, other = entity(personal, "seed"), entity(personal, "other")
    normal = fact(personal, seed, obj=other)
    system = system_fact(personal, seed, other)
    private = fact(personal, seed, MemoryFact.Predicate.USES, obj=other, sensitivity="private")
    default = _ids(selectors.neighbourhood([seed.pk], workspace=personal, hops=1))
    assert default == {normal.pk}
    with_system = _ids(
        selectors.neighbourhood([seed.pk], workspace=personal, hops=1, include_system=True)
    )
    assert with_system == {normal.pk, system.pk}
    with_private = _ids(
        selectors.neighbourhood([seed.pk], workspace=personal, hops=1, include_private=True)
    )
    assert with_private == {normal.pk, private.pk}


def test_private_and_system_edges_are_not_walked_through_in_the_recursive_term(personal):
    seed, mid, far = entity(personal, "seed"), entity(personal, "mid"), entity(personal, "far")
    fact(personal, seed, obj=mid)
    hidden = fact(personal, mid, obj=far, sensitivity="private")
    out = selectors.neighbourhood([seed.pk], workspace=personal, hops=2)
    assert hidden.pk not in _ids(out)


def test_pending_rejected_trashed_and_superseded_are_not_edges(personal, owner):
    seed, other = entity(personal, "seed"), entity(personal, "other")
    fact(personal, seed, obj=other, status="pending_approval")
    fact(personal, seed, obj=other, status="rejected")
    closed = fact(personal, seed, obj=other, valid_to=T1)
    trashed = fact(personal, seed, obj=other)
    MemoryFact.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    assert selectors.neighbourhood([seed.pk], workspace=personal, hops=1) == []
    # as of a past instant, the superseded version is an edge
    past = selectors.neighbourhood([seed.pk], workspace=personal, hops=1, as_of=T0)
    assert _ids(past) == {closed.pk}


def test_trashed_entity_hides_its_edges(personal):
    seed, other = entity(personal, "seed"), entity(personal, "other")
    fact(personal, seed, obj=other)
    MemoryEntity.all_objects.filter(pk=other.pk).update(deleted_at=timezone.now())
    assert selectors.neighbourhood([seed.pk], workspace=personal, hops=1) == []


def test_facts_as_of_and_current(personal):
    s = entity(personal, "s")
    v1 = fact(personal, s, text="v1", valid_from=T0, valid_to=T1)
    v2 = fact(personal, s, text="v2", valid_from=T1, valid_to=T2)
    v3 = fact(personal, s, text="v3", valid_from=T2)
    qs = MemoryFact.objects.filter(subject=s)
    assert list(selectors.facts_as_of(qs, T0)) == [v1]
    assert list(selectors.facts_as_of(qs, T1)) == [v2]
    assert list(selectors.facts_as_of(qs, T2)) == [v3]
    assert list(selectors.current_facts(qs)) == [v3]


def test_entity_timeline_is_oldest_first_with_history(personal):
    s = entity(personal, "s")
    v2 = fact(personal, s, text="v2", valid_from=T1)
    v1 = fact(personal, s, text="v1", valid_from=T0, valid_to=T1)
    private = fact(personal, s, text="p", valid_from=T2, sensitivity="private")
    assert list(selectors.entity_timeline(s)) == [v1, v2]
    assert list(selectors.entity_timeline(s, include_private=True)) == [v1, v2, private]


def test_entity_privacy_is_derived(personal, owner):
    ensure_default_sources(workspace=personal, created_by=owner)
    source = IngestionSource.objects.get(workspace=personal, kind="git")
    e = entity(personal, "secret project", kind=MemoryEntity.Kind.PROJECT)
    fact(personal, e, MemoryFact.Predicate.HAS_STATUS, text="x", sensitivity="private")
    assert selectors.entity_is_private(e)
    normal = fact(personal, e, MemoryFact.Predicate.HAS_STATUS, text="y", source=source)
    assert not selectors.entity_is_private(e)
    services.forget_source_facts(source, uuid.uuid4())
    normal.refresh_from_db()
    assert normal.status == MemoryFact.Status.ARCHIVED
    assert selectors.entity_is_private(e)


def test_with_privacy_agrees_with_entity_is_private(personal):
    only_private = entity(personal, "p1")
    fact(personal, only_private, text="x", sensitivity="private")
    mixed = entity(personal, "m1")
    fact(personal, mixed, text="x", sensitivity="private")
    fact(personal, mixed, MemoryFact.Predicate.USES, text="y")
    as_object = entity(personal, "o1")
    fact(personal, entity(personal, "s1"), obj=as_object)
    pending_only = entity(personal, "pend")
    fact(personal, pending_only, text="z", status="pending_approval")
    closed_only = entity(personal, "closed")
    fact(personal, closed_only, text="z", valid_to=T1)
    bare = entity(personal, "bare")
    rows = MemoryEntity.objects.filter(workspace=personal).with_privacy()
    annotated = {e.pk: e.is_private for e in rows}
    for e in (only_private, mixed, as_object, pending_only, closed_only, bare):
        assert annotated[e.pk] == selectors.entity_is_private(e), e.name
    assert annotated[only_private.pk] and annotated[closed_only.pk] and annotated[bare.pk]
    assert not annotated[mixed.pk] and not annotated[as_object.pk]
    assert not annotated[pending_only.pk]


# --- candidates (the fact object of schema v2) ---------------------------------


def _obj(**over):
    value = {
        "subject": {"kind": "service", "name": "API"},
        "predicate": "deployed_to",
        "object": {"kind": "tool", "name": "Hetzner"},
        "valid_from": "2026-11-19",
    }
    value.update(over)
    return value


def test_validate_fact_object_accepts_entity_and_text_objects():
    parsed = validate_fact_object(_obj())
    assert parsed.subject.name == "API" and parsed.object_entity.kind == "tool"
    parsed = validate_fact_object(_obj(object={"text": "  hazır  "}))
    assert parsed.object_entity is None and parsed.object_text == "hazır"


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"predicate": "imports"}, "fact_predicate"),
        ({"predicate": "invented"}, "fact_predicate"),
        ({"subject": {"kind": "code_module", "name": "x"}}, "fact_kind"),
        ({"subject": {"kind": "service", "name": ""}}, "fact_name"),
        ({"subject": {"kind": "service", "name": "x" * 201}}, "fact_name"),
        ({"subject": {"kind": "service", "name": "x", "extra": 1}}, "fact_schema"),
        ({"object": {"text": ""}}, "fact_object_text"),
        ({"object": {"text": "x", "kind": "tool"}}, "fact_schema"),
        ({"valid_from": "19.11.2026"}, "fact_valid_from"),
        ({"valid_from": "2026-02-30"}, "fact_valid_from"),
        ({"extra": True}, "fact_schema"),
    ],
)
def test_validate_fact_object_is_strict(over, reason):
    with pytest.raises(FactCandidateInvalid) as info:
        validate_fact_object(_obj(**over))
    assert info.value.reason == reason


def test_validate_fact_object_needs_every_key():
    value = _obj()
    del value["valid_from"]
    with pytest.raises(FactCandidateInvalid):
        validate_fact_object(value)
    with pytest.raises(FactCandidateInvalid):
        validate_fact_object(None)


def test_normalise_name():
    assert normalise_name("  Friday   OS ") == "friday os"
    assert normalise_name("ＡＰＩ") == "api"  # NFKC
