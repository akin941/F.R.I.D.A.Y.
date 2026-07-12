import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from agents.execution import advance_step
from agents.services import create_plan
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
