"""Phase 8A branch 5 (brief D10): the morning card API -- role x endpoint
against the five Phase 4 principals (plus a hook PAT), per-item decisions,
bulk approval only for work_log, stale core-block proposals, isolation."""

import secrets
import uuid

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.ingestion import digest
from memory.models import (
    CoreBlockProposal,
    CoreMemoryBlock,
    IngestionCounter,
    IngestionSource,
    Memory,
    compute_fingerprint,
)
from memory.services import InvalidTransition, MemoryPermissionDenied, update_core_block
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

BASE = "/api/v1/memory/learning"


def _learned(workspace, source, title, *, memory_type="semantic"):
    return Memory.objects.create(
        workspace=workspace,
        memory_type=memory_type,
        status=Memory.Status.PENDING_APPROVAL,
        source=Memory.Source.AI_SUGGESTED,
        title=title,
        content=f"{title} content.",
        fingerprint=compute_fingerprint(f"{title} content."),
        ingestion_source=source,
        source_episode_id=uuid.uuid4(),
        provenance_quote=f"quote for {title}",
    )


def _proposal(workspace, source, label="current_focus", base_version=0):
    return CoreBlockProposal.objects.create(
        workspace=workspace,
        label=label,
        proposed_content=f"Proposed {label}",
        base_version=base_version,
        provenance_quote=f"quote for {label}",
        ingestion_source=source,
    )


@pytest.fixture
def git(sources):
    return sources[IngestionSource.Kind.GIT]


@pytest.fixture
def items(personal, firm, git):
    return {
        "memory": _learned(personal, git, "Personal fact"),
        "firm_memory": _learned(firm, git, "Firm fact"),
        "work_log": _learned(personal, git, "Work log", memory_type="work_log"),
        "proposal": _proposal(personal, git),
    }


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
        return web_client(register_user(email="member@test.dev", password=PASSWORD))
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

ENDPOINTS = {
    "digest": lambda i: ("get", f"{BASE}/digest", None, 200),
    "approve-memory": lambda i: (
        "post",
        f"{BASE}/proposals/memory/{i['memory'].id}/approve",
        {},
        200,
    ),
    "edit-memory": lambda i: (
        "post",
        f"{BASE}/proposals/memory/{i['memory'].id}/edit",
        {"title": "Edited"},
        200,
    ),
    "reject-memory": lambda i: (
        "post",
        f"{BASE}/proposals/memory/{i['memory'].id}/reject",
        {},
        200,
    ),
    "approve-core-block": lambda i: (
        "post",
        f"{BASE}/proposals/core-block/{i['proposal'].id}/approve",
        {},
        200,
    ),
    "reject-core-block": lambda i: (
        "post",
        f"{BASE}/proposals/core-block/{i['proposal'].id}/reject",
        {},
        200,
    ),
    "bulk-approve": lambda i: (
        "post",
        f"{BASE}/bulk-approve",
        {"ids": [str(i["work_log"].id)]},
        200,
    ),
}


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_role_matrix(owner, personal, items, web_client, endpoint, principal, expected):
    method, url, body, success = ENDPOINTS[endpoint](items)
    client = _client(principal, owner, personal, web_client)
    response = getattr(client, method)(url, body, format="json")
    assert response.status_code == (success if expected == "ok" else expected), response.content
    if expected != "ok":
        assert Memory.objects.get(pk=items["memory"].pk).status == Memory.Status.PENDING_APPROVAL
        assert Memory.objects.get(pk=items["work_log"].pk).status == Memory.Status.PENDING_APPROVAL
        assert CoreBlockProposal.objects.get(pk=items["proposal"].pk).status == "pending"


def test_digest_groups_by_workspace_and_always_carries_the_quote(
    owner, personal, firm, items, web_client
):
    Memory.objects.create(
        workspace=personal,
        memory_type="semantic",
        status=Memory.Status.PENDING_APPROVAL,
        source=Memory.Source.AI_SUGGESTED,
        title="Chat suggestion, not learned",
        content="Chat content.",
        fingerprint=compute_fingerprint("Chat content."),
    )
    body = web_client(owner).get(f"{BASE}/digest").json()
    assert body["count"] == 4
    groups = {g["workspace"]["id"]: g["items"] for g in body["workspaces"]}
    assert {i["title"] for i in groups[str(personal.id)]} == {
        "Personal fact",
        "Work log",
        "current_focus",
    }
    assert [i["title"] for i in groups[str(firm.id)]] == ["Firm fact"]
    for group in groups.values():
        for item in group:
            assert item["quote"].startswith("quote for")
            assert item["source_kind"] == "git"
            assert item["bulk_approvable"] == (item.get("memory_type") == "work_log")
    proposal = next(i for i in groups[str(personal.id)] if i["kind"] == "core-block")
    assert proposal["actions"] == ["approve", "reject"] and proposal["bulk_approvable"] is False
    audit = AuditLog.objects.get(action="memory.learning.digest_viewed")
    assert audit.metadata["count"] == 4


