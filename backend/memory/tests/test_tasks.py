from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.exceptions import GatewayError
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant.models import Conversation, Message
from memory.extraction import ExtractionParseError
from memory.jobs import MAX_EXTRACTION_RETRIES, claim_extraction_job, create_extraction_job
from memory.models import Memory, MemoryExtractionJob
from memory.tasks import recover_stale_extraction_jobs, run_extraction_job
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
def conversation(workspace, alice):
    return Conversation.objects.create(workspace=workspace, created_by=alice)


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def extraction_prompt():
    template = PromptTemplate.objects.create(
        name="jarvis_memory_extraction", purpose="memory.extraction"
    )
    version = PromptVersion.objects.create(
        template=template, version=1, content="Extract memories."
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def make_job(conversation, content="hi"):
    message = Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        status=Message.Status.COMPLETE,
    )
    with mock.patch("memory.jobs.dispatch_extraction_job"):  # test drives run_extraction_job itself
        return create_extraction_job(conversation=conversation, through_message=message)


class TestRunExtractionJobHappyPath:
    def test_succeeds_and_creates_a_memory_via_the_fake_fixture(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        memory = Memory.objects.get(workspace=conversation.workspace)
        assert memory.status == Memory.Status.PENDING_APPROVAL
        assert memory.source == Memory.Source.AI_SUGGESTED
        assert memory.source_conversation == conversation
        usage = ModelUsage.objects.get(purpose="memory.extraction")
        assert usage.success is True

    def test_redelivery_of_a_succeeded_job_is_a_noop(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        run_extraction_job(job.id)
        usage_count = ModelUsage.objects.count()
        memory_count = Memory.objects.count()
        run_extraction_job(job.id)  # redelivered
        assert ModelUsage.objects.count() == usage_count  # no second gateway call
        assert Memory.objects.count() == memory_count
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED

    def test_missing_prompt_config_raises_and_finalizes_as_retryable(
        self, conversation, fake_provider
    ):
        """No extraction_prompt fixture: an unhandled ImproperlyConfigured
        must still finalize the claim (not leak a stuck `running` row),
        per run_extraction_job's broad exception handler."""
        job = make_job(conversation)
        run_extraction_job(job.id)  # must not raise
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING
        assert job.retry_count == 1
        assert job.error_type == "unexpected"


class TestRunExtractionJobGatewayFailures:
    def test_retryable_gateway_error_returns_job_to_pending(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks.InferenceGateway.complete",
            side_effect=GatewayError("rate_limit", retryable=True),
        ):
            run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING
        assert job.retry_count == 1
        assert job.error_type == "rate_limit"

    def test_non_retryable_gateway_error_fails_immediately(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks.InferenceGateway.complete",
            side_effect=GatewayError("auth_error", retryable=False),
        ):
            run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        assert job.retry_count == 0

    def test_retry_exhaustion_across_repeated_redeliveries_ends_in_failed(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks.InferenceGateway.complete",
            side_effect=GatewayError("timeout", retryable=True),
        ):
            for _ in range(MAX_EXTRACTION_RETRIES):
                run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        assert job.retry_count == MAX_EXTRACTION_RETRIES

    def test_one_modelusage_row_per_gateway_invoking_attempt(
        self, conversation, fake_provider, extraction_prompt
    ):
        """Each redelivery that actually reaches the gateway records its own
        ModelUsage row (the gateway's own one-row-per-call guarantee) — a
        redelivery of an already-succeeded job is the only case that
        records nothing (covered by test_redelivery_of_a_succeeded_job_is_a_noop).
        Patches the ADAPTER, not InferenceGateway.complete itself, so the
        real gateway's accounting path actually runs."""
        from ai_core.adapters.base import AdapterError

        job = make_job(conversation)
        adapter = mock.Mock()
        adapter.complete.side_effect = AdapterError("timeout", retryable=True)
        with mock.patch("ai_core.gateway.get_adapter", return_value=adapter):
            for attempt in range(1, MAX_EXTRACTION_RETRIES + 1):
                run_extraction_job(job.id)
                assert ModelUsage.objects.filter(purpose="memory.extraction").count() == attempt
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED


class TestRunExtractionJobSchemaFailure:
    def test_malformed_extractor_output_is_terminal_not_retried(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks.parse_candidates", side_effect=ExtractionParseError("bad json")
        ):
            run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        assert job.retry_count == 0  # never counted as a retry — terminal on the first attempt
        assert job.error_type == "schema_invalid"
        assert not Memory.objects.filter(workspace=conversation.workspace).exists()


class TestRunExtractionJobNotClaimable:
    def test_already_succeeded_job_is_a_safe_noop(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        run_extraction_job(job.id)  # already succeeded — must not raise or reprocess
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED

    def test_running_job_with_a_live_lease_is_left_alone(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        claim_extraction_job(job.id)  # simulate a live worker already holding it
        run_extraction_job(job.id)  # this call cannot claim it
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.RUNNING


class TestRecoveryScan:
    def test_stale_pending_job_past_grace_is_redispatched(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        MemoryExtractionJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(seconds=120)
        )
        count = recover_stale_extraction_jobs()
        assert count == 1
        job.refresh_from_db()
        assert (
            job.status == MemoryExtractionJob.Status.SUCCEEDED
        )  # re-dispatched, ran to completion

    def test_fresh_pending_job_within_grace_is_left_alone(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        count = recover_stale_extraction_jobs()
        assert count == 0
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING

    def test_running_job_with_expired_lease_is_redispatched(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        claim_extraction_job(job.id)
        MemoryExtractionJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        count = recover_stale_extraction_jobs()
        assert count == 1
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED

    def test_running_job_with_live_lease_is_left_alone(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        claim_extraction_job(job.id)
        count = recover_stale_extraction_jobs()
        assert count == 0
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.RUNNING

    def test_succeeded_job_is_ignored(self, conversation, fake_provider, extraction_prompt):
        job = make_job(conversation)
        run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.SUCCEEDED
        assert recover_stale_extraction_jobs() == 0

    def test_failed_job_is_ignored_even_when_stale(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks.InferenceGateway.complete",
            side_effect=GatewayError("auth_error", retryable=False),
        ):
            run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED
        # Old `updated_at` (past the pending grace window) must not matter —
        # a terminal FAILED job is never re-dispatched by staleness alone.
        MemoryExtractionJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(seconds=120)
        )
        assert recover_stale_extraction_jobs() == 0
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.FAILED


class TestDispatch:
    """create_extraction_job's on_commit dispatch — needs transaction=True
    so on_commit callbacks actually fire (a plain django_db test wraps
    everything in a rolled-back outer transaction and on_commit never runs,
    see memory/tests/test_jobs.py's equivalent note)."""

    @pytest.mark.django_db(transaction=True)
    def test_dispatch_actually_publishes_after_commit(self):
        alice = register_user(email="dispatcher@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        message = Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="hi",
            status=Message.Status.COMPLETE,
        )
        # dispatch_extraction_job does a LOCAL `from memory.tasks import
        # run_extraction_job` at call time, so the patch target is the task
        # object's home module (memory.tasks), not memory.jobs itself —
        # memory.jobs never binds it at module level.
        with mock.patch("memory.tasks.run_extraction_job") as mocked_task:
            job = create_extraction_job(conversation=conversation, through_message=message)
        mocked_task.delay.assert_called_once_with(str(job.id))

    @pytest.mark.django_db(transaction=True)
    def test_non_eager_broker_publish_failure_is_swallowed_not_propagated(self, settings):
        """In non-eager (production-shaped) mode, a broker publish failure
        must be caught and logged, never propagated into the caller — the
        chat reply must not fail because Redis is down."""
        settings.CELERY_TASK_ALWAYS_EAGER = False
        alice = register_user(email="brokerdown@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        message = Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="hi",
            status=Message.Status.COMPLETE,
        )
        with mock.patch(
            "memory.tasks.run_extraction_job.delay", side_effect=ConnectionRefusedError("no redis")
        ):
            job = create_extraction_job(conversation=conversation, through_message=message)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING  # untouched, not crashed

    @pytest.mark.django_db(transaction=True)
    def test_eager_task_body_exception_still_propagates(self, settings):
        """The negative-pair test: unlike a broker publish failure, an
        eager-mode task's own exception must NOT be swallowed by dispatch —
        otherwise CELERY_TASK_EAGER_PROPAGATES would be silently
        ineffective for anyone relying on it in a test."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        alice = register_user(email="eagerbug@test.dev", password=PASSWORD, name="D")
        workspace = Workspace.objects.get(owner=alice)
        conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        message = Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="hi",
            status=Message.Status.COMPLETE,
        )
        with mock.patch("memory.tasks.claim_extraction_job", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                create_extraction_job(conversation=conversation, through_message=message)


class TestSecretSafeErrorMessage:
    def test_unexpected_exception_text_is_never_persisted(
        self, conversation, fake_provider, extraction_prompt
    ):
        job = make_job(conversation)
        with mock.patch(
            "memory.tasks._process_claimed_job",
            side_effect=RuntimeError("dsn=postgres://user:hunter2@host/db"),
        ):
            run_extraction_job(job.id)
        job.refresh_from_db()
        assert job.status == MemoryExtractionJob.Status.PENDING
        assert "hunter2" not in job.error_message
        assert "postgres://" not in job.error_message
        assert job.error_type == "unexpected"
