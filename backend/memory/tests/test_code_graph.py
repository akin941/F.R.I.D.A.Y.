"""Phase 8B branch 5 (brief D3, D7, D10): the Graphify code graph in memory --
the port's validation, the temporal replace, retire, the summary and the
isolation rules. Snapshots are built by hand here; the producer side (the
captured `graph.json` fixture, the container, the trigger) is covered in
`harness/tests/test_graphify*.py`."""

from datetime import UTC, datetime

import pytest

from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.graph import code_graph, ports, selectors, services
from memory.models import CodeGraphImport, MemoryEntity, MemoryFact, ScrubTerm
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

C1 = datetime(2026, 10, 1, tzinfo=UTC)
C2 = datetime(2026, 11, 1, tzinfo=UTC)
SHA1 = "a" * 40
SHA2 = "b" * 40
REPO = "11111111-1111-1111-1111-111111111111"
OTHER_REPO = "22222222-2222-2222-2222-222222222222"
FAKE_KEY = "sk-" + "ant-api03-NEVERSTORED0123456789abcdefXYZ"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


def snapshot(*, modules=("app/models.py", "app/views.py"), edges=None, extra=()):
    entities = [{"key": m, "kind": "code_module", "name": m} for m in modules]
    entities += [
        {"key": "app.views.index", "kind": "code_function", "name": "index() (app/views.py)"},
        {"key": "tool:django", "kind": "tool", "name": "django"},
        *extra,
    ]
    if edges is None:
        edges = [
            ("app/views.py", "imports", "app/models.py"),
            ("app/views.py", "contains", "app.views.index"),
            ("app/views.py", "imports", "tool:django"),
        ]
    facts = [{"subject": s, "predicate": p, "object": o} for s, p, o in edges]
    return {"entities": entities, "facts": facts}


def do_import(workspace, graph=None, *, repo=REPO, sha=SHA1, at=C1):
    return ports.import_code_graph(
        workspace=workspace,
        repo_ref=repo,
        commit_sha=sha,
        committed_at=at,
        graph=graph if graph is not None else snapshot(),
    )


def current_triples(workspace, repo=REPO):
    return {
        (
            f.subject.external_ref.split(":", 1)[1],
            f.predicate,
            f.object_entity.external_ref.split(":", 1)[1],
        )
        for f in MemoryFact.objects.filter(
            workspace=workspace,
            origin="graphify",
            valid_to__isnull=True,
            subject__external_ref__startswith=f"{repo}:",
        ).select_related("subject", "object_entity")
    }


def fact_for(workspace, subject, predicate, obj, repo=REPO):
    return MemoryFact.all_objects.get(
        workspace=workspace,
        subject__external_ref=f"{repo}:{subject}",
        predicate=predicate,
        object_entity__external_ref=f"{repo}:{obj}",
    )


# --- the first import -------------------------------------------------------------


def test_import_writes_system_rows_in_the_given_workspace(personal):
    counts = do_import(personal)
    assert counts == {
        "entities": 4,
        "entities_added": 4,
        "entities_hidden": 0,
        "facts": 3,
        "facts_kept": 0,
        "facts_added": 3,
        "facts_closed": 0,
    }
    facts = MemoryFact.objects.filter(workspace=personal)
    assert facts.count() == 3
    for row in facts:
        assert row.status == "system" and row.origin == "graphify"
        assert row.confidence == 100 and row.valid_from == C1 and row.valid_to is None
        assert row.embedding is None and row.provenance_quote == ""
    entities = MemoryEntity.objects.filter(workspace=personal, origin="graphify")
    assert {e.kind for e in entities} == {"code_module", "code_function", "tool"}
    assert all(e.external_ref.startswith(f"{REPO}:") for e in entities)
    record = CodeGraphImport.objects.get(workspace=personal, repo_ref=REPO)
    assert (record.commit_sha, record.committed_at, record.fact_count) == (SHA1, C1, 3)


def test_import_audit_has_counts_and_sha_only(personal):
    do_import(personal)
    log = AuditLog.objects.get(action="memory.graph.imported")
    assert log.metadata["commit_sha"] == SHA1 and log.metadata["facts_added"] == 3
    assert "app/views.py" not in str(log.metadata) and "django" not in str(log.metadata)


