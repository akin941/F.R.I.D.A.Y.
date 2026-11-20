"""Phase 8A branch 5 (brief D11), in `tools/tests` because it drives the
`purge_trash` tool and the vault export (memory may not import tools): the
forget-source cascade to derived
memories and proposals, its exact restore, the gate-class `purge_trash`
extension and the vault mirror dropping archived rows."""

import uuid

import pytest
from django.utils import timezone

from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition as ApprovalInvalid
from approvals.services import create_request
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from memory.ingestion import services
from memory.models import (
    CoreBlockProposal,
    Episode,
    EpisodeExtractionJob,
    IngestionSource,
    Memory,
    compute_fingerprint,
)
from memory.tests.learning_helpers import enable, episode, map_repo
from tools import export

pytestmark = pytest.mark.django_db

Kind = IngestionSource.Kind


@pytest.fixture
def owner(db):
    from accounts.services import register_user
    from memory.tests.ingestion_helpers import PASSWORD

    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    from workspaces.models import Workspace

    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def sources(owner, personal):
    services.ensure_default_sources(workspace=personal, created_by=owner)
    return {s.kind: s for s in IngestionSource.objects.filter(workspace=personal)}


@pytest.fixture
def world(sources, personal, firm):
    enable(sources, Kind.GIT, Kind.HARNESS)
    map_repo(sources, "r1", personal, kinds=(Kind.GIT, Kind.HARNESS))
    map_repo(sources, "f1", firm, kinds=(Kind.GIT, Kind.HARNESS))
    git, harness = sources[Kind.GIT], sources[Kind.HARNESS]
    rows = {
        "git_approved": _learned(personal, git, "Git approved", Memory.Status.APPROVED),
        "git_pending": _learned(personal, git, "Git pending", Memory.Status.PENDING_APPROVAL),
        "git_rejected": _learned(personal, git, "Git rejected", Memory.Status.REJECTED),
        "git_firm": _learned(firm, git, "Git firm approved", Memory.Status.APPROVED),
        "harness_approved": _learned(personal, harness, "Harness", Memory.Status.APPROVED),
        "manual": Memory.objects.create(
            workspace=personal,
            memory_type="semantic",
            status=Memory.Status.APPROVED,
            source=Memory.Source.USER_CREATED,
            title="Manual",
            content="Manual content.",
            fingerprint=compute_fingerprint("Manual content."),
        ),
    }
    proposal = CoreBlockProposal.objects.create(
        workspace=personal,
        label="current_focus",
        proposed_content="Focus",
        provenance_quote="the owner's own words",
        ingestion_source=git,
    )
    other_proposal = CoreBlockProposal.objects.create(
        workspace=personal,
        label="active_projects",
        proposed_content="Projects",
        provenance_quote="the owner's own words",
        ingestion_source=harness,
    )
    episode(git, owner_text="Owner text of a personal commit message")
    episode(git, owner_text="Owner text of a firm commit message", repo="f1")
    episode(harness, owner_text="Owner goal for a harness job", kind="harness.discovery")
    return {
        "git": git,
        "harness": harness,
        "rows": rows,
        "proposal": proposal,
        "other_proposal": other_proposal,
    }


def _learned(workspace, source, title, status):
    return Memory.objects.create(
        workspace=workspace,
        memory_type="semantic",
        status=status,
        source=Memory.Source.AI_SUGGESTED,
        title=title,
        content=f"{title} content.",
        fingerprint=compute_fingerprint(f"{title} content."),
        ingestion_source=source,
        source_episode_id=uuid.uuid4(),
        provenance_quote="the owner's own words",
    )


def _snapshot():
    memories = {
        m.id: (m.status, m.title, m.content, m.provenance_quote, m.ingestion_source_id)
        for m in Memory.objects.all()
    }
    proposals = {p.id: p.status for p in CoreBlockProposal.objects.all()}
    episodes = {e.id: (e.deleted_at is None, e.owner_text) for e in Episode.all_objects.all()}
    return memories, proposals, episodes


def test_forget_archives_exactly_the_sources_memories_and_rejects_its_proposals(owner, world):
    rows = world["rows"]
    result = services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    assert result["memories_archived"] == 2  # personal + firm approved
    assert result["memories_rejected"] == 1
    assert result["core_block_proposals"] == 1
    assert result["episodes"] == 2

    def status(key):
        return Memory.objects.get(pk=rows[key].pk).status

    assert status("git_approved") == status("git_firm") == Memory.Status.ARCHIVED
    assert status("git_pending") == Memory.Status.REJECTED
    assert status("git_rejected") == Memory.Status.REJECTED  # untouched, unmarked
    assert Memory.objects.get(pk=rows["git_rejected"].pk).forget_operation_id is None
    assert status("harness_approved") == status("manual") == Memory.Status.APPROVED
    world["proposal"].refresh_from_db()
    world["other_proposal"].refresh_from_db()
    assert world["proposal"].status == CoreBlockProposal.Status.REJECTED
    assert world["other_proposal"].status == CoreBlockProposal.Status.PENDING
    # Archived rows leave cloud-eligible context.
    assert not Memory.objects.cloud_eligible().filter(ingestion_source=world["git"]).exists()
    audit = AuditLog.objects.get(action="memory.source_forgotten")
    assert set(audit.metadata) == {
        "kind",
        "operation_id",
        "episodes",
        "memories_archived",
        "memories_rejected",
        "core_block_proposals",
        "candidates_discarded",
    }


