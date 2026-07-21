import threading
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from assistant.models import Conversation, Message
from memory.jobs import (
    MAX_EXTRACTION_RETRIES,
    claim_extraction_job,
    create_extraction_job,
    finalize_extraction_failure,
    finalize_extraction_success,
)
from memory.models import Memory, MemoryExtractionJob, compute_fingerprint
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
def conversation(workspace, alice):
    return Conversation.objects.create(workspace=workspace, created_by=alice)


def make_message(conversation, content="hi"):
    return Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        status=Message.Status.COMPLETE,
    )


def make_job(conversation):
    message = make_message(conversation)
    return create_extraction_job(conversation=conversation, through_message=message)


def expire_lease(job):
    MemoryExtractionJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )


class TestClaim:
    def test_pending_job_is_claimable(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        assert claimed is not None
        job_id, token = claimed
        assert job_id == job.id
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.RUNNING
        assert job.claim_token == token
        assert job.lease_expires_at > timezone.now()

    def test_running_job_with_live_lease_is_not_claimable(self, conversation):
        job = make_job(conversation)
        claim_extraction_job(job.id)  # first claim, live lease
        assert claim_extraction_job(job.id) is None

    def test_running_job_with_expired_lease_is_claimable_takeover(self, conversation):
        job = make_job(conversation)
        first = claim_extraction_job(job.id)
        expire_lease(job)
        second = claim_extraction_job(job.id)
        assert second is not None
        assert second[1] != first[1]  # a fresh token, not the stale one
        job.refresh_from_db()
        assert job.claim_token == second[1]

    def test_succeeded_job_is_not_claimable(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        finalize_extraction_success(*claimed, [])
        assert claim_extraction_job(job.id) is None

    def test_failed_job_is_not_claimable(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        finalize_extraction_failure(
            *claimed, error_type="auth_error", error_message="nope", retryable=False
        )
        assert claim_extraction_job(job.id) is None

    def test_nonexistent_job_is_not_claimable(self):
        assert claim_extraction_job(uuid.uuid4()) is None


@pytest.mark.django_db(transaction=True)
def test_two_workers_racing_to_claim_one_pending_job_exactly_one_wins():
    """Genuine two-connection concurrency test: only one of two simultaneous
    claim attempts on the same pending job may succeed.

    Unlike the plain `django_db` tests above, `transaction=True` means
    `create_extraction_job`'s `on_commit` dispatch genuinely fires — this
    test is about the claim primitive in isolation, not the full task
    pipeline (covered separately in test_tasks.py), so the real dispatch is
    patched out; otherwise the auto-dispatched (eager-mode) task would claim
    the job itself before this test's own two threads got to it.
    """
    alice = register_user(email="claimrace@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=alice)
    conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
    with mock.patch("memory.jobs.dispatch_extraction_job"):
        job = make_job(conversation)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key):
        try:
            barrier.wait(timeout=5)
            results[key] = claim_extraction_job(job.id)
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
    def test_creates_memories_and_marks_succeeded(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        candidates = [{"type": "preference", "title": "Coffee", "content": "Drinks coffee black."}]
        assert finalize_extraction_success(*claimed, candidates) is True
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        assert job.claim_token is None
        assert job.lease_expires_at is None
        memory = Memory.objects.get(workspace=job.workspace)
        assert memory.status == Memory.Status.PENDING_APPROVAL
        assert memory.source == Memory.Source.AI_SUGGESTED
        assert memory.sensitivity == Memory.Sensitivity.NORMAL
        assert memory.source_conversation == conversation
        assert memory.fingerprint == compute_fingerprint("Drinks coffee black.")

    def test_empty_candidates_still_succeeds(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        assert finalize_extraction_success(*claimed, []) is True
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        assert not Memory.objects.filter(workspace=job.workspace).exists()

    def test_duplicate_fingerprint_is_silently_absorbed(self, conversation, alice):
        from memory.services import create_memory

        create_memory(
            workspace=conversation.workspace,
            actor=alice,
            title="Existing",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        candidates = [{"type": "preference", "title": "Coffee", "content": "Drinks coffee black."}]
        assert finalize_extraction_success(*claimed, candidates) is True  # no IntegrityError
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        assert Memory.objects.filter(workspace=job.workspace).count() == 1  # not duplicated

    def test_fenced_out_wrong_token_is_a_noop(self, conversation):
        job = make_job(conversation)
        claim_extraction_job(job.id)  # real claim
        wrong_token = uuid.uuid4()
        result = finalize_extraction_success(
            job.id, wrong_token, [{"type": "semantic", "title": "T", "content": "C"}]
        )
        assert result is False
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.RUNNING  # untouched
        assert not Memory.objects.filter(workspace=job.workspace).exists()

    def test_conversation_turned_private_after_claim_discards_candidates(self, conversation):
        """Enforcement point (c) — the actual race-closing guarantee (Phase
        1 p1-work-log, Codex round-3 HIGH on the brief): the conversation
        can turn private AFTER the worker-time check
        (memory.tasks._process_claimed_job) but WHILE the extraction
        prompt is in flight. finalize_extraction_success must re-check
        under lock and discard the candidates regardless of what the
        caller passed, still marking the job succeeded (not failed)."""
        from assistant.services import mark_conversation_private

        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        mark_conversation_private(conversation=conversation, actor=conversation.created_by)
        candidates = [{"type": "semantic", "title": "T", "content": "Should never be created."}]
        assert finalize_extraction_success(*claimed, candidates) is True
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        assert not Memory.objects.filter(workspace=job.workspace).exists()


class TestFinalizeFailure:
    def test_retryable_returns_to_pending_and_increments_retry_count(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        assert finalize_extraction_failure(
            *claimed, error_type="rate_limit", error_message="slow down", retryable=True
        )
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING
        assert job.retry_count == 1
        assert job.claim_token is None
        assert job.error_type == "rate_limit"

    def test_retry_exhaustion_transitions_to_failed(self, conversation):
        job = make_job(conversation)
        for _ in range(MAX_EXTRACTION_RETRIES):
            claimed = claim_extraction_job(job.id)
            finalize_extraction_failure(
                *claimed, error_type="timeout", error_message="slow", retryable=True
            )
            job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        # retry_count increments on every attempt, including the one that
        # exhausts the budget — it reaches MAX_EXTRACTION_RETRIES exactly,
        # it isn't left one short of it.
        assert job.retry_count == MAX_EXTRACTION_RETRIES

    def test_non_retryable_fails_immediately_regardless_of_retry_count(self, conversation):
        job = make_job(conversation)
        claimed = claim_extraction_job(job.id)
        assert finalize_extraction_failure(
            *claimed, error_type="auth_error", error_message="bad key", retryable=False
        )
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        assert job.retry_count == 0

    def test_fenced_out_wrong_token_is_a_noop(self, conversation):
        job = make_job(conversation)
        claim_extraction_job(job.id)
        wrong_token = uuid.uuid4()
        result = finalize_extraction_failure(
            job.id, wrong_token, error_type="timeout", error_message="x", retryable=True
        )
        assert result is False
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.RUNNING  # untouched
        assert job.retry_count == 0


@pytest.mark.django_db(transaction=True)
def test_stale_worker_is_fenced_out_after_takeover_success_and_retry_variants():
    """Genuine two-thread takeover test (brief §4 requirement): worker A
    claims the job, its lease is then (simulated to be) expired, worker B
    takes over and finalizes first. Worker A's later finalize attempt —
    tested for BOTH a success and a retryable-failure outcome — must be
    discarded, never overwriting B's result."""
    alice = register_user(email="takeover@test.dev", password=PASSWORD, name="Takeover")
    workspace = Workspace.objects.get(owner=alice)

    for success_variant in (True, False):
        conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        message = Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="hi",
            status=Message.Status.COMPLETE,
        )
        # Real dispatch patched out for the same reason as the claim-race
        # test above: this test is about claim/finalize fencing, not the
        # full task pipeline.
        with mock.patch("memory.jobs.dispatch_extraction_job"):
            job = create_extraction_job(conversation=conversation, through_message=message)

        claim_a = claim_extraction_job(job.id)
        expire_lease(job)

        # `b_claimed` enforces the ordering the scenario depends on: worker B
        # must claim (rotating the token, invalidating A's) BEFORE worker A
        # attempts its stale finalize — otherwise the two threads racing
        # freely could let A finalize first (its token is still nominally
        # valid until B's claim rotates it), which is a different, already
        # separately-tested scenario (plain fencing-by-wrong-token), not the
        # takeover-ordering this test targets.
        b_claimed = threading.Event()
        results = {}
        errors = []

        # Content must be unique per loop iteration: both iterations share
        # `workspace`, and Memory.fingerprint dedup is unique per workspace —
        # reusing content across iterations would make the second iteration's
        # insert a silently-absorbed duplicate of the first's, masking what
        # this test is actually checking.
        b_content = f"B content ({success_variant})"
        a_content = f"A content ({success_variant})"

        t_b = threading.Thread(
            target=_takeover_worker_b, args=(job, results, errors, b_claimed, b_content)
        )
        t_a = threading.Thread(
            target=_takeover_worker_a_stale,
            args=(claim_a, results, errors, b_claimed, success_variant, a_content),
        )
        t_b.start()
        t_a.start()
        t_b.join(timeout=10)
        t_a.join(timeout=10)

        assert not errors, errors
        assert results["claim_b"] is not None
        assert results["b_finalize"] is True
        assert results["a_finalize"] is False  # stale worker was fenced out

        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        memories = Memory.objects.filter(workspace=workspace, source_conversation=conversation)
        titles = set(memories.values_list("title", flat=True))
        assert titles == {"B wins"}  # A's write never landed


def _takeover_worker_b(job, results, errors, b_claimed, b_content):
    try:
        claim_b = claim_extraction_job(job.id)
        results["claim_b"] = claim_b
        b_claimed.set()
        results["b_finalize"] = finalize_extraction_success(
            *claim_b, [{"type": "semantic", "title": "B wins", "content": b_content}]
        )
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()


def _takeover_worker_a_stale(claim_a, results, errors, b_claimed, success_variant, a_content):
    try:
        assert b_claimed.wait(timeout=5)
        if success_variant:
            results["a_finalize"] = finalize_extraction_success(
                *claim_a, [{"type": "semantic", "title": "A stale", "content": a_content}]
            )
        else:
            results["a_finalize"] = finalize_extraction_failure(
                *claim_a, error_type="timeout", error_message="stale", retryable=True
            )
    except Exception as exc:  # pragma: no cover - surfaced via `errors`
        errors.append(exc)
    finally:
        connection.close()