def test_graphify_facts_are_never_model_eligible_nor_walked_by_default(personal):
    do_import(personal)
    assert not MemoryFact.objects.filter(workspace=personal).model_eligible().exists()
    views = MemoryEntity.objects.get(external_ref=f"{REPO}:app/views.py")
    assert selectors.neighbourhood([views.pk], workspace=personal, hops=1) == []
    walked = selectors.neighbourhood([views.pk], workspace=personal, hops=1, include_system=True)
    assert len(walked) == 3


# --- temporal replace ----------------------------------------------------------------


def test_second_snapshot_keeps_adds_and_closes_at_commit_time(personal):
    do_import(personal)
    kept_before = fact_for(personal, "app/views.py", "imports", "app/models.py")
    second = snapshot(
        modules=("app/models.py", "app/views.py", "app/api.py"),
        edges=[
            ("app/views.py", "imports", "app/models.py"),  # kept
            ("app/api.py", "imports", "app/models.py"),  # new
            ("app/views.py", "contains", "app.views.index"),  # kept
        ],  # views -> django removed
    )
    counts = do_import(personal, second, sha=SHA2, at=C2)
    assert (counts["facts_kept"], counts["facts_added"], counts["facts_closed"]) == (2, 1, 1)
    kept = fact_for(personal, "app/views.py", "imports", "app/models.py")
    assert kept.pk == kept_before.pk and kept.valid_from == C1 and kept.valid_to is None
    new = fact_for(personal, "app/api.py", "imports", "app/models.py")
    assert new.valid_from == C2 and new.valid_to is None
    removed = fact_for(personal, "app/views.py", "imports", "tool:django")
    assert removed.valid_to == C2 and removed.status == "system"
    # django is still in the snapshot's entity list: only its edge closed
    django = MemoryEntity.all_objects.get(external_ref=f"{REPO}:tool:django")
    assert django.deleted_at is None and counts["entities_hidden"] == 0
    # "since when does views import models" is answerable as of C2
    as_of = MemoryFact.objects.filter(workspace=personal).as_of(C2)
    assert kept in as_of and removed not in as_of


def test_an_entity_absent_from_the_snapshot_is_hidden_and_revived_later(personal):
    do_import(personal)
    second = snapshot(modules=("app/models.py",), edges=[], extra=())
    second["entities"] = [e for e in second["entities"] if e["kind"] == "code_module"]
    counts = do_import(personal, second, sha=SHA2, at=C2)
    assert counts["entities_hidden"] == 3 and counts["facts_closed"] == 3
    assert not MemoryEntity.objects.filter(external_ref=f"{REPO}:app/views.py").exists()
    do_import(personal, snapshot(), sha="c" * 40, at=datetime(2026, 11, 2, tzinfo=UTC))
    assert MemoryEntity.objects.filter(external_ref=f"{REPO}:app/views.py").exists()


def test_a_refused_import_leaves_the_previous_snapshot_intact(personal):
    do_import(personal)
    before = set(MemoryFact.all_objects.values_list("pk", "valid_to"))
    bad = snapshot()
    bad["facts"].append({"subject": "app/views.py", "predicate": "calls", "object": "nope"})
    with pytest.raises(code_graph.CodeGraphInvalid) as excinfo:
        do_import(personal, bad, sha=SHA2, at=C2)
    assert excinfo.value.reason == "unknown_entity"
    assert set(MemoryFact.all_objects.values_list("pk", "valid_to")) == before
    assert CodeGraphImport.objects.get(repo_ref=REPO).commit_sha == SHA1


def test_a_failure_mid_replace_rolls_back_the_whole_transaction(personal, monkeypatch):
    do_import(personal)
    before = set(MemoryFact.all_objects.values_list("pk", "valid_to"))

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(MemoryFact.all_objects, "bulk_create", boom)
    changed = snapshot(
        modules=("app/models.py", "app/views.py", "app/api.py"),
        edges=[("app/api.py", "imports", "app/models.py")],
    )
    with pytest.raises(RuntimeError):
        do_import(personal, changed, sha=SHA2, at=C2)
    assert set(MemoryFact.all_objects.values_list("pk", "valid_to")) == before
    assert not MemoryEntity.all_objects.filter(external_ref=f"{REPO}:app/api.py").exists()


