import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from accounts.services import register_user
from agents.models import Agent, AgentStep
from agents.planner import PlanParseError
from agents.planner_service import create_agent_from_goal
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
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


@pytest.fixture
def agent_plan_prompt():
    template = PromptTemplate.objects.create(name="jarvis_agent_plan", purpose="agent.plan")
    version = PromptVersion.objects.create(template=template, version=1, content="Plan the goal.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


class TestCreateAgentFromGoal:
    def test_missing_prompt_template_raises_improperly_configured(self, workspace, owner):
        with pytest.raises(ImproperlyConfigured):
            create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")
        # Codex round-2 LOW fix: config is validated before any Agent row
        # is created, so a misconfigured deployment never leaves a stuck
        # `planning`-status row behind.
        assert Agent.objects.count() == 0

    def test_missing_active_version_raises_improperly_configured(self, workspace, owner):
        PromptTemplate.objects.create(name="jarvis_agent_plan", purpose="agent.plan")
        with pytest.raises(ImproperlyConfigured):
            create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")
        assert Agent.objects.count() == 0

    def test_fake_provider_creates_single_read_step_agent(
        self, workspace, owner, fake_provider, agent_plan_prompt
    ):
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.RUNNING
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1
        steps = list(agent.steps.order_by("order"))
        assert len(steps) == 1
        assert steps[0].kind == AgentStep.Kind.READ
        assert steps[0].description == "Fake plan read step"

    def test_no_provider_gateway_error_fails_agent_with_planner_error(
        self, workspace, owner, agent_plan_prompt
    ):
        # No ModelProvider row registered at all -> GatewayError("no_provider").
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.PLANNER_ERROR
        assert agent.steps.count() == 0
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1
        assert not ModelUsage.objects.get(purpose="agent.plan").success

    def test_malformed_planner_output_fails_agent_with_planner_error(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        import agents.planner_service as planner_service_module

        def _boom(raw_text):
            raise PlanParseError("boom")

        monkeypatch.setattr(planner_service_module, "parse_plan", _boom)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.PLANNER_ERROR
        assert agent.steps.count() == 0
        # The gateway call itself succeeded (FakeAdapter returned a response);
        # only our own post-hoc schema parsing failed.
        assert ModelUsage.objects.get(purpose="agent.plan").success

    def test_unregistered_tool_name_fails_agent_with_planner_error(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        import agents.planner_service as planner_service_module

        def _unknown_tool(raw_text):
            return [
                {
                    "kind": "tool_request",
                    "description": "x",
                    "payload": {"tool_name": "definitely_not_a_tool"},
                }
            ]

        monkeypatch.setattr(planner_service_module, "parse_plan", _unknown_tool)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.PLANNER_ERROR
        assert agent.steps.count() == 0

    def test_registered_tool_name_creates_the_step(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        import agents.planner_service as planner_service_module

        def _tool_request(raw_text):
            return [
                {
                    "kind": "tool_request",
                    "description": "Export the vault",
                    "payload": {"tool_name": "vault_export"},
                }
            ]

        monkeypatch.setattr(planner_service_module, "parse_plan", _tool_request)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Export my vault")

        assert agent.status == Agent.Status.RUNNING
        step = agent.steps.get()
        assert step.kind == AgentStep.Kind.TOOL_REQUEST
        assert step.payload == {"tool_name": "vault_export"}

    def test_valid_multi_step_plan_creates_ordered_steps(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        import agents.planner_service as planner_service_module

        def _multi_step(raw_text):
            return [
                {"kind": "read", "description": "First", "payload": {}},
                {
                    "kind": "task_draft",
                    "description": "Second",
                    "payload": {"title": "Buy milk", "due_date": None},
                },
            ]

        monkeypatch.setattr(planner_service_module, "parse_plan", _multi_step)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Multi-step goal")

        steps = list(agent.steps.order_by("order"))
        assert [s.order for s in steps] == [0, 1]
        assert steps[0].kind == AgentStep.Kind.READ
        assert steps[1].kind == AgentStep.Kind.TASK_DRAFT
        assert steps[1].payload == {"title": "Buy milk", "due_date": None}

    def test_valid_plan_with_all_three_kinds_creates_ordered_steps(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        import agents.planner_service as planner_service_module

        def _all_three_kinds(raw_text):
            return [
                {"kind": "read", "description": "Look something up", "payload": {}},
                {
                    "kind": "tool_request",
                    "description": "Export the vault",
                    "payload": {"tool_name": "vault_export"},
                },
                {
                    "kind": "task_draft",
                    "description": "Draft a task",
                    "payload": {"title": "Buy milk", "due_date": None},
                },
            ]

        monkeypatch.setattr(planner_service_module, "parse_plan", _all_three_kinds)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do everything")

        assert agent.status == Agent.Status.RUNNING
        steps = list(agent.steps.order_by("order"))
        assert [s.order for s in steps] == [0, 1, 2]
        assert [s.kind for s in steps] == [
            AgentStep.Kind.READ,
            AgentStep.Kind.TOOL_REQUEST,
            AgentStep.Kind.TASK_DRAFT,
        ]

    def test_agent_plan_prompt_lists_registered_tool_names(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        """The planning prompt's tool vocabulary is built from
        `tools.registry.list_tool_names()` at call time, not hardcoded --
        proves the vocabulary line actually reaches the gateway payload
        (Codex round-1 LOW fix)."""
        import agents.planner_service as planner_service_module
        from ai_core.adapters.base import AdapterResult
        from ai_core.gateway import InferenceGateway

        captured = {}

        def _capture_complete(self, **kwargs):
            captured.update(kwargs)
            return AdapterResult(
                text='{"steps": []}',
                input_tokens=1,
                output_tokens=1,
                finish_reason="stop",
            )

        monkeypatch.setattr(InferenceGateway, "complete", _capture_complete)
        planner_service_module.create_agent_from_goal(
            workspace=workspace, actor=owner, goal="Export my vault"
        )
        system_content = captured["messages"][0]["content"]
        assert "vault_export" in system_content

    @override_settings(AGENT_MAX_PLAN_STEPS=1)
    def test_over_bound_plan_fails_with_plan_too_large_not_planner_error(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        """Proves the orchestration preserves finalize_plan's own specific
        reason rather than overwriting every downstream failure to
        planner_error -- a well-shaped, over-bound plan is a different
        failure mode from a malformed/unresolvable one."""
        import agents.planner_service as planner_service_module

        def _two_steps(raw_text):
            return [
                {"kind": "read", "description": "First", "payload": {}},
                {"kind": "read", "description": "Second", "payload": {}},
            ]

        monkeypatch.setattr(planner_service_module, "parse_plan", _two_steps)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Too big")

        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.PLAN_TOO_LARGE
        assert agent.steps.count() == 0

    def test_empty_but_well_shaped_plan_fails_with_plan_empty_not_planner_error(
        self, workspace, owner, fake_provider, agent_plan_prompt, monkeypatch
    ):
        """A validly-shaped `{"steps": []}` response is a different failure
        mode from a parse error -- proves the two never get conflated."""
        import agents.planner_service as planner_service_module

        monkeypatch.setattr(planner_service_module, "parse_plan", lambda raw_text: [])
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Nothing to do")

        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.PLAN_EMPTY
        assert agent.steps.count() == 0
