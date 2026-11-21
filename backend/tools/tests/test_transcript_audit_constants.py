"""Phase 8B branch 9: the transcript pull and the audit view read the same
audit rows, so the pull job uses memory's constants (memory may not import
tools; the direction is tools -> memory)."""

from memory.ingestion.transcript_audit import CAPABILITY_BY_SOURCE_KIND, PULL_AUDIT_ACTION
from tools import transcript_ingest


def test_the_pull_job_uses_memorys_constants():
    assert transcript_ingest.AUDIT_ACTION == PULL_AUDIT_ACTION
    assert set(transcript_ingest.CAPABILITIES.values()) == set(CAPABILITY_BY_SOURCE_KIND.values())
    assert transcript_ingest.CAPABILITIES["claude"] == "claude.transcripts.owner_turns"
    assert transcript_ingest.CAPABILITIES["codex"] == "codex.transcripts.owner_turns"
