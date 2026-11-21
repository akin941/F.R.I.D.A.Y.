"""Phase 8B branch 1 (brief D3): the owner graph API. Role x endpoint against
the five Phase 4 principals plus a `hook` PAT, another workspace's rows ->
404, `system` rows -> 405, the 409 conflicts, audit rows with ids and counts
only, and workspace isolation.

PATs: these views use default authentication only, so an owner
`cli`/`openai`/`hook` token is not authenticated at all -> 401.
"""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from memory.models import MemoryEntity, MemoryFact
from memory.tests.graph_helpers import T0, T1, entity, fact, system_fact
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

BASE = "/api/v1/memory/graph"
Status = MemoryFact.Status


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal.startswith("owner-pat-"):
        return _pat_client(owner, workspace, principal.removeprefix("owner-pat-"))
    if principal == "non-owner":
        member = register_user(email="member@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(
            workspace=workspace, user=member, role=WorkspaceMember.Role.MEMBER
        )
        return web_client(member)
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    ("owner-web", "ok"),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("owner-pat-hook", 401),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.fixture
def setup(personal):
    api = entity(personal, "api")
    hetzner = entity(personal, "hetzner", kind=MemoryEntity.Kind.TOOL)
    trashed_entity = entity(personal, "old", kind=MemoryEntity.Kind.TOOL)
    current = fact(personal, api, obj=hetzner, valid_from=T0)
    rows = {
        "api": api,
        "hetzner": hetzner,
        "trashed_entity": trashed_entity,
        "current": current,
        "pending_a": fact(personal, api, MemoryFact.Predicate.USES, status="pending_approval"),
        "pending_b": fact(personal, api, MemoryFact.Predicate.PREFERS, status="pending_approval"),
        "to_edit": fact(personal, api, MemoryFact.Predicate.WORKS_ON, valid_from=T0),
        "to_trash": fact(personal, api, MemoryFact.Predicate.OWNED_BY),
        "to_restore": fact(personal, api, MemoryFact.Predicate.BLOCKED_BY),
        "to_supersede": fact(personal, api, MemoryFact.Predicate.HAS_STATUS, valid_from=T0),
    }
    from django.utils import timezone

    MemoryFact.all_objects.filter(pk=rows["to_restore"].pk).update(deleted_at=timezone.now())
    MemoryEntity.all_objects.filter(pk=trashed_entity.pk).update(deleted_at=timezone.now())
    return rows


def _endpoints(personal, rows):
    """(method, url, body, success status, write?)."""
    ws = personal.id
    ent = f"{BASE}/entities/{rows['api'].id}"
    return [
        ("get", f"{BASE}/entities?workspace={ws}", None, 200, False),
        ("get", f"{ent}?hops=2", None, 200, False),
        ("get", f"{ent}/facts", None, 200, False),
        ("delete", f"{BASE}/entities/{rows['hetzner'].id}", None, 200, True),
        ("post", f"{BASE}/entities/{rows['trashed_entity'].id}/restore", {}, 200, True),
        ("get", f"{BASE}/facts?workspace={ws}", None, 200, False),
        (
            "post",
            f"{BASE}/facts",
            {
                "workspace": str(ws),
                "subject_kind": "service",
                "subject_name": "web",
                "predicate": "uses",
                "object_text": "react",
            },
            201,
            True,
        ),
        ("get", f"{BASE}/facts/{rows['current'].id}", None, 200, False),
        ("patch", f"{BASE}/facts/{rows['to_edit'].id}", {"statement": "yeni"}, 200, True),
        ("delete", f"{BASE}/facts/{rows['to_trash'].id}", None, 204, True),
        ("post", f"{BASE}/facts/{rows['pending_a'].id}/approve", {}, 200, True),
        ("post", f"{BASE}/facts/{rows['pending_b'].id}/reject", {}, 200, True),
        (
            "post",
            f"{BASE}/facts/{rows['to_supersede'].id}/supersede",
            {"valid_from": T1.isoformat(), "object_text": "frozen"},
            200,
            True,
        ),
        ("post", f"{BASE}/facts/{rows['to_restore'].id}/restore", {}, 200, True),
    ]


ENDPOINT_IDS = [
    "list-entities",
    "entity-detail",
    "entity-facts",
    "entity-trash",
    "entity-restore",
    "list-facts",
    "create-fact",
    "fact-detail",
    "fact-edit",
    "fact-trash",
    "fact-approve",
    "fact-reject",
    "fact-supersede",
    "fact-restore",
]


