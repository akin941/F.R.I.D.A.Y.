"""knowledge.jobs: claim/finalize lease-fencing (MVP 3 §3), mirroring
memory/tests/test_jobs_worker.py's pattern. `make_document` creates rows
directly via the model manager (not the `create_*_document` services), so
none of these tests trigger the real `on_commit` dispatch — this file is
about the claim/finalize primitives in isolation, not the task pipeline
(covered separately in test_tasks.py).
"""

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelProvider
from knowledge.jobs import (
    MAX_INGESTION_RETRIES,
    claim_document,
    finalize_ingestion_failure,
    finalize_ingestion_success,
)
from knowledge.models import KnowledgeChunk, KnowledgeDocument
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def workspace(alice):
    return Workspace.objects.get(owner=alice)


@pytest.fixture
def embedding_provider():
    return ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )


def make_document(
    workspace,
    *,
    source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
    sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
    with_chunks=True,
):
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=source_type,
        title="Doc",
        status=KnowledgeDocument.Status.PENDING,
        sensitivity=sensitivity,
        raw_text="hello world" if with_chunks else None,
    )
    if with_chunks:
        KnowledgeChunk.objects.create(
            workspace=workspace,
            document=document,
            ordinal=0,
            content="hello world",
            token_count=2,
        )
    return document


def expire_lease(document):
    KnowledgeDocument.objects.filter(pk=document.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )


class TestClaim:
    def test_pending_document_is_claimable(self, workspace):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        assert claimed is not None
        document_id, token = claimed
        assert document_id == document.id
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PROCESSING
        assert document.claim_token == token
        assert document.lease_expires_at > timezone.now()

    def test_processing_document_with_live_lease_is_not_claimable(self, workspace):
        document = make_document(workspace)
        claim_document(document.id)
        assert claim_document(document.id) is None

    def test_processing_document_with_expired_lease_is_claimable_takeover(self, workspace):
        document = make_document(workspace)
        first = claim_document(document.id)
        expire_lease(document)
        second = claim_document(document.id)
        assert second is not None
        assert second[1] != first[1]  # a fresh token, not the stale one
        document.refresh_from_db()
        assert document.claim_token == second[1]

    def test_ready_document_is_not_claimable(self, workspace):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        finalize_ingestion_success(*claimed, content=None, embeddings=None)
        assert claim_document(document.id) is None

    def test_failed_document_is_not_claimable(self, workspace):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        finalize_ingestion_failure(*claimed, error_message="nope", retryable=False)
        assert claim_document(document.id) is None

    def test_nonexistent_document_is_not_claimable(self):
        assert claim_document(uuid.uuid4()) is None


@pytest.mark.django_db(transaction=True)
def test_two_workers_racing_to_claim_one_pending_document_exactly_one_wins():
    """Genuine two-connection concurrency test: only one of two simultaneous
    claim attempts on the same pending document may succeed."""
    alice = register_user(email="claimrace@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=alice)
    document = make_document(workspace)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key):
        try:
            barrier.wait(timeout=5)
            results[key] = claim_document(document.id)
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    winners = [v for v in results.values() if v is not None]
    losers = [v for v in results.values() if v is None]
    assert len(winners) == 1
    assert len(losers) == 1


class TestFinalizeSuccessAlreadyChunked:
    """`content=None` path: pasted_text/file_upload documents, already
    chunked synchronously in branch 2 — finalize only attaches embeddings."""

    def test_writes_embeddings_and_marks_ready(self, workspace, embedding_provider):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        assert finalize_ingestion_success(
            *claimed,
            content=None,
            embeddings=[([0.1, 0.2, 0.3], embedding_provider)],
        )
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert document.claim_token is None
        assert document.lease_expires_at is None
        chunk = document.chunks.get()
        assert chunk.embedding is not None
        assert chunk.embedding_provider_id == embedding_provider.id

    def test_private_document_skips_embedding_but_still_becomes_ready(self, workspace):
        document = make_document(workspace, sensitivity=KnowledgeDocument.Sensitivity.PRIVATE)
        claimed = claim_document(document.id)
        assert finalize_ingestion_success(*claimed, content=None, embeddings=None)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        chunk = document.chunks.get()
        assert chunk.embedding is None
        assert chunk.embedding_provider is None

    def test_embeddings_length_mismatch_raises(self, workspace, embedding_provider):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        with pytest.raises(ValueError, match="must match"):
            finalize_ingestion_success(
                *claimed,
                content=None,
                # 2 (vector, provider) pairs, only 1 chunk exists
                embeddings=[([0.1], embedding_provider), ([0.2], embedding_provider)],
            )

    def test_fenced_out_wrong_token_is_a_noop(self, workspace):
        document = make_document(workspace)
        claim_document(document.id)  # real claim
        wrong_token = uuid.uuid4()
        result = finalize_ingestion_success(document.id, wrong_token, content=None, embeddings=None)
        assert result is False
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PROCESSING  # untouched
        chunk = document.chunks.get()
        assert chunk.embedding is None


