"""Phase 8B branch 1 (brief D7), in `tools/tests` because it drives the
gate-class `purge_trash` tool (memory may not import tools): forget-source
reaches facts and entities, restore brings back exactly those, and purge of
facts/entities happens only through an approved request."""

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition as ApprovalInvalid
from approvals.services import create_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.graph import services as graph
from memory.ingestion import services
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, MemoryEntity, MemoryFact
from memory.tests.graph_helpers import T0, T1, T2, entity, fact, refreshed, system_fact
from memory.tests.ingestion_helpers import PASSWORD
from tools.purge import describe_purge_trash_request
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

Status = MemoryFact.Status
WEB = CHANNEL_WEB


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def world(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    git = IngestionSource.objects.get(workspace=personal, kind="git")
    hooks = IngestionSource.objects.get(workspace=personal, kind="claude_code_hooks")
    api = entity(personal, "api")
    shared = entity(personal, "hetzner", kind=MemoryEntity.Kind.TOOL)
    lonely = entity(personal, "only-git", kind=MemoryEntity.Kind.TOOL)
    # the other source's current fact, targeted by a git proposal
    other_current = fact(personal, api, obj=shared, valid_from=T0, source=hooks)
    rows = {
        "approved": fact(personal, api, MemoryFact.Predicate.USES, obj=lonely, source=git),
        "pending": fact(
            personal,
            api,
            obj=shared,
            valid_from=T1,
            status="pending_approval",
            source=git,
            supersedes=other_current,
        ),
        "other_current": other_current,
        "system": system_fact(
            personal,
            entity(personal, "mod", kind="code_module", origin="graphify"),
            entity(personal, "mod2", kind="code_module", origin="graphify"),
        ),
    }
    return {"git": git, "hooks": hooks, "rows": rows, "lonely": lonely, "shared": shared}


def test_forget_archives_and_rejects_exactly_the_sources_facts(owner, world):
    result = services.forget_source(world["git"], actor=owner, channel=WEB)
    assert result["facts_archived"] == 1 and result["facts_rejected"] == 1
    assert result["entities_trashed"] == 1
    rows = world["rows"]
    approved, pending, other, system = refreshed(
        rows["approved"], rows["pending"], rows["other_current"], rows["system"]
    )
    assert approved.status == Status.ARCHIVED and approved.forget_prior_status == "approved"
    assert pending.status == Status.REJECTED and pending.supersedes_id is None
    assert other.status == Status.APPROVED and other.valid_to is None  # target stays current
    assert system.status == Status.SYSTEM and system.forget_operation_id is None
    # the entity left with no live fact is trashed; the shared one is not
    assert not MemoryEntity.objects.filter(pk=world["lonely"].pk).exists()
    assert MemoryEntity.objects.filter(pk=world["shared"].pk).exists()
    assert services.forget_preview(world["git"])["facts"] == 0


def test_restore_brings_back_exactly_those_rows_without_the_link(owner, world):
    rejected_before = fact(
        world["rows"]["approved"].workspace,
        world["rows"]["approved"].subject,
        MemoryFact.Predicate.WORKS_ON,
        status="rejected",
        source=world["git"],
    )
    services.forget_source(world["git"], actor=owner, channel=WEB)
    result = services.restore_source(world["git"], actor=owner, channel=WEB)
    assert result["facts"] == 2 and result["entities"] == 1
    approved, pending, rejected_before = refreshed(
        world["rows"]["approved"], world["rows"]["pending"], rejected_before
    )
    assert approved.status == Status.APPROVED and approved.forget_operation_id is None
    assert pending.status == Status.PENDING_APPROVAL and pending.supersedes_id is None
    assert rejected_before.status == Status.REJECTED  # not this operation's row
    assert MemoryEntity.objects.filter(pk=world["lonely"].pk).exists()


def test_forgotten_target_makes_the_other_sources_proposal_conflict(owner, personal, world):
    api = world["rows"]["approved"].subject
    hooks_proposal = fact(
        personal,
        api,
        MemoryFact.Predicate.USES,
        text="aws",
        valid_from=T2,
        status="pending_approval",
        source=world["hooks"],
        supersedes=world["rows"]["approved"],
    )
    services.forget_source(world["git"], actor=owner, channel=WEB)
    with pytest.raises(graph.SupersedeTargetChanged):
        graph.approve_fact(fact=hooks_proposal, actor=owner, channel=WEB)
    hooks_proposal.refresh_from_db()
    assert hooks_proposal.status == Status.PENDING_APPROVAL


def test_forget_audit_has_counts_only(owner, world):
    services.forget_source(world["git"], actor=owner, channel=WEB)
    log = AuditLog.objects.get(action="memory.source_forgotten")
    assert log.metadata["facts_archived"] == 1 and log.metadata["entities_trashed"] == 1
    # ids, kinds and counts only: no entity name or statement text
    assert "only-git" not in str(log.metadata) and "hetzner" not in str(log.metadata)


# --- purge_trash (gate-class) --------------------------------------------------


def _purge_request(owner, workspace, payload):
    return create_request(
        workspace=workspace,
        actor=owner,
        tool_name="purge_trash",
        payload=payload,
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )


def test_facts_and_entities_are_purged_only_by_an_approved_request(
    owner, personal, world, web_client
):
    services.forget_source(world["git"], actor=owner, channel=WEB)
    fact_ids = [str(world["rows"]["approved"].pk), str(world["rows"]["pending"].pk)]
    request = _purge_request(
        owner, personal, {"fact_ids": fact_ids, "entity_ids": [str(world["lonely"].pk)]}
    )
    summary = describe_purge_trash_request(request)
    assert summary["fact_count"] == 2 and summary["entity_count"] == 1
    # requesting purged nothing
    assert MemoryFact.all_objects.filter(pk__in=fact_ids).count() == 2
    response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200, response.content
    assert not MemoryFact.all_objects.filter(pk__in=fact_ids).exists()
    assert not MemoryEntity.all_objects.filter(pk=world["lonely"].pk).exists()
    logs = AuditLog.objects.filter(
        action__in=["memory.graph.fact_purged", "memory.graph.entity_purged"]
    )
    assert logs.count() == 3
    assert {log.metadata["approval_request_id"] for log in logs} == {str(request.id)}
    # Graphify rows and the other source's fact survive
    assert MemoryFact.all_objects.filter(pk=world["rows"]["system"].pk).exists()
    assert MemoryFact.objects.filter(pk=world["rows"]["other_current"].pk).exists()


def test_purge_of_a_fact_a_pending_proposal_supersedes_rejects_it_first(
    owner, personal, web_client
):
    api = entity(personal, "api")
    target = fact(personal, api, valid_from=T0)
    proposal = fact(
        personal, api, text="aws", valid_from=T1, status="pending_approval", supersedes=target
    )
    graph.trash_fact(fact=target, actor=owner, channel=WEB)
    request = _purge_request(owner, personal, {"fact_ids": [str(target.pk)]})
    response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200, response.content
    assert not MemoryFact.all_objects.filter(pk=target.pk).exists()
    proposal.refresh_from_db()
    assert proposal.status == Status.REJECTED and proposal.supersedes_id is None


@pytest.mark.parametrize("key", ["fact_ids", "entity_ids"])
def test_purge_refuses_live_and_system_rows(owner, personal, world, key):
    if key == "fact_ids":
        candidates = [world["rows"]["approved"], world["rows"]["system"]]
    else:
        candidates = [world["shared"], world["rows"]["system"].subject]
    for row in candidates:
        with pytest.raises(ApprovalInvalid):
            _purge_request(owner, personal, {key: [str(row.pk)]})


def test_a_source_restored_before_the_decision_survives_the_purge(
    owner, personal, world, web_client
):
    services.forget_source(world["git"], actor=owner, channel=WEB)
    pk = world["rows"]["approved"].pk
    request = _purge_request(owner, personal, {"fact_ids": [str(pk)]})
    services.restore_source(world["git"], actor=owner, channel=WEB)
    web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert MemoryFact.objects.get(pk=pk).status == Status.APPROVED


def test_entity_revived_before_the_decision_survives_the_purge(owner, personal, world, web_client):
    services.forget_source(world["git"], actor=owner, channel=WEB)
    lonely = world["lonely"]
    request = _purge_request(owner, personal, {"entity_ids": [str(lonely.pk)]})
    # the owner names it again: resolution revives it with a live fact
    graph.create_fact(
        workspace=personal,
        actor=owner,
        channel=WEB,
        subject_kind="tool",
        subject_name="only-git",
        predicate="has_status",
        object_text="kept",
    )
    web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert MemoryEntity.objects.filter(pk=lonely.pk).exists()