def test_full_forget_restore_round_trip_leaves_the_db_as_before(owner, world):
    before = _snapshot()
    services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    assert _snapshot() != before
    result = services.restore_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    assert result["memories"] == 3 and result["core_block_proposals"] == 1
    assert _snapshot() == before
    assert not Memory.objects.filter(forget_operation_id__isnull=False).exists()
    assert not CoreBlockProposal.objects.filter(forget_operation_id__isnull=False).exists()


def test_restore_leaves_a_privacy_locked_memory_archived(owner, world):
    services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    Memory.objects.filter(pk=world["rows"]["git_approved"].pk).update(privacy_locked=True)
    services.restore_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    assert Memory.objects.get(pk=world["rows"]["git_approved"].pk).status == Memory.Status.ARCHIVED
    assert Memory.objects.get(pk=world["rows"]["git_firm"].pk).status == Memory.Status.APPROVED


def test_forget_strips_waiting_candidates_of_that_source_only(owner, world, personal):
    git, harness = world["git"], world["harness"]
    job = EpisodeExtractionJob.objects.create(
        workspace=personal,
        night=timezone.localdate(),
        status=EpisodeExtractionJob.Status.SUCCEEDED,
        candidates=[{"source_id": str(git.id)}, {"source_id": str(harness.id)}],
    )
    result = services.forget_source(git, actor=owner, channel=CHANNEL_WEB)
    assert result["candidates_discarded"] == 1
    job.refresh_from_db()
    assert job.candidates == [{"source_id": str(harness.id)}]


@pytest.mark.parametrize("channel", [CHANNEL_CLI, "openai", "telegram"])
def test_forget_and_restore_require_the_web_channel(owner, world, channel):
    with pytest.raises(services.IngestionPermissionDenied):
        services.forget_source(world["git"], actor=owner, channel=channel)
    assert Memory.objects.get(pk=world["rows"]["git_approved"].pk).status == "approved"


def test_forget_preview_names_the_cascade_counts(world):
    assert services.forget_preview(world["git"]) == {
        "episodes": 2,
        "memories": 3,
        "core_block_proposals": 1,
    }


def test_approval_and_rejection_feed_the_acceptance_counters(owner, world):
    from memory.models import IngestionCounter
    from memory.services import reject_memory

    reject_memory(memory=world["rows"]["git_pending"], actor=owner)
    counter = IngestionCounter.objects.get(source=world["git"], day=timezone.localdate())
    assert counter.proposals_rejected == 1


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


def test_forgotten_rows_are_purged_only_by_an_approved_request(owner, world, personal, web_client):
    services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    trashed = list(
        Episode.all_objects.filter(workspace=personal, deleted_at__isnull=False).values_list(
            "pk", flat=True
        )
    )
    forgotten = [world["rows"]["git_approved"].pk, world["rows"]["git_pending"].pk]
    request = _purge_request(
        owner,
        personal,
        {
            "episode_ids": [str(pk) for pk in trashed],
            "forgotten_memory_ids": [str(pk) for pk in forgotten],
        },
    )
    from tools.purge import describe_purge_trash_request

    summary = describe_purge_trash_request(request)
    assert summary["episode_count"] == 1 and summary["forgotten_memory_count"] == 2
    # Requesting purged nothing.
    assert Episode.all_objects.filter(pk__in=trashed).count() == 1
    assert Memory.objects.filter(pk__in=forgotten).count() == 2

    response = web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 200, response.content
    assert not Episode.all_objects.filter(pk__in=trashed).exists()
    assert not Memory.objects.filter(pk__in=forgotten).exists()
    logs = AuditLog.objects.filter(action__in=["memory.episode_purged", "memory.purged"])
    assert logs.count() == 3
    assert {log.metadata["approval_request_id"] for log in logs} == {str(request.id)}
    # The firm workspace's forgotten row is out of this request's scope.
    assert Memory.objects.filter(pk=world["rows"]["git_firm"].pk).exists()


@pytest.mark.parametrize("key", ["episode_ids", "forgotten_memory_ids"])
def test_purge_refuses_live_rows(owner, world, personal, key):
    if key == "episode_ids":
        ids = [str(Episode.objects.filter(workspace=personal).first().pk)]
    else:
        ids = [str(world["rows"]["git_approved"].pk)]  # approved, not forgotten
    with pytest.raises(ApprovalInvalid):
        _purge_request(owner, personal, {key: ids})


def test_a_source_restored_before_the_decision_survives_the_purge(
    owner, world, personal, web_client
):
    services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    pk = world["rows"]["git_approved"].pk
    request = _purge_request(owner, personal, {"forgotten_memory_ids": [str(pk)]})
    services.restore_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert Memory.objects.get(pk=pk).status == Memory.Status.APPROVED


# --- the vault mirror -----------------------------------------------------------


def test_previously_exported_notes_of_forgotten_rows_are_removed(owner, world, personal, tmp_path):
    vault = export.validate_vault_root_exists(str(tmp_path))
    export.export_workspace_memories(vault, personal)
    memory = world["rows"]["git_approved"]
    note = tmp_path / "03_Workspaces" / str(personal.id) / "Memory" / f"{memory.id}.md"
    assert note.exists()
    services.forget_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    written, updated, removed = export.export_workspace_memories(vault, personal)
    assert not note.exists() and removed == 1
    services.restore_source(world["git"], actor=owner, channel=CHANNEL_WEB)
    export.export_workspace_memories(vault, personal)
    assert note.exists()
