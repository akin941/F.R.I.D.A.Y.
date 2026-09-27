"""memory.tasks: the `embed_memory` Celery task + its periodic recovery scan
(Phase 1 `p1-memory-embeddings`), mirroring `memory/tests/test_tasks.py`'s
pattern for `run_extraction_job`.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage
from memory.jobs import MAX_EMBEDDING_RETRIES, claim_memory_for_embedding
from memory.models import Memory, compute_fingerprint
from memory.services import create_memory
from memory.tasks import _process_claimed_memory, embed_memory, recover_stale_memory_embeddings
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


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


class TestEmbedMemoryHappyPath:
    def test_succeeds_and_writes_an_embedding_via_the_fake_fixture(
        self, workspace, embedding_provider
    ):
        memory = make_memory(workspace)
        embed_memory(memory.id)
        memory.refresh_from_db()
        assert memory.embedding is not None
        assert memory.embedding_provider == embedding_provider
        assert memory.claim_token is None
        usage = ModelUsage.objects.get(purpose="memory.retrieval")
        assert usage.success is True

    def test_redelivery_of_an_embedded_memory_is_a_noop(self, workspace, embedding_provider):
        memory = make_memory(workspace)
        embed_memory(memory.id)
        usage_count = ModelUsage.objects.count()
        embed_memory(memory.id)  # redelivered
        assert ModelUsage.objects.count() == usage_count  # no second gateway call

    def test_no_provider_finalizes_as_retryable_without_crashing(self, workspace):
        """No embedding_provider fixture: GatewayError(no_provider) must not
        propagate — it finalizes as a retryable failure, same non-crashing
        contract as run_extraction_job."""
        memory = make_memory(workspace)
        embed_memory(memory.id)  # must not raise
        memory.refresh_from_db()
        assert memory.embedding is None
        assert memory.retry_count == 1
        assert memory.claim_token is None

    def test_retry_exhaustion_stops_claims_but_never_crashes(self, workspace):
        memory = make_memory(workspace)
        for _ in range(MAX_EMBEDDING_RETRIES):
            embed_memory(memory.id)
        memory.refresh_from_db()
        assert memory.retry_count == MAX_EMBEDDING_RETRIES
        embed_memory(memory.id)  # not claimable anymore; must still no-op cleanly
        memory.refresh_from_db()
        assert memory.retry_count == MAX_EMBEDDING_RETRIES

    def test_unexpected_exception_still_finalizes_as_retryable(self, workspace, embedding_provider):
        """Mirrors run_extraction_job's broad-exception discipline: a claimed
        memory must never be silently abandoned mid-flight."""
        memory = make_memory(workspace)
        with mock.patch("memory.tasks.InferenceGateway", side_effect=RuntimeError("boom")):
            embed_memory(memory.id)  # must not raise
        memory.refresh_from_db()
        assert memory.retry_count == 1
        assert memory.claim_token is None

    def test_not_claimable_memory_is_a_safe_noop(self, workspace):
        memory = make_memory(workspace, status=Memory.Status.PENDING_APPROVAL)
        embed_memory(memory.id)  # must not raise
        assert ModelUsage.objects.count() == 0

    def test_memory_turned_private_after_claim_is_never_sent_to_the_gateway(
        self, workspace, embedding_provider
    ):
        """Codex round-1 P1: the claim and the gateway call are not atomic —
        if the row turns private (or is forgotten) in the window between
        them, the row's content must never reach `gateway.embed()` at all,
        not just be discarded afterward at finalize time."""
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        Memory.objects.filter(pk=memory.pk).update(sensitivity=Memory.Sensitivity.PRIVATE)
        _process_claimed_memory(*claimed)  # must not raise, must not call the gateway
        assert ModelUsage.objects.count() == 0
        memory.refresh_from_db()
        assert memory.embedding is None

    def test_memory_forgotten_after_claim_is_never_sent_to_the_gateway(
        self, workspace, embedding_provider
    ):
        memory = make_memory(workspace)
        claimed = claim_memory_for_embedding(memory.id)
        Memory.objects.filter(pk=memory.pk).update(status=Memory.Status.ARCHIVED)
        _process_claimed_memory(*claimed)
        assert ModelUsage.objects.count() == 0


class TestRecoverStaleMemoryEmbeddings:
    def test_never_claimed_eligible_memory_is_dispatched(self, workspace):
        memory = make_memory(workspace)
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 1
        dispatch.assert_called_once_with(memory.id)

    def test_expired_lease_memory_is_redispatched(self, workspace):
        memory = make_memory(workspace)
        claim_memory_for_embedding(memory.id)
        Memory.objects.filter(pk=memory.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 1
        dispatch.assert_called_once_with(memory.id)

    def test_live_lease_memory_is_not_redispatched(self, workspace):
        memory = make_memory(workspace)
        claim_memory_for_embedding(memory.id)
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 0
        dispatch.assert_not_called()

    def test_already_embedded_memory_is_not_dispatched(self, workspace, embedding_provider):
        make_memory(workspace, embedding=[0.1, 0.2], embedding_provider=embedding_provider)
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 0
        dispatch.assert_not_called()

    def test_ineligible_memory_is_not_dispatched(self, workspace):
        make_memory(workspace, "private one", sensitivity=Memory.Sensitivity.PRIVATE)
        make_memory(workspace, "pending one", status=Memory.Status.PENDING_APPROVAL)
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 0
        dispatch.assert_not_called()

    def test_retry_exhausted_memory_is_not_dispatched(self, workspace):
        make_memory(workspace, retry_count=MAX_EMBEDDING_RETRIES)
        with mock.patch("memory.tasks.dispatch_memory_embedding") as dispatch:
            count = recover_stale_memory_embeddings()
        assert count == 0
        dispatch.assert_not_called()


class TestReconcileEmbeddingDispatch:
    """`memory.services._reconcile_embedding`'s `on_commit` dispatch — needs
    `transaction=True` so `on_commit` callbacks actually fire (a plain
    `django_db` test wraps everything in a rolled-back outer transaction and
    `on_commit` never runs; mirrors `memory/tests/test_tasks.py`'s
    `TestDispatch` for `create_extraction_job`'s identical on_commit
    pattern)."""

    @pytest.mark.django_db(transaction=True)
    def test_create_memory_dispatches_embedding_on_commit(self):
        alice = register_user(email="embeddispatch@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        # dispatch_memory_embedding does a LOCAL `from memory.tasks import
        # embed_memory` at call time, so the patch target is the task
        # object's home module (memory.tasks), not memory.jobs itself.
        with mock.patch("memory.tasks.embed_memory") as mocked_task:
            memory = create_memory(
                workspace=workspace,
                actor=alice,
                title="Coffee",
                content="Drinks coffee black.",
                memory_type=Memory.Type.PREFERENCE,
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        mocked_task.delay.assert_called_once_with(str(memory.id))

    @pytest.mark.django_db(transaction=True)
    def test_private_memory_creation_dispatches_nothing(self):
        alice = register_user(email="noembeddispatch@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        with mock.patch("memory.tasks.embed_memory") as mocked_task:
            create_memory(
                workspace=workspace,
                actor=alice,
                title="Secret",
                content="A private note.",
                memory_type=Memory.Type.PREFERENCE,
                sensitivity=Memory.Sensitivity.PRIVATE,
            )
        mocked_task.delay.assert_not_called()