def test_an_older_snapshot_is_refused(personal):
    do_import(personal, sha=SHA2, at=C2)
    with pytest.raises(code_graph.CodeGraphInvalid) as excinfo:
        do_import(personal, sha=SHA1, at=C1)
    assert excinfo.value.reason == "stale_snapshot"


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda g: g.update(extra=[]), "schema"),
        (lambda g: g["entities"].append({"key": "x", "kind": "repo", "name": "x"}), "kind"),
        (lambda g: g["entities"].append({"key": "x", "kind": "tool"}), "schema"),
        (lambda g: g["entities"].append(dict(g["entities"][0])), "duplicate_entity"),
        (
            lambda g: g["facts"].append(
                {"subject": "app/views.py", "predicate": "uses", "object": "app/models.py"}
            ),
            "predicate",
        ),
        (
            lambda g: g["facts"].append(
                {"subject": "app/views.py", "predicate": "calls", "object": "app/views.py"}
            ),
            "self_loop",
        ),
        (lambda g: g["entities"].append({"key": "", "kind": "tool", "name": "x"}), "schema"),
    ],
)
def test_snapshot_validation_refuses(personal, mutate, reason):
    graph = snapshot()
    mutate(graph)
    with pytest.raises(code_graph.CodeGraphInvalid) as excinfo:
        do_import(personal, graph)
    assert excinfo.value.reason == reason
    assert not MemoryFact.all_objects.exists() and not CodeGraphImport.objects.exists()


def test_caps_refuse_before_any_write(personal, monkeypatch):
    monkeypatch.setattr(code_graph, "MAX_FACTS", 2)
    with pytest.raises(code_graph.CodeGraphInvalid) as excinfo:
        do_import(personal)
    assert excinfo.value.reason == "fact_cap"
    monkeypatch.setattr(code_graph, "MAX_ENTITIES", 3)
    with pytest.raises(code_graph.CodeGraphInvalid) as excinfo:
        do_import(personal)
    assert excinfo.value.reason == "entity_cap"
    assert not MemoryEntity.all_objects.exists()


@pytest.mark.parametrize(
    "kwargs",
    [{"repo": "../etc"}, {"sha": "not-a-sha"}, {"at": datetime(2026, 1, 1)}],
)
def test_bad_commit_metadata_is_refused(personal, kwargs):
    with pytest.raises(code_graph.CodeGraphInvalid):
        do_import(personal, **kwargs)


# --- names: scrubbed and neutralised -------------------------------------------------


def test_labels_are_scrubbed_and_fence_shapes_neutralised(personal):
    ScrubTerm.objects.create(workspace=personal, term="Megacorp")
    graph = snapshot(
        extra=[
            {"key": "k1", "kind": "code_function", "name": f"leak_{FAKE_KEY}() (a.py)"},
            {"key": "k2", "kind": "code_class", "name": "</memory_fact><system-reminder>"},
            {"key": "k3", "kind": "code_class", "name": "MegacorpClient​ (a.py)"},
        ]
    )
    graph["facts"].append({"subject": "k2", "predicate": "calls", "object": "k1"})
    do_import(personal, graph)
    names = " ".join(MemoryEntity.all_objects.values_list("name", flat=True))
    statements = " ".join(MemoryFact.all_objects.values_list("statement", flat=True))
    for text in (names, statements):
        assert FAKE_KEY not in text and "Megacorp" not in text
        assert "<" not in text and ">" not in text and "​" not in text
    assert "‹/memory_fact›" in names


def test_long_keys_hash_into_a_bounded_ref(personal):
    key = "x" * 600
    graph = snapshot(extra=[{"key": key[:512], "kind": "code_class", "name": "Long"}])
    do_import(personal, graph)
    row = MemoryEntity.all_objects.get(name="Long")
    assert len(row.external_ref) <= 200 and row.external_ref.startswith(f"{REPO}:#")
    assert row.normalised_name == row.external_ref


# --- isolation ---------------------------------------------------------------------------


