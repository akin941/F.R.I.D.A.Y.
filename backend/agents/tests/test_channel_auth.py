"""PAT/CLI-channel behavior on the agent goal-entry/list/detail/cancel
endpoints (MVP 9 §1/§2): scoped_for_actor() workspace fixation."""

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from agents.models import Agent
from agents.services import create_plan
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from workspaces.models import Workspace, WorkspaceMember

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
def other_workspace(owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    ws = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=ws, user=owner, role=WorkspaceMember.Role.MEMBER)
    return ws


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def agent_plan_prompt():
    template = PromptTemplate.objects.create(name="friday_agent_plan", purpose="agent.plan")
    version = PromptVersion.objects.create(template=template, version=1, content="Plan the goal.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def pat(owner, workspace):
    raw = "jpat_test-raw-token-0000000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="test cli", token_hash=hash_token(raw)
    )
    return raw


def pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


def test_pat_list_agents_without_workspace_param_succeeds_scoped_to_bound_workspace(
    pat, owner, workspace, other_workspace
):
    create_plan(workspace=workspace, actor=owner, goal="in bound workspace", steps=[])
    create_plan(workspace=other_workspace, actor=owner, goal="in other workspace", steps=[])

    response = pat_client(pat).get("/api/v1/agents")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["goal"] == "in bound workspace"


def test_pat_create_agent_with_mismatched_body_workspace_is_forced_to_bound(
    pat, owner, workspace, other_workspace, fake_provider, agent_plan_prompt
):
    response = pat_client(pat).post(
        "/api/v1/agents",
        {"workspace": str(other_workspace.id), "goal": "do something"},
        format="json",
    )
    assert response.status_code == 201
    assert str(response.data["workspace"]) == str(workspace.id)


def test_pat_detail_for_agent_in_a_different_workspace_returns_404(pat, owner, other_workspace):
    """Proves the round-2 P1 fix's by-id-lookup coverage: the underlying
    user IS a member of other_workspace, but the PAT is bound elsewhere."""
    agent = create_plan(workspace=other_workspace, actor=owner, goal="elsewhere", steps=[])
    response = pat_client(pat).get(f"/api/v1/agents/{agent.id}")
    assert response.status_code == 404


_ONE_STEP = [{"kind": "read", "description": "Step", "payload": {}}]


def test_pat_cancel_for_agent_in_a_different_workspace_returns_404(pat, owner, other_workspace):
    agent = create_plan(workspace=other_workspace, actor=owner, goal="elsewhere", steps=_ONE_STEP)
    response = pat_client(pat).post(f"/api/v1/agents/{agent.id}/cancel")
    assert response.status_code == 404
    agent.refresh_from_db()
    assert agent.status != Agent.Status.CANCELLED


def test_pat_can_cancel_agent_in_its_own_bound_workspace(pat, owner, workspace):
    agent = create_plan(workspace=workspace, actor=owner, goal="cancel me", steps=_ONE_STEP)
    response = pat_client(pat).post(f"/api/v1/agents/{agent.id}/cancel")
    assert response.status_code == 200
    agent.refresh_from_db()
    assert agent.status == Agent.Status.CANCELLED
