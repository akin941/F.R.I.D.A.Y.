"""Phase 8B branch 2 (brief D3, D4): fact proposals on the morning card --
role x endpoint for the fact actions, the "supersedes" line from the FK,
approval through the digest closing the target's window, the "has changed"
state (409), per-item only (bulk refused), private facts owner-only."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.channels import CHANNEL_WEB
from memory.graph import services as graph_services
from memory.ingestion import digest
from memory.models import IngestionSource, MemoryFact
from memory.services import InvalidTransition
from memory.tests.graph_helpers import entity, fact
from memory.tests.test_learning_digest import PRINCIPALS, _client

pytestmark = pytest.mark.django_db

BASE = "/api/v1/memory/learning"
QUOTE = "we moved the billing api to hetzner after the outage"


@pytest.fixture
def git(sources):
    return sources[IngestionSource.Kind.GIT]


@pytest.fixture
def target(personal):
    subject = entity(personal, "billing api")
    return fact(
        personal,
        subject,
        obj=entity(personal, "AWS"),
        valid_from=timezone.now() - timedelta(days=30),
    )


def _proposal(workspace, git, subject, *, supersedes=None, sensitivity="normal", obj=None):
    row = fact(
        workspace,
        subject,
        obj=obj or entity(workspace, f"hetzner-{uuid.uuid4().hex[:6]}"),
        valid_from=timezone.now() - timedelta(days=1),
        status=MemoryFact.Status.PENDING_APPROVAL,
        source=git,
        supersedes=supersedes,
        sensitivity=sensitivity,
        quote=QUOTE,
    )
    row.source_episode_id = uuid.uuid4()
    row.save()
    return row


@pytest.fixture
def items(personal, git, target):
    return {
        "fact": _proposal(personal, git, target.subject, supersedes=target),
        "plain": _proposal(personal, git, target.subject),
    }


ENDPOINTS = {
    "approve-fact": lambda i: ("post", f"{BASE}/proposals/fact/{i['fact'].id}/approve", {}, 200),
    "edit-fact": lambda i: (
        "post",
        f"{BASE}/proposals/fact/{i['plain'].id}/edit",
        {"statement": "Billing api Hetzner'da."},
        200,
    ),
    "reject-fact": lambda i: ("post", f"{BASE}/proposals/fact/{i['fact'].id}/reject", {}, 200),
}


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_fact_action_role_matrix(
    owner, personal, items, target, web_client, endpoint, principal, expected
):
    method, url, body, success = ENDPOINTS[endpoint](items)
    client = _client(principal, owner, personal, web_client)
    response = getattr(client, method)(url, body, format="json")
    assert response.status_code == (success if expected == "ok" else expected), response.content
    if expected != "ok":
        for row in (items["fact"], items["plain"]):
            assert MemoryFact.all_objects.get(pk=row.pk).status == "pending_approval"
        target.refresh_from_db()
        assert target.valid_to is None


def test_the_digest_lists_fact_proposals_with_the_supersedes_line(
    owner, personal, items, target, web_client
):
    body = web_client(owner).get(f"{BASE}/digest").json()
    facts = [i for g in body["workspaces"] for i in g["items"] if i["kind"] == "fact"]
    assert {i["id"] for i in facts} == {str(items["fact"].id), str(items["plain"].id)}
    item = next(i for i in facts if i["id"] == str(items["fact"].id))
    assert item["statement"] == items["fact"].statement and item["quote"] == QUOTE
    assert item["valid_from"] and item["valid_to"] is None
    assert item["subject"] == {"kind": "service", "name": "billing api"}
    assert item["supersedes"] == {
        "id": str(target.id),
        "statement": target.statement,
        "valid_from": target.valid_from.isoformat(),
        "changed": False,
    }
    assert item["actions"] == ["approve", "edit", "reject"]
    assert item["bulk_approvable"] is False and item["source_kind"] == "git"
    plain = next(i for i in facts if i["id"] == str(items["plain"].id))
    assert plain["supersedes"] is None


def test_approving_through_the_digest_supersedes_atomically(owner, items, target, web_client):
    response = web_client(owner).post(f"{BASE}/proposals/fact/{items['fact'].id}/approve", {})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    proposal = MemoryFact.objects.get(pk=items["fact"].pk)
    target.refresh_from_db()
    assert proposal.status == "approved" and proposal.supersedes_id is None
    assert target.valid_to == proposal.valid_from and target.superseded_by_id == proposal.id


def test_rejecting_changes_nothing_on_the_target(owner, items, target, web_client):
    response = web_client(owner).post(f"{BASE}/proposals/fact/{items['fact'].id}/reject", {})
    assert response.status_code == 200
    target.refresh_from_db()
    assert target.valid_to is None and target.superseded_by_id is None
    proposal = MemoryFact.all_objects.get(pk=items["fact"].pk)
    assert proposal.status == "rejected" and proposal.supersedes_id is None


@pytest.mark.parametrize("change", ["trash", "supersede"])
def test_a_changed_target_is_409_and_shows_as_changed(
    owner, personal, items, target, web_client, change
):
    if change == "trash":
        graph_services.trash_fact(fact=target, actor=owner, channel=CHANNEL_WEB)
    else:
        graph_services.supersede_fact(
            fact=target,
            actor=owner,
            channel=CHANNEL_WEB,
            valid_from=timezone.now(),
            object_text="gcp",
        )
    client = web_client(owner)
    response = client.post(f"{BASE}/proposals/fact/{items['fact'].id}/approve", {})
    assert response.status_code == 409
    assert response.json()["code"] == "memory.graph.supersede_target_changed"
    proposal = MemoryFact.objects.get(pk=items["fact"].pk)
    assert proposal.status == "pending_approval" and proposal.supersedes_id == target.id
    body = client.get(f"{BASE}/digest").json()
    item = next(
        i for g in body["workspaces"] for i in g["items"] if i["id"] == str(items["fact"].id)
    )
    assert item["supersedes"]["changed"] is True
    assert item["actions"] == ["edit", "reject"]


def test_an_earlier_valid_from_than_the_target_is_409_supersede_order(
    owner, personal, git, target, web_client
):
    proposal = _proposal(personal, git, target.subject, supersedes=target)
    MemoryFact.all_objects.filter(pk=proposal.pk).update(
        valid_from=target.valid_from - timedelta(days=1)
    )
    response = web_client(owner).post(f"{BASE}/proposals/fact/{proposal.id}/approve", {})
    assert response.status_code == 409
    assert response.json()["code"] == "memory.graph.supersede_order"


def test_bulk_approval_is_refused_for_facts(owner, items, web_client):
    response = web_client(owner).post(
        f"{BASE}/bulk-approve", {"ids": [str(items["plain"].id)]}, format="json"
    )
    assert response.status_code == 400
    assert MemoryFact.objects.get(pk=items["plain"].pk).status == "pending_approval"
    with pytest.raises(InvalidTransition):
        digest.bulk_approve(actor=owner, channel=CHANNEL_WEB, memory_ids=[items["plain"].id])


def test_edit_through_the_digest_creates_the_owners_version(owner, items, web_client):
    response = web_client(owner).post(
        f"{BASE}/proposals/fact/{items['plain'].id}/edit",
        {"statement": "Billing api Hetzner'da."},
        format="json",
    )
    assert response.status_code == 200
    version = MemoryFact.objects.get(pk=response.json()["id"])
    assert version.status == "approved" and version.statement == "Billing api Hetzner'da."
    assert MemoryFact.all_objects.get(pk=items["plain"].pk).status == "rejected"


def test_private_facts_are_listed_to_the_owner_web_session_only(
    owner, personal, git, target, web_client
):
    private = _proposal(personal, git, target.subject, sensitivity="private")
    body = web_client(owner).get(f"{BASE}/digest").json()
    item = next(i for g in body["workspaces"] for i in g["items"] if i["id"] == str(private.id))
    assert item["sensitivity"] == "private"
    for principal, expected in PRINCIPALS[1:]:
        client = _client(principal, owner, personal, web_client)
        assert client.get(f"{BASE}/digest").status_code == expected


def test_another_workspaces_or_graphify_fact_is_404(owner, personal, git, web_client):
    from accounts.services import register_user
    from memory.tests.ingestion_helpers import PASSWORD
    from workspaces.models import Workspace

    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    theirs = Workspace.objects.get(owner=stranger)
    foreign = _proposal(theirs, git, entity(theirs, "billing api"))
    client = web_client(owner)
    assert client.post(f"{BASE}/proposals/fact/{foreign.id}/approve", {}).status_code == 404
    owner_typed = fact(personal, entity(personal, "web app"), text="vercel")
    assert client.post(f"{BASE}/proposals/fact/{owner_typed.id}/approve", {}).status_code == 404
    assert client.post(f"{BASE}/proposals/fact/{uuid.uuid4()}/approve", {}).status_code == 404


def test_fact_decisions_refuse_non_web_channels_in_the_service(owner, items):
    from memory.services import MemoryPermissionDenied

    for channel in ("cli", "openai", "telegram", "hook", "voice"):
        with pytest.raises(MemoryPermissionDenied):
            digest.decide(
                actor=owner,
                channel=channel,
                kind=digest.KIND_FACT,
                item=items["fact"],
                action="approve",
            )
