"""knowledge.tasks: the ingestion Celery task + recovery scan (MVP 3 §3),
mirroring memory/tests/test_tasks.py's pattern. Documents are built directly
via the model manager (see knowledge/tests/test_jobs.py's identical
rationale) except in `TestDispatch`, which needs the real `create_*_document`
service call sites to exercise `on_commit` dispatch itself.

`knowledge.tasks.fetch_url` is always mocked for `url`-source documents —
never a real network call in this test suite.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.exceptions import GatewayError
from ai_core.models import ModelProvider, ModelUsage
from knowledge.jobs import MAX_INGESTION_RETRIES, claim_document
from knowledge.models import KnowledgeChunk, KnowledgeDocument
from knowledge.services import create_pasted_text_document
from knowledge.tasks import process_knowledge_document, recover_stale_knowledge_documents
from knowledge.url_fetch import UrlFetchError
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
def fake_embedding_provider():
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
    source_url="",
    with_chunks=True,
):
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=source_type,
        title="Doc",
        status=KnowledgeDocument.Status.PENDING,
        sensitivity=sensitivity,
        source_url=source_url,
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


class TestProcessKnowledgeDocumentAlreadyChunkedHappyPath:
    def test_succeeds_and_embeds_via_the_fake_fixture(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        chunk = document.chunks.get()
        assert chunk.embedding is not None
        assert chunk.embedding_provider_id == fake_embedding_provider.id
        usage = ModelUsage.objects.get(purpose="knowledge.embedding")
        assert usage.success is True

    def test_redelivery_of_a_ready_document_is_a_noop(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        process_knowledge_document(document.id)
        usage_count = ModelUsage.objects.count()
        process_knowledge_document(document.id)  # redelivered
        assert ModelUsage.objects.count() == usage_count  # no second gateway call
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY


class TestProcessKnowledgeDocumentPrivateGate:
    def test_private_document_never_calls_embed(self, workspace, fake_embedding_provider):
        """Ingestion-side private gate (§2): captured via a spy on the
        gateway's own `embed()`, not just an assertion on the end state —
        proves the call itself never happens, not merely that its result
        was discarded."""
        document = make_document(workspace, sensitivity=KnowledgeDocument.Sensitivity.PRIVATE)
        with mock.patch("knowledge.tasks.InferenceGateway.embed") as mocked_embed:
            process_knowledge_document(document.id)
        mocked_embed.assert_not_called()
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        chunk = document.chunks.get()
        assert chunk.embedding is None
        assert not ModelUsage.objects.filter(purpose="knowledge.embedding").exists()

    def test_private_url_document_fetches_but_never_embeds(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            sensitivity=KnowledgeDocument.Sensitivity.PRIVATE,
            source_url="http://example.com/private",
            with_chunks=False,
        )
        with (
            mock.patch("knowledge.tasks.fetch_url", return_value="Private fetched text."),
            mock.patch("knowledge.tasks.InferenceGateway.embed") as mocked_embed,
        ):
            process_knowledge_document(document.id)
        mocked_embed.assert_not_called()
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert document.raw_text == "Private fetched text."
        chunk = document.chunks.get()
        assert chunk.embedding is None


class TestProcessKnowledgeDocumentPerBatchProviderTagging:
    """Regression lock for the round-2 review finding: a batched
    `gateway.embed()` call can fall back to a different provider partway
    through a document, and every chunk must be tagged with the provider
    that actually produced ITS OWN vector — never overwritten by a later
    batch's provider. This is the live contract retrieval's
    filter-by-provider-before-comparing-vectors invariant depends on
    (`KnowledgeChunkQuerySet.cloud_eligible()` docstring, `knowledge/models.py`).
    """

    def test_mid_document_provider_fallback_tags_each_chunk_with_its_own_batch_provider(
        self, workspace
    ):
        provider_a = ModelProvider.objects.create(
            name="embedding-a",
            provider_type=ModelProvider.ProviderType.FAKE,
            capability=ModelProvider.Capability.EMBEDDING,
            model_id="fake-embedding-a",
            priority=0,
        )
        provider_b = ModelProvider.objects.create(
            name="embedding-b",
            provider_type=ModelProvider.ProviderType.FAKE,
            capability=ModelProvider.Capability.EMBEDDING,
            model_id="fake-embedding-b",
            priority=1,
        )

        # _EMBED_BATCH_SIZE is 100: 250 chunks means 3 batches (100, 100, 50).
        # Batches 1-2 (chunks 0-199) resolve to provider A; batch 3 (chunks
        # 200-249) falls back to provider B — this is the exact "provider
        # changes mid-document" shape the review finding was about.
        chunk_count = 250
        document = KnowledgeDocument.objects.create(
            workspace=workspace,
            source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
            title="Big doc",
            status=KnowledgeDocument.Status.PENDING,
            sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            raw_text="x",
        )
        KnowledgeChunk.objects.bulk_create(
            [
                KnowledgeChunk(
                    workspace=workspace,
                    document=document,
                    ordinal=i,
                    content=f"chunk {i} text",
                    token_count=2,
                )
                for i in range(chunk_count)
            ]
        )

        def fake_embed(self, *, purpose, texts, workspace, **kwargs):
            fake_embed.call_count += 1
            provider = provider_a if fake_embed.call_count <= 2 else provider_b
            usage = ModelUsage(purpose=purpose, workspace=workspace, provider=provider)
            return mock.Mock(vectors=[[0.1, 0.2] for _ in texts], usage=usage)

        fake_embed.call_count = 0

        with mock.patch("knowledge.tasks.InferenceGateway.embed", fake_embed):
            process_knowledge_document(document.id)

        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert fake_embed.call_count == 3  # confirms the 3-batch shape this test relies on

        chunks_a = list(
            KnowledgeChunk.objects.filter(document=document, embedding_provider=provider_a)
            .order_by("ordinal")
            .values_list("ordinal", flat=True)
        )
        chunks_b = list(
            KnowledgeChunk.objects.filter(document=document, embedding_provider=provider_b)
            .order_by("ordinal")
            .values_list("ordinal", flat=True)
        )
        # Each chunk carries its OWN batch's provider — batches 1-2 (ordinals
        # 0-199) never get relabeled with batch 3's provider, and batch 3
        # (ordinals 200-249) isn't mislabeled with an earlier batch's either.
        assert chunks_a == list(range(0, 200))
        assert chunks_b == list(range(200, 250))
        assert all(
            chunk.embedding is not None
            for chunk in KnowledgeChunk.objects.filter(document=document)
        )

        # The retrieval-side half of the invariant: filtering to a single
        # embedding_provider (branch 4's mandatory pre-comparison step, see
        # KnowledgeChunkQuerySet.cloud_eligible()'s docstring) finds exactly
        # the chunks that provider actually embedded — neither group leaks
        # into the other, and every chunk is findable through ITS provider.
        assert (
            KnowledgeChunk.objects.filter(document=document, embedding_provider=provider_a).count()
            == 200
        )
        assert (
            KnowledgeChunk.objects.filter(document=document, embedding_provider=provider_b).count()
            == 50
        )
        assert (
            KnowledgeChunk.objects.filter(document=document)
            .exclude(embedding_provider__in=[provider_a, provider_b])
            .count()
            == 0
        )


class TestProcessKnowledgeDocumentGatewayFailures:
    def test_retryable_gateway_error_returns_document_to_pending(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        with mock.patch(
            "knowledge.tasks.InferenceGateway.embed",
            side_effect=GatewayError("rate_limit", retryable=True),
        ):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PENDING
        assert document.retry_count == 1
        assert document.error_message == "rate_limit"

    def test_non_retryable_gateway_error_fails_immediately(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        with mock.patch(
            "knowledge.tasks.InferenceGateway.embed",
            side_effect=GatewayError("auth_error", retryable=False),
        ):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.retry_count == 0

    def test_retry_exhaustion_across_repeated_redeliveries_ends_in_failed(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        with mock.patch(
            "knowledge.tasks.InferenceGateway.embed",
            side_effect=GatewayError("timeout", retryable=True),
        ):
            for _ in range(MAX_INGESTION_RETRIES):
                process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.retry_count == MAX_INGESTION_RETRIES


class TestProcessKnowledgeDocumentUrlFailures:
    def test_fetch_error_is_terminal_not_retried(self, workspace, fake_embedding_provider):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            source_url="http://blocked.example/x",
            with_chunks=False,
        )
        with mock.patch("knowledge.tasks.fetch_url", side_effect=UrlFetchError("blocked")):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.retry_count == 0  # never counted as a retry — terminal on first attempt
        assert document.error_message == "blocked"

    def test_empty_fetched_content_is_terminal(self, workspace, fake_embedding_provider):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            source_url="http://example.com/empty",
            with_chunks=False,
        )
        with mock.patch("knowledge.tasks.fetch_url", return_value="   "):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.chunks.count() == 0

    def test_nul_byte_in_fetched_content_is_terminal(self, workspace, fake_embedding_provider):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            source_url="http://example.com/nul",
            with_chunks=False,
        )
        with mock.patch("knowledge.tasks.fetch_url", return_value="before\x00after"):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.chunks.count() == 0

    def test_duplicate_content_hash_is_terminal(self, workspace, fake_embedding_provider):
        create_pasted_text_document(
            workspace=workspace,
            actor=workspace.owner,
            title="Existing",
            content="Duplicate body text.",
            sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
        )
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            source_url="http://example.com/dup",
            with_chunks=False,
        )
        with mock.patch("knowledge.tasks.fetch_url", return_value="Duplicate body text."):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        assert document.chunks.count() == 0

    def test_successful_url_fetch_creates_chunks_and_embeds(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(
            workspace,
            source_type=KnowledgeDocument.SourceType.URL,
            source_url="http://example.com/doc",
            with_chunks=False,
        )
        with mock.patch("knowledge.tasks.fetch_url", return_value="Fetched page content here."):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert document.raw_text == "Fetched page content here."
        chunk = document.chunks.get()
        assert chunk.embedding is not None


class TestProcessKnowledgeDocumentNotClaimable:
    def test_already_ready_document_is_a_safe_noop(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        process_knowledge_document(document.id)  # already ready — must not raise or reprocess
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY

    def test_processing_document_with_a_live_lease_is_left_alone(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        claim_document(document.id)  # simulate a live worker already holding it
        process_knowledge_document(document.id)  # this call cannot claim it
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PROCESSING


class TestRecoveryScan:
    def test_stale_pending_document_past_grace_is_redispatched(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        KnowledgeDocument.objects.filter(pk=document.pk).update(
            updated_at=timezone.now() - timedelta(seconds=120)
        )
        count = recover_stale_knowledge_documents()
        assert count == 1
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY  # re-dispatched, ran to completion

    def test_fresh_pending_document_within_grace_is_left_alone(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        count = recover_stale_knowledge_documents()
        assert count == 0
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PENDING

    def test_processing_document_with_expired_lease_is_redispatched(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        claim_document(document.id)
        KnowledgeDocument.objects.filter(pk=document.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        count = recover_stale_knowledge_documents()
        assert count == 1
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY

    def test_processing_document_with_live_lease_is_left_alone(
        self, workspace, fake_embedding_provider
    ):
        document = make_document(workspace)
        claim_document(document.id)
        count = recover_stale_knowledge_documents()
        assert count == 0
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PROCESSING

    def test_ready_document_is_ignored(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.READY
        assert recover_stale_knowledge_documents() == 0

    def test_failed_document_is_ignored_even_when_stale(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        with mock.patch(
            "knowledge.tasks.InferenceGateway.embed",
            side_effect=GatewayError("auth_error", retryable=False),
        ):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED
        KnowledgeDocument.objects.filter(pk=document.pk).update(
            updated_at=timezone.now() - timedelta(seconds=120)
        )
        assert recover_stale_knowledge_documents() == 0
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.FAILED


class TestDispatch:
    """create_pasted_text_document's on_commit dispatch — needs
    transaction=True so on_commit callbacks actually fire (a plain django_db
    test wraps everything in a rolled-back outer transaction and on_commit
    never runs)."""

    @pytest.mark.django_db(transaction=True)
    def test_dispatch_actually_publishes_after_commit(self):
        alice = register_user(email="dispatcher@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        # dispatch_knowledge_document does a LOCAL `from knowledge.tasks
        # import process_knowledge_document` at call time, so the patch
        # target is the task object's home module (knowledge.tasks), not
        # knowledge.jobs itself — knowledge.jobs never binds it at module
        # level.
        with mock.patch("knowledge.tasks.process_knowledge_document") as mocked_task:
            document = create_pasted_text_document(
                workspace=workspace,
                actor=alice,
                title="Doc",
                content="Some content.",
                sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            )
        mocked_task.delay.assert_called_once_with(str(document.id))

    @pytest.mark.django_db(transaction=True)
    def test_non_eager_broker_publish_failure_is_swallowed_not_propagated(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = False
        alice = register_user(email="brokerdown@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        with mock.patch(
            "knowledge.tasks.process_knowledge_document.delay",
            side_effect=ConnectionRefusedError("no redis"),
        ):
            document = create_pasted_text_document(
                workspace=workspace,
                actor=alice,
                title="Doc",
                content="Some content.",
                sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
            )
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PENDING  # untouched, not crashed

    @pytest.mark.django_db(transaction=True)
    def test_eager_task_body_exception_still_propagates(self, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        alice = register_user(email="eagerbug@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        with mock.patch("knowledge.tasks.claim_document", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                create_pasted_text_document(
                    workspace=workspace,
                    actor=alice,
                    title="Doc",
                    content="Some content.",
                    sensitivity=KnowledgeDocument.Sensitivity.NORMAL,
                )


class TestSecretSafeErrorMessage:
    def test_unexpected_exception_text_is_never_persisted(self, workspace, fake_embedding_provider):
        document = make_document(workspace)
        with mock.patch(
            "knowledge.tasks._process_claimed_document",
            side_effect=RuntimeError("dsn=postgres://user:hunter2@host/db"),
        ):
            process_knowledge_document(document.id)
        document.refresh_from_db()
        assert document.status == KnowledgeDocument.Status.PENDING
        assert "hunter2" not in document.error_message
        assert "postgres://" not in document.error_message
