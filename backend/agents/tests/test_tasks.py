import pytest

from accounts.services import register_user
from agents.models import Agent, AgentStep
from agents.services import create_plan
from agents.tasks import advance_agent_step
from ai_core.models import ModelProvider
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


def test_missing_prompt_config_resolves_the_claimed_step_instead_of_leaving_it_running(
    workspace, owner, fake_provider
):
    """Codex round-7 fix: no `read_step_prompt` fixture here, so
    `run_read_step` raises `ImproperlyConfigured` -- an unhandled exception
    that `execution.advance_step` itself never catches (only
    `SoftTimeLimitExceeded` is). `_claim_next_step` already committed the
    step to `running` before that happened; the task's own outer safety
    net must resolve it as failed immediately rather than leaving it
    `running` for the full recovery-scan grace window."""
    steps = [{"kind": "read", "description": "Step", "payload": {}}]
    agent = create_plan(workspace=workspace, actor=owner, goal="Do things", steps=steps)

    advance_agent_step(agent.id)  # must not raise

    step = agent.steps.get()
    assert step.status == AgentStep.Status.FAILED
    assert step.result == {"error": "unexpected_error"}
    agent.refresh_from_db()
    assert agent.consecutive_failures == 1
    assert agent.status == Agent.Status.RUNNING  # one more advance needed to complete