class TestFinalizeSuccessUrlContent:
    """`content=<fetched text>` path: a `url` document with no chunks yet —
    finalize both creates the chunk rows and (optionally) embeds them, all
    inside the one fenced transaction."""

    def test_creates_chunks_and_populates_document_fields(self, workspace):
        document = make_document(
            workspace, source_type=KnowledgeDocument.SourceType.URL, with_chunks=False
        )
        assert document.chunks.count() == 0
        claimed = claim_document(document.id)
        assert finalize_ingestion_success(
            *claimed, content="Fetched page content.", embeddings=None
        )
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert document.raw_text == "Fetched page content."
        assert document.content_hash
        assert document.byte_size == len(b"Fetched page content.")
        assert document.chunks.count() == 1

    def test_private_url_document_creates_unembedded_chunks(self, workspace):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            sensitivity=KnowledgeDocument.Sensitivity.PRIVATE,
            with_chunks=False,
        )
        claimed = claim_document(document.id)
        assert finalize_ingestion_success(
            *claimed, content="Private fetched content.", embeddings=None
        )
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        chunk = document.chunks.get()
        assert chunk.embedding is None

    def test_url_content_with_embeddings(self, workspace, embedding_provider):
        document = make_document(
            workspace, source_type=KnowledgeDocument.SourceType.URL, with_chunks=False
        )
        claimed = claim_document(document.id)
        assert finalize_ingestion_success(
            *claimed,
            content="Fetched page content.",
            embeddings=[([0.5, 0.5], embedding_provider)],
        )
        chunk = document.chunks.get()
        assert chunk.embedding is not None
        assert chunk.embedding_provider_id == embedding_provider.id


class TestFinalizeFailure:
    def test_retryable_returns_to_pending_and_increments_retry_count(self, workspace):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        assert finalize_ingestion_failure(*claimed, error_message="slow down", retryable=True)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PENDING
        assert document.retry_count == 1
        assert document.claim_token is None
        assert document.error_message == "slow down"

    def test_retry_exhaustion_transitions_to_failed(self, workspace):
        document = make_document(workspace)
        for _ in range(MAX_INGESTION_RETRIES):
            claimed = claim_document(document.id)
            finalize_ingestion_failure(*claimed, error_message="slow", retryable=True)
            document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.retry_count == MAX_INGESTION_RETRIES

    def test_non_retryable_fails_immediately_regardless_of_retry_count(self, workspace):
        document = make_document(workspace)
        claimed = claim_document(document.id)
        assert finalize_ingestion_failure(*claimed, error_message="bad url", retryable=False)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.retry_count == 0

    def test_fenced_out_wrong_token_is_a_noop(self, workspace):
        document = make_document(workspace)
        claim_document(document.id)
        wrong_token = uuid.uuid4()
        result = finalize_ingestion_failure(
            document.id, wrong_token, error_message="x", retryable=True
        )
        assert result is False
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PROCESSING  # untouched
        assert document.retry_count == 0


@pytest.mark.django_db(transaction=True)
def test_stale_worker_is_fenced_out_after_takeover_success_and_retry_variants():
    """Genuine two-thread takeover test: worker A claims the document, its
    lease is then (simulated to be) expired, worker B takes over and
    finalizes first. Worker A's later finalize attempt — tested for BOTH a
    success and a retryable-failure outcome — must be discarded, never
    overwriting B's result."""
    alice = register_user(email="takeover@test.dev", password=PASSWORD, name="Takeover")
    workspace = Workspace.objects.get(owner=alice)

    for success_variant in (True, False):
        document = make_document(workspace)
        claim_a = claim_document(document.id)
        expire_lease(document)

        b_claimed = threading.Event()
        results = {}
        errors = []

        t_b = threading.Thread(
            target=_takeover_worker_b, args=(document, results, errors, b_claimed)
        )
        t_a = threading.Thread(
            target=_takeover_worker_a_stale,
            args=(claim_a, results, errors, b_claimed, success_variant),
        )
        t_b.start()
        t_a.start()
        t_b.join(timeout=10)
        t_a.join(timeout=10)

        assert not errors, errors
        assert results["claim_b"] is not None
        assert results["b_finalize"] is True
        assert results["a_finalize"] is False  # stale worker was fenced out

        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY


def _takeover_worker_b(document, results, errors, b_claimed):
    try:
        claim_b = claim_document(document.id)
        results["claim_b"] = claim_b
        b_claimed.set()
        results["b_finalize"] = finalize_ingestion_success(*claim_b, content=None, embeddings=None)
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()


def _takeover_worker_a_stale(claim_a, results, errors, b_claimed, success_variant):
    try:
        assert b_claimed.wait(timeout=5)
        if success_variant:
            results["a_finalize"] = finalize_ingestion_success(
                *claim_a, content=None, embeddings=None
            )
        else:
            results["a_finalize"] = finalize_ingestion_failure(
                *claim_a, error_message="stale", retryable=True
            )
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()
