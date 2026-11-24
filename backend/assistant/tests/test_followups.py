"""Phase 12 D11: the follow-up of an async read, against a controllable
async `ReadToolSpec` (no connector): the listener wakes only its own
follow-up, the composition is claimed once (never a second completion),
a still-running read is released, a lost notification is re-dispatched, a
lost composer ends `failed`, and every final state is final."""

from datetime import timedelta

import pytest
from django.utils import timezone

from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from assistant import followups
from assistant.models import Conversation, Message, PendingFollowUp
from tools import registry
from tools.registry import (
    ASYNC_OK,
    ASYNC_RUNNING,
    AsyncReadHandle,
    AsyncReadOutcome,
    ReadResult,
    ReadToolSpec,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

TOOL = "test_slow_read"
Status = PendingFollowUp.Status


class Outcomes:
    def __init__(self):
        self.status = ASYNC_OK
        self.text = "the result"

    def fetch(self, handle):
        if self.status != ASYNC_OK:
            return AsyncReadOutcome(status=self.status)
        return AsyncReadOutcome(status=ASYNC_OK, result=ReadResult(text=self.text))


@pytest.fixture
def outcomes():
    state = Outcomes()
    spec = ReadToolSpec(
        name=TOOL,
        description="A slow test read.",
        argument_serializer=None,
        data_class="firm",
        channels=frozenset({"web"}),
        execute=lambda **kw: ReadResult(text=""),
        is_available=lambda workspace: True,
        mode="async",
        start_async=lambda **kw: AsyncReadHandle(id="x", tool=TOOL),
        fetch_async=state.fetch,
        label="Yavaş okuma",
    )
    registry.register_read_tool(spec)
    yield state
    registry.unregister_read_tool(TOOL)


@pytest.fixture
def setup(db, settings):
    from accounts.services import register_user

    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    template, _ = PromptTemplate.objects.get_or_create(
        name=followups.PROMPT_NAME, defaults={"purpose": followups.PURPOSE}
    )
    if template.active_version_id is None:
        version = PromptVersion.objects.create(template=template, version=1, content="Tell.")
        template.active_version = version
        template.save(update_fields=["active_version"])
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    source = Message.objects.create(
        workspace=workspace, conversation=conversation, role="user", content="durum ne?"
    )
    return owner, workspace, conversation, source


def _pending(setup, handle_id="h1"):
    owner, workspace, conversation, source = setup
    return PendingFollowUp.objects.create(
        workspace=workspace,
        conversation=conversation,
        source_message=source,
        created_by=owner,
        channel="web",
        tool_name=TOOL,
        tool_label="Yavaş okuma",
        data_class="firm",
        handle_id=handle_id,
    )


def test_the_listener_is_registered_at_startup():
    assert followups.on_async_read_finished in registry._ASYNC_LISTENERS


def test_the_listener_wakes_only_its_own_followup(setup, outcomes, monkeypatch):
    dispatched = []
    monkeypatch.setattr(followups, "dispatch_compose", dispatched.append)
    mine = _pending(setup, "h1")
    _pending(setup, "h2")
    followups.on_async_read_finished(AsyncReadHandle(id="h1", tool=TOOL))
    followups.on_async_read_finished(AsyncReadHandle(id="h1", tool="other_tool"))
    followups.on_async_read_finished(AsyncReadHandle(id="nope", tool=TOOL))
    assert dispatched == [str(mine.pk)]


def test_one_completion_however_often_it_is_dispatched(setup, outcomes):
    followup = _pending(setup)
    assert followups.compose_followup(followup.pk) == Status.DONE
    assert followups.compose_followup(followup.pk) is None
    assert ModelUsage.objects.filter(purpose="chat.followup").count() == 1
    followup.refresh_from_db()
    reply = followup.followup_message
    assert reply.followup_of_id == followup.source_message_id
    assert reply.connector_reads[0]["mode"] == "async"


def test_a_concurrent_claim_makes_no_second_call(setup, outcomes):
    followup = _pending(setup)
    assert followups._claim(followup.pk) is not None
    assert followups.compose_followup(followup.pk) is None
    assert not ModelUsage.objects.exists()


def test_a_running_read_is_released_for_later(setup, outcomes):
    outcomes.status = ASYNC_RUNNING
    followup = _pending(setup)
    assert followups.compose_followup(followup.pk) is None
    followup.refresh_from_db()
    assert followup.status == Status.PENDING and followup.claimed_at is None
    assert not Message.objects.filter(followup_of__isnull=False).exists()


def test_recovery_redispatches_a_lost_notification(setup, outcomes, monkeypatch):
    dispatched = []
    monkeypatch.setattr(followups, "dispatch_compose", dispatched.append)
    followup = _pending(setup)
    PendingFollowUp.objects.filter(pk=followup.pk).update(
        created_at=timezone.now() - timedelta(minutes=5)
    )
    assert followups.recover_followups()["redispatched"] == 1
    assert dispatched == [str(followup.pk)]


def test_recovery_fails_a_lost_composer_without_a_second_call(setup, outcomes):
    followup = _pending(setup)
    PendingFollowUp.objects.filter(pk=followup.pk).update(
        claimed_at=timezone.now() - timedelta(hours=1)
    )
    assert followups.recover_followups()["worker_lost"] == 1
    followup.refresh_from_db()
    assert (followup.status, followup.outcome) == (Status.FAILED, "worker_lost")
    assert followups.compose_followup(followup.pk) is None
    assert not ModelUsage.objects.exists()


def test_a_missing_prompt_appends_a_template(setup, outcomes):
    PromptTemplate.objects.filter(name=followups.PROMPT_NAME).delete()
    followup = _pending(setup)
    assert followups.compose_followup(followup.pk) == Status.FAILED
    followup.refresh_from_db()
    assert followup.outcome == "no_prompt"
    assert followup.followup_message.content == "Yavaş okuma: sonuç alınamadı (no_prompt)."
    assert not ModelUsage.objects.exists()


def test_create_followup_dispatches_when_the_read_already_ended(setup, outcomes, monkeypatch):
    dispatched = []
    monkeypatch.setattr(followups, "dispatch_compose", dispatched.append)
    owner, workspace, conversation, source = setup
    followup = followups.create_followup(
        conversation=conversation,
        source_message=source,
        actor=owner,
        channel="web",
        handle=AsyncReadHandle(id="fast", tool=TOOL),
        tool_label="Yavaş okuma",
        source="test",
        data_class="firm",
    )
    assert dispatched == [str(followup.pk)]
    outcomes.status = ASYNC_RUNNING
    later = followups.create_followup(
        conversation=conversation,
        source_message=source,
        actor=owner,
        channel="web",
        handle=AsyncReadHandle(id="slow", tool=TOOL),
        tool_label="Yavaş okuma",
        source="test",
        data_class="firm",
    )
    assert str(later.pk) not in dispatched