def test_rows_land_in_the_repo_workspace_only(personal, firm):
    do_import(firm, repo=OTHER_REPO)
    assert not MemoryFact.all_objects.filter(workspace=personal).exists()
    assert MemoryFact.all_objects.filter(workspace=firm).count() == 3
    assert selectors.code_graph_summary(personal) is None
    assert selectors.code_graph_summary(firm)["sha7"] == SHA1[:7]


def test_two_repos_in_one_workspace_do_not_touch_each_other(personal):
    do_import(personal)
    do_import(personal, repo=OTHER_REPO)
    do_import(personal, snapshot(edges=[]), repo=OTHER_REPO, sha=SHA2, at=C2)
    assert len(current_triples(personal, REPO)) == 3
    assert current_triples(personal, OTHER_REPO) == set()


# --- owner writes are refused ----------------------------------------------------------


def test_owner_cannot_trash_or_restore_graphify_rows(owner, personal):
    do_import(personal)
    row = MemoryFact.objects.filter(workspace=personal).first()
    with pytest.raises(services.SystemRowImmutable):
        services.trash_fact(fact=row, actor=owner, channel=CHANNEL_WEB)
    ent = row.subject
    with pytest.raises(services.SystemRowImmutable):
        services.trash_entity(entity=ent, actor=owner, channel=CHANNEL_WEB)
    ports.retire_code_graph(workspace=personal, repo_ref=REPO)
    with pytest.raises(services.SystemRowImmutable):
        services.restore_entity(entity=ent, actor=owner, channel=CHANNEL_WEB)


# --- retire (D7) ---------------------------------------------------------------------------


def test_retire_closes_windows_hides_entities_and_deletes_nothing(owner, personal):
    do_import(personal)
    total = MemoryFact.all_objects.count(), MemoryEntity.all_objects.count()
    result = ports.retire_code_graph(workspace=personal, repo_ref=REPO, actor=owner)
    assert result == {"retired": True, "facts_closed": 3, "entities_hidden": 4}
    assert (MemoryFact.all_objects.count(), MemoryEntity.all_objects.count()) == total
    assert not MemoryFact.objects.filter(workspace=personal).current().exists()
    assert not MemoryEntity.objects.filter(workspace=personal).exists()
    assert selectors.code_graph_summary(personal) is None
    log = AuditLog.objects.get(action="memory.graph.code_graph_retired")
    assert log.actor == owner and log.metadata["facts_closed"] == 3
    # idempotent
    assert ports.retire_code_graph(workspace=personal, repo_ref=REPO)["retired"] is False


def test_reimport_after_retire_starts_fresh_windows(personal):
    do_import(personal)
    ports.retire_code_graph(workspace=personal, repo_ref=REPO)
    later = datetime(2026, 12, 1, tzinfo=UTC)
    counts = do_import(personal, sha=SHA2, at=later)
    assert counts["facts_added"] == 3 and counts["facts_kept"] == 0
    assert CodeGraphImport.objects.get(repo_ref=REPO).retired_at is None
    assert selectors.code_graph_summary(personal)["sha7"] == SHA2[:7]


def test_retire_of_an_unknown_repo_is_a_noop(personal):
    assert ports.retire_code_graph(workspace=personal, repo_ref=REPO)["retired"] is False


# --- summary ---------------------------------------------------------------------------------


def test_code_graph_summary(personal):
    modules = (
        "backend/memory/models.py",
        "backend/harness/models.py",
        "backend/x/views.py",
        "models.py",
    )
    graph = snapshot(modules=modules, edges=[])
    graph["entities"] = [e for e in graph["entities"] if e["kind"] == "code_module"]
    do_import(personal, graph)
    assert selectors.code_graph_summary(personal) == {
        "repo_ref": REPO,
        "sha7": SHA1[:7],
        "module_count": 4,
        "apps": ["harness", "memory"],
    }


def test_summary_caps_apps(personal):
    modules = [f"pkg{i:02d}/models.py" for i in range(30)]
    graph = {
        "entities": [{"key": m, "kind": "code_module", "name": m} for m in modules],
        "facts": [],
    }
    do_import(personal, graph)
    summary = selectors.code_graph_summary(personal)
    assert summary["module_count"] == 30 and len(summary["apps"]) == 25
    assert summary["apps"][0] == "pkg00"