def _snapshot():
    return (
        sorted(
            MemoryFact.all_objects.values_list(
                "id", "status", "valid_to", "deleted_at", "superseded_by"
            )
        ),
        sorted(MemoryEntity.all_objects.values_list("id", "deleted_at")),
    )


@pytest.mark.parametrize("index", range(len(ENDPOINT_IDS)), ids=ENDPOINT_IDS)
@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_role_matrix(owner, personal, setup, web_client, principal, expected, index):
    method, url, body, success, write = _endpoints(personal, setup)[index]
    client = _client(principal, owner, personal, web_client)
    before = _snapshot()
    response = getattr(client, method)(url, body, format="json")
    assert response.status_code == (success if expected == "ok" else expected), response.content
    if expected != "ok":
        assert _snapshot() == before  # nothing changed
    elif write:
        assert _snapshot() != before


@pytest.fixture
def foreign(db):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    workspace = Workspace.objects.get(owner=stranger)
    e = entity(workspace, "api")
    return {
        "workspace": workspace,
        "entity": e,
        "fact": fact(workspace, e, status="pending_approval"),
    }


def test_another_workspaces_rows_are_404(owner, personal, web_client, foreign):
    client = web_client(owner)
    ent = f"{BASE}/entities/{foreign['entity'].id}"
    fid = f"{BASE}/facts/{foreign['fact'].id}"
    ws = foreign["workspace"].id
    assert client.get(f"{BASE}/entities?workspace={ws}").status_code == 404
    assert client.get(f"{BASE}/facts?workspace={ws}").status_code == 404
    assert client.get(ent).status_code == 404
    assert client.get(f"{ent}/facts").status_code == 404
    assert client.delete(ent).status_code == 404
    assert client.post(f"{ent}/restore", {}, format="json").status_code == 404
    assert client.get(fid).status_code == 404
    assert client.patch(fid, {"statement": "x"}, format="json").status_code == 404
    assert client.delete(fid).status_code == 404
    for action in ("approve", "reject", "supersede", "restore"):
        assert client.post(f"{fid}/{action}", {}, format="json").status_code == 404
    body = {
        "workspace": str(ws),
        "subject_kind": "service",
        "subject_name": "x",
        "predicate": "uses",
        "object_text": "y",
    }
    assert client.post(f"{BASE}/facts", body, format="json").status_code == 404
    foreign["fact"].refresh_from_db()
    assert foreign["fact"].status == Status.PENDING_APPROVAL


def test_system_rows_are_405(owner, personal, web_client):
    mod = entity(personal, "memory.graph", kind="code_module", origin="graphify")
    other = entity(personal, "memory.models", kind="code_module", origin="graphify")
    row = system_fact(personal, mod, other)
    client = web_client(owner)
    url = f"{BASE}/facts/{row.id}"
    assert client.post(f"{url}/approve", {}, format="json").status_code == 405
    assert client.post(f"{url}/reject", {}, format="json").status_code == 405
    assert client.patch(url, {"statement": "x"}, format="json").status_code == 405
    assert client.delete(url).status_code == 405
    response = client.post(
        f"{url}/supersede", {"valid_from": T1.isoformat(), "object_text": "x"}, format="json"
    )
    assert response.status_code == 405
    assert response.json()["code"] == "memory.graph.system_row"
    assert client.delete(f"{BASE}/entities/{mod.id}").status_code == 405
    row.refresh_from_db()
    assert row.status == Status.SYSTEM and row.deleted_at is None
    # but readable
    assert client.get(url).status_code == 200


