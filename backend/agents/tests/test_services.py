import pytest
from django.test import override_settings

from accounts.services import register_user
from agents.models import Agent, AgentStep
from agents.services import create_plan
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def _steps(count, kind=AgentStep.Kind.READ):
    return [{"kind": kind, "payload": {"n": i}} for i in range(count)]


def test_create_plan_within_bound_creates_steps_and_running_agent(workspace, owner):
    agent = create_plan(workspace=workspace, actor=owner, goal="Do things", steps=_steps(3))

    assert agent.status == Agent.Status.RUNNING
    steps = list(agent.steps.order_by("order"))
    assert [s.order for s in steps] == [0, 1, 2]
    assert all(s.status == AgentStep.Status.PENDING for s in steps)
    assert steps[1].payload == {"n": 1}


@override_settings(AGENT_MAX_PLAN_STEPS=2)
def test_create_plan_over_bound_rejects_whole_plan_and_fails_agent(workspace, owner):
    agent = create_plan(workspace=workspace, actor=owner, goal="Too big", steps=_steps(3))

    assert agent.status == Agent.Status.FAILED
    assert agent.steps.count() == 0


def test_create_plan_empty_steps_fails_agent(workspace, owner):
    agent = create_plan(workspace=workspace, actor=owner, goal="Nothing to do", steps=[])

    assert agent.status == Agent.Status.FAILED
    assert agent.steps.count() == 0


@override_settings(AGENT_MAX_PLAN_STEPS=3)
def test_create_plan_exactly_at_bound_succeeds(workspace, owner):
    agent = create_plan(workspace=workspace, actor=owner, goal="Right at the edge", steps=_steps(3))

    assert agent.status == Agent.Status.RUNNING
    assert agent.steps.count() == 3


def test_create_plan_always_creates_agent_row_even_on_rejection(workspace, owner):
    before = Agent.objects.count()
    create_plan(workspace=workspace, actor=owner, goal="Will fail", steps=[])
    assert Agent.objects.count() == before + 1