def test_digest_never_lists_another_owners_items(owner, items, web_client, git):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    stranger.is_owner = False
    stranger.save()
    theirs = Workspace.objects.get(owner=stranger)
    _learned(theirs, git, "Stranger fact")
    body = web_client(owner).get(f"{BASE}/digest").json()
    titles = {i["title"] for g in body["workspaces"] for i in g["items"]}
    assert "Stranger fact" not in titles


def test_another_workspaces_item_is_404(owner, items, web_client, git):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    theirs = _learned(Workspace.objects.get(owner=stranger), git, "Stranger fact")
    response = web_client(owner).post(f"{BASE}/proposals/memory/{theirs.id}/approve", {})
    assert response.status_code == 404
    unknown = web_client(owner).post(f"{BASE}/proposals/memory/{uuid.uuid4()}/approve", {})
    assert unknown.status_code == 404
    bogus = web_client(owner).post(f"{BASE}/proposals/memory/{items['memory'].id}/forget", {})
    assert bogus.status_code == 404
    edit_block = web_client(owner).post(
        f"{BASE}/proposals/core-block/{items['proposal'].id}/edit", {}
    )
    assert edit_block.status_code == 404


def test_approve_with_curation_and_acceptance_counter(owner, items, web_client, git):
    response = web_client(owner).post(
        f"{BASE}/proposals/memory/{items['memory'].id}/approve",
        {"sensitivity": "private", "title": "Curated"},
        format="json",
    )
    assert response.status_code == 200
    memory = Memory.objects.get(pk=items["memory"].pk)
    assert (memory.status, memory.sensitivity, memory.title) == ("approved", "private", "Curated")
    assert IngestionCounter.objects.get(source=git).proposals_accepted == 1


def test_bulk_approve_is_refused_for_non_work_log_items(owner, items, web_client):
    client = web_client(owner)
    mixed = client.post(
        f"{BASE}/bulk-approve",
        {"ids": [str(items["work_log"].id), str(items["memory"].id)]},
        format="json",
    )
    assert mixed.status_code == 403
    assert Memory.objects.get(pk=items["work_log"].pk).status == "pending_approval"  # nothing
    assert Memory.objects.get(pk=items["memory"].pk).status == "pending_approval"
    proposal_id = client.post(
        f"{BASE}/bulk-approve", {"ids": [str(items["proposal"].id)]}, format="json"
    )
    assert proposal_id.status_code == 400  # a core-block proposal is never bulk-approvable
    ok = client.post(f"{BASE}/bulk-approve", {"ids": [str(items["work_log"].id)]}, format="json")
    assert ok.status_code == 200 and ok.json()["approved"] == [str(items["work_log"].id)]


@pytest.mark.parametrize("label", ["developer", "preferences", "current_focus"])
def test_core_block_proposals_are_decided_one_at_a_time(owner, personal, git, web_client, label):
    proposal = _proposal(personal, git, label=label)
    with pytest.raises(InvalidTransition):  # not a memory: never bulk-approvable
        digest.bulk_approve(actor=owner, channel=CHANNEL_WEB, memory_ids=[proposal.id])
    preference = _learned(personal, git, f"{label} preference", memory_type="preference")
    with pytest.raises(MemoryPermissionDenied):
        digest.bulk_approve(actor=owner, channel=CHANNEL_WEB, memory_ids=[preference.id])
    response = web_client(owner).post(f"{BASE}/proposals/core-block/{proposal.id}/approve", {})
    assert response.status_code == 200
    block = CoreMemoryBlock.objects.get(workspace=personal, label=label)
    assert block.content == f"Proposed {label}" and block.updated_by_kind == "approved_proposal"


def test_a_stale_core_block_proposal_is_refused_at_approval(owner, personal, git, web_client):
    proposal = _proposal(personal, git, base_version=0)
    update_core_block(
        workspace=personal,
        label="current_focus",
        content="Owner moved on.",
        actor=owner,
        channel=CHANNEL_WEB,
    )
    response = web_client(owner).post(f"{BASE}/proposals/core-block/{proposal.id}/approve", {})
    assert response.status_code == 400
    assert response.json()["code"] == "memory.core_block.stale_version"
    proposal.refresh_from_db()
    assert proposal.status == CoreBlockProposal.Status.PENDING
    assert CoreMemoryBlock.objects.get(label="current_focus").content == "Owner moved on."


def test_decisions_refuse_non_web_channels_in_the_service(owner, items):
    for channel in ("cli", "openai", "telegram", "hook"):
        with pytest.raises(MemoryPermissionDenied):
            digest.decide(
                actor=owner,
                channel=channel,
                kind=digest.KIND_MEMORY,
                item=items["memory"],
                action="approve",
            )