def test_conflicts_are_409_with_stable_codes(owner, personal, web_client):
    api = entity(personal, "api")
    target = fact(personal, api, valid_from=T1)
    client = web_client(owner)
    response = client.patch(
        f"{BASE}/facts/{target.id}",
        {"statement": "x", "valid_from": T0.isoformat()},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "memory.graph.supersede_order"
    assert MemoryFact.all_objects.count() == 1

    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    client.delete(f"{BASE}/facts/{target.id}")
    response = client.post(f"{BASE}/facts/{proposal.id}/approve", {}, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "memory.graph.supersede_target_changed"
    proposal.refresh_from_db()
    assert proposal.status == Status.PENDING_APPROVAL

    approved = fact(personal, api, MemoryFact.Predicate.USES)
    response = client.post(f"{BASE}/facts/{approved.id}/approve", {}, format="json")
    assert response.status_code == 409


def test_unknown_action_and_bad_hops(owner, personal, web_client):
    api = entity(personal, "api")
    row = fact(personal, api, status="pending_approval")
    client = web_client(owner)
    assert client.post(f"{BASE}/facts/{row.id}/purge", {}, format="json").status_code == 404
    assert client.get(f"{BASE}/entities/{api.id}?hops=3").status_code == 400


def test_entity_detail_returns_neighbourhood_with_private_and_system(owner, personal, web_client):
    api = entity(personal, "api")
    other = entity(personal, "db")
    normal = fact(personal, api, obj=other)
    private = fact(personal, api, MemoryFact.Predicate.USES, obj=other, sensitivity="private")
    body = web_client(owner).get(f"{BASE}/entities/{api.id}").json()
    assert {e["id"] for e in body["edges"]} == {str(normal.id), str(private.id)}
    assert body["entity"]["is_private"] is False


def test_create_then_supersede_then_read_as_of(owner, personal, web_client):
    client = web_client(owner)
    created = client.post(
        f"{BASE}/facts",
        {
            "workspace": str(personal.id),
            "subject_kind": "firm",
            "subject_name": "Acme",
            "predicate": "deployed_to",
            "object_kind": "service",
            "object_name": "payments",
            "valid_from": T0.isoformat(),
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    fid = created.json()["id"]
    version = client.post(
        f"{BASE}/facts/{fid}/supersede",
        {"valid_from": T1.isoformat(), "object_kind": "service", "object_name": "billing"},
        format="json",
    ).json()
    subject = created.json()["subject"]["id"]
    then = client.get(f"{BASE}/entities/{subject}/facts", {"as_of": T0.isoformat()}).json()
    now = client.get(f"{BASE}/entities/{subject}/facts", {"as_of": T1.isoformat()}).json()
    assert [f["id"] for f in then] == [fid]
    assert [f["id"] for f in now] == [version["id"]]
    timeline = client.get(f"{BASE}/entities/{subject}/facts").json()
    assert [f["id"] for f in timeline] == [fid, version["id"]]
    current = client.get(f"{BASE}/facts?workspace={personal.id}&current=true").json()
    assert [f["id"] for f in current] == [version["id"]]


def test_audit_rows_hold_ids_and_counts_only(owner, personal, web_client):
    client = web_client(owner)
    client.post(
        f"{BASE}/facts",
        {
            "workspace": str(personal.id),
            "subject_kind": "service",
            "subject_name": "Gizli Servis",
            "predicate": "uses",
            "object_text": "çok gizli değer",
            "statement": "Gizli Servis çok gizli değer kullanıyor",
        },
        format="json",
    )
    client.get(f"{BASE}/facts?workspace={personal.id}")
    client.get(f"{BASE}/entities?workspace={personal.id}")
    logs = AuditLog.objects.filter(action__startswith="memory.graph.")
    assert logs.count() == 3
    for log in logs:
        text = str(log.metadata)
        assert "Gizli" not in text and "gizli" not in text


def test_isolation_firm_actor_never_sees_personal_facts_and_reverse(owner, personal, firm):
    personal_fact = fact(personal, entity(personal, "api"))
    firm_fact = fact(firm, entity(firm, "api"))
    member = register_user(email="firm-member@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    seen = set(MemoryFact.objects.for_actor(member).values_list("pk", flat=True))
    assert seen == {firm_fact.pk}
    assert not MemoryEntity.objects.for_actor(member).filter(workspace=personal).exists()
    # the owner, scoped to the personal workspace, never sees the firm row
    scoped = MemoryFact.objects.for_actor(owner, workspace=personal)
    assert set(scoped.values_list("pk", flat=True)) == {personal_fact.pk}
    scoped_firm = MemoryFact.objects.for_actor(owner, workspace=firm)
    assert set(scoped_firm.values_list("pk", flat=True)) == {firm_fact.pk}


def test_non_owner_member_of_a_firm_is_403_on_its_graph(owner, firm, web_client):
    member = register_user(email="m2@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    response = web_client(member).get(f"{BASE}/facts?workspace={firm.id}")
    assert response.status_code == 403
