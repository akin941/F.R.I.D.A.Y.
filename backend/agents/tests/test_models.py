import pytest
from django.db import IntegrityError

from accounts.services import register_user
from agents.models import Agent, AgentStep
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def test_agent_defaults(workspace, owner):
    agent = Agent.objects.create(workspace=workspace, goal="Do the thing", created_by=owner)
    assert agent.status == Agent.Status.PLANNING
    assert agent.gateway_calls_used == 0
    assert str(agent) == f"agent {agent.id} (planning)"


def test_agent_workspace_scoping(owner, workspace):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    Agent.objects.create(workspace=workspace, goal="Mine", created_by=owner)
    Agent.objects.create(workspace=other_workspace, goal="Theirs", created_by=stranger)

    visible = Agent.objects.for_actor(owner)
    assert visible.count() == 1
    assert visible.first().goal == "Mine"


def test_agent_step_defaults_and_ordering(workspace, owner):
    agent = Agent.objects.create(workspace=workspace, goal="Goal", created_by=owner)
    step = AgentStep.objects.create(agent=agent, order=0, kind=AgentStep.Kind.READ)
    assert step.status == AgentStep.Status.PENDING
    assert step.payload == {}
    assert step.result == {}
    assert step.approval_request_id is None
    assert str(step) == f"agent step {step.id} (read, pending)"


def test_agent_step_unique_order_per_agent(workspace, owner):
    agent = Agent.objects.create(workspace=workspace, goal="Goal", created_by=owner)
    AgentStep.objects.create(agent=agent, order=0, kind=AgentStep.Kind.READ)
    with pytest.raises(IntegrityError):
        AgentStep.objects.create(agent=agent, order=0, kind=AgentStep.Kind.TASK_DRAFT)


def test_agent_step_for_actor_scopes_through_agent_workspace(owner, workspace):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    mine = Agent.objects.create(workspace=workspace, goal="Mine", created_by=owner)
    theirs = Agent.objects.create(workspace=other_workspace, goal="Theirs", created_by=stranger)
    AgentStep.objects.create(agent=mine, order=0, kind=AgentStep.Kind.READ)
    AgentStep.objects.create(agent=theirs, order=0, kind=AgentStep.Kind.READ)

    visible = AgentStep.objects.for_actor(owner)
    assert visible.count() == 1
    assert visible.first().agent_id == mine.id

    scoped_to_other_workspace = AgentStep.objects.for_actor(owner, workspace=other_workspace)
    assert scoped_to_other_workspace.count() == 0
