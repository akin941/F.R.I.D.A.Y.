"""Shared fixtures for the Phase 12 branch 3 wake-route tests: the chat
setup (FakeAdapter provider + the router prompts), an owner with a personal
and a firm workspace (with a non-owner member), and digest / notice rows.

Import the fixtures you need by name (`from assistant.tests.wake_fixtures
import ...  # noqa: F401`)."""

from datetime import time

import pytest

from accounts.tests.node_voice_helpers import CHAT_URL, make_owner, other_user, sse_events
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from proactive.models import DigestRun, ProactiveNotice, ProactiveRun, ProactiveSchedule
from workspaces.models import Workspace, WorkspaceMember


def _prompt(name, purpose, content):
    template = PromptTemplate.objects.create(name=name, purpose=purpose)
    version = PromptVersion.objects.create(template=template, version=1, content=content)
    template.active_version = version
    template.save(update_fields=["active_version"])


@pytest.fixture
def chat_setup(db, settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.AI_PURPOSE_TIERS = {}
    ModelProvider.objects.create(
        name="fake-chat",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )
    _prompt("friday_system", "chat", "You are Friday.")
    _prompt("friday_router_intent", "router.intent", "Classify the message.")
    _prompt("friday_router_task_draft", "router.task_draft", "Extract the task.")


@pytest.fixture
def owner(db):
    return make_owner()


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def member(db):
    return other_user()[0]


@pytest.fixture
def firm(owner, member):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    return firm


def make_digest(workspace, owner, *, text="Günaydın. Bugün 2 onay bekliyor.", created_at=None):
    schedule, _ = ProactiveSchedule.objects.get_or_create(
        workspace=workspace,
        kind=ProactiveSchedule.Kind.MORNING_DIGEST,
        defaults={"created_by": owner, "local_time": time(7, 45), "enabled": True},
    )
    run = ProactiveRun.objects.create(
        workspace=workspace,
        schedule=schedule,
        kind=ProactiveSchedule.Kind.MORNING_DIGEST,
        started_at=schedule.created_at,
    )
    digest = DigestRun.objects.create(
        workspace=workspace,
        run=run,
        schedule=schedule,
        sections=[],
        spoken_text=text,
        composed_by=DigestRun.ComposedBy.TEMPLATE,
        data_class="personal" if workspace.type == Workspace.Type.PERSONAL else "firm",
    )
    if created_at is not None:
        DigestRun.objects.filter(pk=digest.pk).update(created_at=created_at)
        digest.refresh_from_db()
    return digest


def make_notice(workspace, *, key="a", title="Yeni görev: Faturayı öde"):
    return ProactiveNotice.objects.create(
        workspace=workspace,
        kind="tasks.due_today",
        dedupe_key=key,
        title=title,
        count=1,
        link="/tasks",
        data_class="personal" if workspace.type == Workspace.Type.PERSONAL else "firm",
    )


def chat_turn(client, conversation, content, **body):
    response = client.post(
        CHAT_URL.format(conversation.pk), {"content": content, **body}, format="json"
    )
    assert response.status_code == 200, response.content
    return sse_events(response)


def reply_text(events):
    return "".join(e["text"] for e in events if e["type"] == "chunk")
