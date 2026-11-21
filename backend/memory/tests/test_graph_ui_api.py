"""Phase 8B branch 9: the read additions behind the `/memory/graph` page and
the ingestion audit view.

- The entity detail carries `fact_count` (what the trash confirmation names,
  matching what `trash_entity` moves) and, for a Graphify entity, its
  snapshot's sha (`code_graph`).
- The audit view of a transcript source carries one entry per cursor: the
  `since_clamped` count, the last skipped window (from the pull's own
  audit row) and `tie_overflow`; other sources carry none.
"""

from datetime import UTC, datetime

import pytest
from django.utils import timezone

from audit.models import AuditLog
from memory.graph import ports
from memory.ingestion.transcript_audit import CAPABILITY_BY_SOURCE_KIND, PULL_AUDIT_ACTION
from memory.models import IngestionSource, MemoryEntity, MemoryFact, TranscriptCursor
from memory.tests.graph_helpers import entity, fact

pytestmark = pytest.mark.django_db

BASE = "/api/v1/memory/graph"
SHA = "b" * 40
COMMITTED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def test_entity_detail_counts_the_facts_a_trash_would_move(owner, personal, web_client):
    api = entity(personal, "api")
    db = entity(personal, "db")
    fact(personal, api, obj=db)
    fact(personal, db, MemoryFact.Predicate.USES, obj=api)  # as object
    fact(personal, api, MemoryFact.Predicate.PREFERS, status="pending_approval")
    trashed = fact(personal, api, MemoryFact.Predicate.OWNED_BY)
    MemoryFact.all_objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())
    client = web_client(owner)

    body = client.get(f"{BASE}/entities/{api.id}").json()
    assert body["fact_count"] == 3
    assert body["code_graph"] is None

    result = client.delete(f"{BASE}/entities/{api.id}").json()
    assert result["facts"] == body["fact_count"]


def test_a_graphify_entity_carries_its_snapshot_sha(owner, personal, web_client):
    ports.import_code_graph(
        workspace=personal,
        repo_ref="42",
        commit_sha=SHA,
        committed_at=COMMITTED,
        graph={
            "entities": [
                {"key": "memory/models.py", "kind": "code_module", "name": "memory/models.py"},
                {"key": "memory/views.py", "kind": "code_module", "name": "memory/views.py"},
            ],
            "facts": [
                {"subject": "memory/views.py", "predicate": "imports", "object": "memory/models.py"}
            ],
        },
    )
    module = MemoryEntity.objects.get(workspace=personal, name="memory/views.py")
    body = web_client(owner).get(f"{BASE}/entities/{module.id}").json()
    assert body["entity"]["origin"] == "graphify"
    assert body["fact_count"] == 0  # nothing the owner could trash
    assert body["code_graph"]["commit_sha"] == SHA
    assert body["code_graph"]["entity_count"] == 2
    assert body["code_graph"]["fact_count"] == 1
    assert body["code_graph"]["retired_at"] is None
    assert len(body["edges"]) == 1


def _transcript_source(sources):
    source = sources[IngestionSource.Kind.CLAUDE_CODE_TRANSCRIPTS]
    source.enabled = True
    source.save()
    return source


def _pull_row(workspace, *, node, capability, window=None):
    metadata = {
        "system": True,
        "job": "transcript_pull",
        "capability": capability,
        "node": node,
        "since_clamped": window is not None,
    }
    if window is not None:
        metadata["skipped_window"] = window
    return AuditLog.objects.create(
        action=PULL_AUDIT_ACTION,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        metadata=metadata,
    )


def test_transcript_audit_view_shows_clamp_window_and_tie_counts(
    owner, personal, firm, sources, web_client
):
    source = _transcript_source(sources)
    capability = CAPABILITY_BY_SOURCE_KIND[source.kind]
    TranscriptCursor.objects.create(
        workspace=personal,
        source=source,
        target_workspace=firm,
        node_ref="node-1",
        since=datetime(2026, 11, 14, tzinfo=UTC),
        since_clamped=2,
        last_clamped_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        tie_overflow=1,
    )
    older = {"from": "2026-11-01T00:00:00.000Z", "to": "2026-11-12T09:05:00.000Z"}
    newest = {"from": "2026-11-02T00:00:00.000Z", "to": "2026-11-13T09:05:00.000Z"}
    _pull_row(firm, node="node-1", capability=capability, window=older)
    _pull_row(firm, node="node-1", capability=capability, window=newest)
    _pull_row(firm, node="node-1", capability=capability)  # a normal pull later
    # Another node's and the other tool's clamps are not this cursor's.
    _pull_row(firm, node="node-2", capability=capability, window={"from": "x", "to": "y"})
    _pull_row(
        firm,
        node="node-1",
        capability=CAPABILITY_BY_SOURCE_KIND[IngestionSource.Kind.CODEX_TRANSCRIPTS],
        window={"from": "x", "to": "y"},
    )

    body = web_client(owner).get(f"/api/v1/memory/ingestion/sources/{source.id}").json()
    assert body["rules_shared_with"] == "claude_code_hooks"
    (cursor,) = body["transcript_cursors"]
    assert cursor["target_workspace"] == str(firm.id)
    assert cursor["target_workspace_name"] == firm.name
    assert cursor["since_clamped"] == 2
    assert cursor["tie_overflow"] == 1
    assert cursor["last_skipped_window"] == newest
    assert cursor["last_clamped_at"].startswith("2026-11-20")


def test_an_unclamped_cursor_has_no_window_and_other_sources_have_no_cursors(
    owner, personal, sources, web_client
):
    source = _transcript_source(sources)
    TranscriptCursor.objects.create(
        workspace=personal,
        source=source,
        target_workspace=personal,
        node_ref="node-1",
        since=datetime(2026, 11, 14, tzinfo=UTC),
    )
    client = web_client(owner)
    (cursor,) = client.get(f"/api/v1/memory/ingestion/sources/{source.id}").json()[
        "transcript_cursors"
    ]
    assert cursor["since_clamped"] == 0 and cursor["last_skipped_window"] is None

    git = sources[IngestionSource.Kind.GIT]
    assert client.get(f"/api/v1/memory/ingestion/sources/{git.id}").json()[
        "transcript_cursors"
    ] == []


def test_the_pull_job_uses_memorys_constants():
    from tools import transcript_ingest

    assert transcript_ingest.AUDIT_ACTION == PULL_AUDIT_ACTION
    assert set(transcript_ingest.CAPABILITIES.values()) == set(CAPABILITY_BY_SOURCE_KIND.values())
    assert transcript_ingest.CAPABILITIES["claude"] == "claude.transcripts.owner_turns"
    assert transcript_ingest.CAPABILITIES["codex"] == "codex.transcripts.owner_turns"
