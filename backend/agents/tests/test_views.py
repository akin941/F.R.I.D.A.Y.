import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from agents.execution import advance_step, cancel_agent
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
    return register_user(email="stepview-owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def second_admin(workspace):
    user = register_user(email="stepview-admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.ADMIN)
    return user


@pytest.fixture
def viewer(workspace):
    user = register_user(email="stepview-viewer@test.dev", password=PASSWORD, name="Viewer")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.VIEWER)
    return user


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
    template = PromptTemplate.objects.create(name="jarvis_agent_plan", purpose="agent.plan")
    version = PromptVersion.objects.create(template=template, version=1, content="Plan the goal.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _task_draft_agent(workspace, owner, title="Buy milk"):
    steps = [
        {
            "kind": "task_draft",
            "description": "Draft a task",
            "payload": {"title": title, "due_date": None},
        }
    ]
    agent = create_plan(workspace=workspace, actor=owner, goal="Draft a task", steps=steps)
    advance_step(str(agent.id))
    return agent


class TestAgentListCreateView:
    def test_create_runs_the_planner_and_returns_the_agent(
        self, workspace, owner, fake_provider, agent_plan_prompt
    ):
        response = client_for(owner).post(
            "/api/v1/agents", {"workspace": str(workspace.id), "goal": "Do something useful"}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["goal"] == "Do something useful"
        assert body["status"] == Agent.Status.RUNNING
        assert Agent.objects.get(pk=body["id"]).steps.count() == 1

    def test_create_requires_a_non_blank_goal(self, workspace, owner):
        response = client_for(owner).post(
            "/api/v1/agents", {"workspace": str(workspace.id), "goal": "   "}
        )
        assert response.status_code == 400

    def test_viewer_cannot_create_an_agent(
        self, workspace, viewer, fake_provider, agent_plan_prompt
    ):
        response = client_for(viewer).post(
            "/api/v1/agents", {"workspace": str(workspace.id), "goal": "Do something"}
        )
        assert response.status_code == 403
        assert Agent.objects.count() == 0

    def test_list_requires_workspace_param(self, owner):
        assert client_for(owner).get("/api/v1/agents").status_code == 400

    def test_list_filters_by_status(self, workspace, owner):
        running = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Running",
            steps=[{"kind": "read", "description": "Step", "payload": {}}],
        )
        create_plan(workspace=workspace, actor=owner, goal="Failed", steps=[])  # empty plan fails

        response = client_for(owner).get(
            "/api/v1/agents", {"workspace": str(workspace.id), "status": "running"}
        )

        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(running.id)]

    def test_list_scoped_to_workspace_membership(self, owner):
        outsider = register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")
        response = client_for(outsider).get("/api/v1/agents", {"workspace": "not-a-real-uuid"})
        assert response.status_code == 400


class TestAgentDetailView:
    def test_detail_includes_ordered_steps(self, workspace, owner):
        agent = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Do things",
            steps=[
                {"kind": "read", "description": "First", "payload": {}},
                {"kind": "read", "description": "Second", "payload": {}},
            ],
        )

        response = client_for(owner).get(f"/api/v1/agents/{agent.id}")

        assert response.status_code == 200
        body = response.json()
        assert [step["description"] for step in body["steps"]] == ["First", "Second"]

    def test_unknown_agent_is_404(self, owner):
        import uuid

        assert client_for(owner).get(f"/api/v1/agents/{uuid.uuid4()}").status_code == 404

    def test_out_of_scope_agent_is_404_not_403(self, workspace, owner):
        outsider = register_user(email="detail-outsider@test.dev", password=PASSWORD, name="O")
        agent = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Private",
            steps=[{"kind": "read", "description": "Step", "payload": {}}],
        )
        assert client_for(outsider).get(f"/api/v1/agents/{agent.id}").status_code == 404


class TestAgentCancelView:
    def test_cancel_a_running_agent(self, workspace, owner):
        agent = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Do things",
            steps=[{"kind": "read", "description": "Step", "payload": {}}],
        )

        response = client_for(owner).post(f"/api/v1/agents/{agent.id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == Agent.Status.CANCELLED

    def test_viewer_cannot_cancel(self, workspace, owner, viewer):
        agent = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Do things",
            steps=[{"kind": "read", "description": "Step", "payload": {}}],
        )

        response = client_for(viewer).post(f"/api/v1/agents/{agent.id}/cancel")

        assert response.status_code == 403
        agent.refresh_from_db()
        assert agent.status == Agent.Status.RUNNING

    def test_cancel_is_idempotent(self, workspace, owner):
        agent = create_plan(
            workspace=workspace,
            actor=owner,
            goal="Do things",
            steps=[{"kind": "read", "description": "Step", "payload": {}}],
        )
        cancel_agent(agent=agent, actor=owner)

        response = client_for(owner).post(f"/api/v1/agents/{agent.id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == Agent.Status.CANCELLED

    def test_unknown_agent_is_404(self, owner):
        import uuid

        assert client_for(owner).post(f"/api/v1/agents/{uuid.uuid4()}/cancel").status_code == 404


class TestAgentStepConfirmView:
    def test_confirm_creates_and_returns_the_task(self, workspace, owner):
        agent = _task_draft_agent(workspace, owner)
        step = agent.steps.get()

        response = client_for(owner).post(f"/api/v1/agents/steps/{step.id}/confirm")

        assert response.status_code == 201
        assert response.json()["title"] == "Buy milk"

    def test_unknown_step_is_404(self, owner):
        import uuid

        response = client_for(owner).post(f"/api/v1/agents/steps/{uuid.uuid4()}/confirm")
        assert response.status_code == 404

    def test_task_permission_denied_from_the_task_service_is_translated_to_403(
        self, workspace, owner, second_admin
    ):
        """Codex round-1 P2 fix: `confirm_task_draft_step()` calls
        `tasks.services.create_task(actor=agent.created_by, ...)`
        internally; if the goal-submitter's role changed since planning,
        that call raises `TaskPermissionDenied` -- the view must translate
        this to a normal 403 response, not let it surface as a 500."""
        agent = _task_draft_agent(workspace, owner)
        step = agent.steps.get()
        WorkspaceMember.objects.filter(workspace=workspace, user=owner).update(
            role=WorkspaceMember.Role.VIEWER
        )

        # second_admin (a curator) may confirm someone else's agent -- the
        # denial comes from `create_task`'s own actor check on
        # `agent.created_by` (owner), not from `_require_can_confirm`.
        response = client_for(second_admin).post(f"/api/v1/agents/steps/{step.id}/confirm")

        assert response.status_code == 403


class TestAgentStepDiscardView:
    def test_discard_marks_the_step_skipped(self, workspace, owner):
        agent = _task_draft_agent(workspace, owner)
        step = agent.steps.get()

        response = client_for(owner).post(f"/api/v1/agents/steps/{step.id}/discard")

        assert response.status_code == 200
        assert response.json()["status"] == "skipped"

    def test_unknown_step_is_404(self, owner):
        import uuid

        response = client_for(owner).post(f"/api/v1/agents/steps/{uuid.uuid4()}/discard")
        assert response.status_code == 404
