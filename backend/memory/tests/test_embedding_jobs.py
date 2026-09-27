"""memory.jobs: Memory embedding claim/finalize lease-fencing (Phase 1
`p1-memory-embeddings`), mirroring `knowledge/tests/test_jobs.py`'s pattern.
`make_memory` creates rows directly via the model manager (not
`memory.services`), so none of these tests trigger the real `on_commit`
dispatch — this file is about the claim/finalize primitives in isolation,
not the task pipeline (covered separately in `test_embedding_tasks.py`).
"""

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelProvider
from memory.jobs import (
    MAX_EMBEDDING_RETRIES,
    claim_memory_for_embedding,
    finalize_embedding_failure,
    finalize_embedding_success,
)
from memory.models import Memory, compute_fingerprint
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


def make_memory(workspace, content="hello world", **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.SEMANTIC,
        "status": Memory.Status.APPROVED,
        "sensitivity": Memory.Sensitivity.NORMAL,
        "source": Memory.Source.USER_CREATED,
        "title": content[:30],
        "content": content,
        "fingerprint": compute_fingerprint(content),
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


def expire_lease(memory):
    Memory.objects.filter(pk=memory.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )


class TestClaim:
    def test_claiming_does_not_bump_updated_at(self, workspace):
        """Codex round-1 P2: `Memory.Meta.ordering` and
        `select_context_memories`'s deterministic fallback both sort by
        `-updated_at` — a background embedding lease claim must never look
        like a user curation event and reorder the row."""
        memory = make_memory(workspace)
        before = memory.updated_at
        claim_memory_for_embedding(memory.id)
        memory.refresh_from_db()
        assert memory.updated_at == before

    def test_eligible_unembedded_memory_is_claimable(self, workspace):
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        assert claimed is not None
        memory_id, token = claimed
        assert memory_id == memory.id
        memory.refresh_from_db()
        assert memory.claim_token == token
        assert memory.lease_expires_at > timezone.now()

    def test_already_claimed_with_live_lease_is_not_claimable(self, workspace):
        memory = make_memory(workspace)
        claim_memory_for_embedding(memory.id)
        assert claim_memory_for_embedding(memory.id) is None

    def test_claimed_with_expired_lease_is_claimable_takeover(self, workspace):
        memory = make_memory(workspace)
        first = claim_memory_for_embedding(memory.id)
        expire_lease(memory)
        second = claim_memory_for_embedding(memory.id)
        assert second is not None
        assert second[1] != first[1]  # a fresh token, not the stale one
        memory.refresh_from_db()
        assert memory.claim_token == second[1]

    def test_already_embedded_memory_is_not_claimable(self, workspace, embedding_provider):
        memory = make_memory(workspace, embedding=[0.1, 0.2], embedding_provider=embedding_provider)
        assert claim_memory_for_embedding(memory.id) is None

    def test_pending_approval_memory_is_not_claimable(self, workspace):
        memory = make_memory(workspace, status=Memory.Status.PENDING_APPROVAL)
        assert claim_memory_for_embedding(memory.id) is None

    def test_private_memory_is_not_claimable(self, workspace):
        memory = make_memory(workspace, sensitivity=Memory.Sensitivity.PRIVATE)
        assert claim_memory_for_embedding(memory.id) is None

    def test_retry_exhausted_memory_is_not_claimable(self, workspace):
        memory = make_memory(workspace, retry_count=MAX_EMBEDDING_RETRIES)
        assert claim_memory_for_embedding(memory.id) is None

    def test_nonexistent_memory_is_not_claimable(self):
        assert claim_memory_for_embedding(uuid.uuid4()) is None


@pytest.mark.django_db(transaction=True)
def test_two_workers_racing_to_claim_one_memory_exactly_one_wins():
    """Genuine two-connection concurrency test: only one of two simultaneous
    claim attempts on the same eligible memory may succeed."""
    alice = register_user(email="claimrace@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=alice)
    memory = make_memory(workspace)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key):
        try:
            barrier.wait(timeout=5)
            results[key] = claim_memory_for_embedding(memory.id)
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


class TestFinalizeSuccess:
    def test_writes_embedding_and_clears_lease(self, workspace, embedding_provider):
        memory = make_memory(workspace)
        before = memory.updated_at
        claimed = claim_memory_for_embedding(memory.id)
        assert finalize_embedding_success(
            *claimed, vector=[0.1, 0.2, 0.3], provider=embedding_provider
        )
        memory.refresh_from_db()
        assert memory.embedding is not None
        assert memory.embedding_provider_id == embedding_provider.id
        assert memory.claim_token is None
        assert memory.lease_expires_at is None
        assert memory.updated_at == before  # Codex round-1 P2
        assert memory.retry_count == 0

    def test_fenced_out_wrong_token_is_a_noop(self, workspace, embedding_provider):
        memory = make_memory(workspace)
        claim_memory_for_embedding(memory.id)  # real claim
        wrong_token = uuid.uuid4()
        result = finalize_embedding_success(
            memory.id, wrong_token, vector=[0.1], provider=embedding_provider
        )
        assert result is False
        memory.refresh_from_db()
        assert memory.embedding is None  # untouched

    def test_fenced_out_when_memory_turned_private_mid_flight(self, workspace, embedding_provider):
        """The belt-and-suspenders half of `_fenced_memory`: a claim_token
        match alone is not enough. If the memory turned private (or was
        forgotten/rejected) while the embed call was in flight, the
        eligibility re-check inside `_fenced_memory` must discard the write
        — a claim_token-only fence would let a stale attempt write a vector
        onto a row that is no longer supposed to have one at all."""
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        Memory.objects.filter(pk=memory.pk).update(sensitivity=Memory.Sensitivity.PRIVATE)
        result = finalize_embedding_success(
            *claimed, vector=[0.1, 0.2], provider=embedding_provider
        )
        assert result is False
        memory.refresh_from_db()
        assert memory.embedding is None

    def test_fenced_out_when_memory_archived_mid_flight(self, workspace, embedding_provider):
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        Memory.objects.filter(pk=memory.pk).update(status=Memory.Status.ARCHIVED)
        result = finalize_embedding_success(
            *claimed, vector=[0.1, 0.2], provider=embedding_provider
        )
        assert result is False
        memory.refresh_from_db()
        assert memory.embedding is None


class TestFinalizeFailure:
    def test_retryable_increments_retry_count_and_clears_lease(self, workspace):
        memory = make_memory(workspace)
        before = memory.updated_at
        claimed = claim_memory_for_embedding(memory.id)
        assert finalize_embedding_failure(*claimed, retryable=True)
        memory.refresh_from_db()
        assert memory.retry_count == 1
        assert memory.claim_token is None
        assert memory.embedding is None
        assert memory.updated_at == before  # Codex round-1 P2

    def test_retry_exhaustion_stops_further_claims_without_a_terminal_status(self, workspace):
        memory = make_memory(workspace)
        for _ in range(MAX_EMBEDDING_RETRIES):
            claimed = claim_memory_for_embedding(memory.id)
            finalize_embedding_failure(*claimed, retryable=True)
        memory.refresh_from_db()
        assert memory.retry_count == MAX_EMBEDDING_RETRIES
        # No separate "failed" status exists for Memory (unlike
        # KnowledgeDocument) — the row just stops being claimable, and
        # retrieval already falls back to deterministic ordering for it.
        assert memory.status == Memory.Status.APPROVED
        assert claim_memory_for_embedding(memory.id) is None

    def test_non_retryable_jumps_straight_to_the_retry_ceiling(self, workspace):
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        assert finalize_embedding_failure(*claimed, retryable=False)
        memory.refresh_from_db()
        assert memory.retry_count == MAX_EMBEDDING_RETRIES
        assert claim_memory_for_embedding(memory.id) is None

    def test_fenced_out_wrong_token_is_a_noop(self, workspace):
        memory = make_memory(workspace)
        claim_memory_for_embedding(memory.id)
        wrong_token = uuid.uuid4()
        result = finalize_embedding_failure(memory.id, wrong_token, retryable=True)
        assert result is False
        memory.refresh_from_db()
        assert memory.retry_count == 0


@pytest.mark.django_db(transaction=True)
def test_stale_worker_is_fenced_out_after_takeover_success_and_retry_variants():
    """Genuine two-thread takeover test: worker A claims the memory, its
    lease is then (simulated to be) expired, worker B takes over and
    finalizes first. Worker A's later finalize attempt — tested for BOTH a
    success and a retryable-failure outcome — must be discarded, never
    overwriting B's result."""
    alice = register_user(email="takeover@test.dev", password=PASSWORD, name="Takeover")
    workspace = Workspace.objects.get(owner=alice)
    embedding_provider = ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )

    for success_variant in (True, False):
        memory = make_memory(workspace, content=f"variant {success_variant}")
        claim_a = claim_memory_for_embedding(memory.id)
        expire_lease(memory)

        b_claimed = threading.Event()
        results = {}
        errors = []

        t_b = threading.Thread(
            target=_takeover_worker_b, args=(memory, embedding_provider, results, errors, b_claimed)
        )
        t_a = threading.Thread(
            target=_takeover_worker_a_stale,
            args=(claim_a, embedding_provider, results, errors, b_claimed, success_variant),
        )
        t_b.start()
        t_a.start()
        t_b.join(timeout=10)
        t_a.join(timeout=10)

        assert not errors, errors
        assert results["claim_b"] is not None
        assert results["b_finalize"] is True
        assert results["a_finalize"] is False  # stale worker was fenced out

        memory.refresh_from_db()
        assert memory.embedding is not None


def _takeover_worker_b(memory, provider, results, errors, b_claimed):
    try:
        claim_b = claim_memory_for_embedding(memory.id)
        results["claim_b"] = claim_b
        b_claimed.set()
        results["b_finalize"] = finalize_embedding_success(
            *claim_b, vector=[0.9, 0.9], provider=provider
        )
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()


def _takeover_worker_a_stale(claim_a, provider, results, errors, b_claimed, success_variant):
    try:
        assert b_claimed.wait(timeout=5)
        if success_variant:
            results["a_finalize"] = finalize_embedding_success(
                *claim_a, vector=[0.1, 0.1], provider=provider
            )
        else:
            results["a_finalize"] = finalize_embedding_failure(*claim_a, retryable=True)
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()
