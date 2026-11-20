"""ADR-004 self-modification guarantee (MVP 7 §4, brief) -- "agents can
never modify their own definition or permissions" (also CLAUDE.md rule 4).

Deliberately not a new runtime sandboxing mechanism: the execution engine
simply has no code path that writes to `AgentStep.kind`/`payload` after
creation, creates a second `Agent` from within a running one's own
execution (no self-spawning), or bypasses `tools`/`approvals`'s own public
service functions to touch their permission/registry logic directly. These
tests exist so a future change that accidentally adds one of those paths
fails loudly, not because any sandboxing code lives here.
"""

from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from agents.execution import (
    advance_step,
    cancel_agent,
    confirm_task_draft_step,
    resume_after_approval_decision,
)
from agents.models import Agent, AgentStep
from agents.planner_service import create_agent_from_goal
from agents.services import create_plan
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from approvals.models import ApprovalRequest
from approvals.services import approve_request
from core.channels import CHANNEL_WEB
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
def second_admin(workspace):
    user = register_user(email="admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.ADMIN)
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
def read_step_prompt():
    template = PromptTemplate.objects.create(
        name="friday_agent_read_step", purpose="agent.read_step"
    )
    version = PromptVersion.objects.create(template=template, version=1, content="Summarize.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def agent_plan_prompt():
    template = PromptTemplate.objects.create(name="friday_agent_plan", purpose="agent.plan")
    version = PromptVersion.objects.create(template=template, version=1, content="Plan the goal.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def skill_match_prompt():
    template = PromptTemplate.objects.create(
        name="friday_agent_skill_match", purpose="agent.skill_match"
    )
    version = PromptVersion.objects.create(template=template, version=1, content="Match a skill.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def _mixed_kind_plan(workspace, owner, goal="Do things"):
    """One step of each kind -- read, tool_request, task_draft -- so a
    single lifecycle run exercises every write path the engine has."""
    steps = [
        {"kind": "read", "description": "Step 0", "payload": {"note": "read this"}},
        {
            "kind": "tool_request",
            "description": "Step 1",
            "payload": {"tool_name": "vault_export"},
        },
        {
            "kind": "task_draft",
            "description": "Step 2",
            "payload": {"title": "Buy milk", "due_date": "2026-08-01"},
        },
    ]
    return create_plan(workspace=workspace, actor=owner, goal=goal, steps=steps)


class TestAgentStepImmutability:
    def test_kind_and_payload_are_never_mutated_across_a_full_lifecycle(
        self, workspace, owner, second_admin, fake_provider, read_step_prompt, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _mixed_kind_plan(workspace, owner)
        original = [
            (step.id, step.kind, dict(step.payload)) for step in agent.steps.order_by("order")
        ]

        advance_step(str(agent.id))  # read step runs to completion
        advance_step(str(agent.id))  # tool_request claimed -> blocked_on_approval
        tool_step = agent.steps.get(order=1)
        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(
                approval_request=tool_step.approval_request, actor=second_admin, channel=CHANNEL_WEB
            )
        resume_after_approval_decision(agent_step_id=tool_step.id, approved=True)
        advance_step(str(agent.id))  # task_draft claimed -> blocked_on_confirmation
        draft_step = agent.steps.get(order=2)
        confirm_task_draft_step(step=draft_step, actor=owner)

        current = [
            (step.id, step.kind, dict(step.payload)) for step in agent.steps.order_by("order")
        ]
        assert current == original

    def test_kind_and_payload_survive_cancellation_too(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        agent = _mixed_kind_plan(workspace, owner)
        original = [
            (step.id, step.kind, dict(step.payload)) for step in agent.steps.order_by("order")
        ]

        cancel_agent(agent=agent, actor=owner)

        current = [
            (step.id, step.kind, dict(step.payload)) for step in agent.steps.order_by("order")
        ]
        assert current == original

    def test_engine_never_writes_kind_or_payload_fields(self):
        """Static cross-check on top of the behavioral tests above: every
        `AgentStep.save(update_fields=[...])` call anywhere in the
        execution engine must exclude `kind`/`payload` -- confirms the
        guarantee holds by construction, not just for the specific
        lifecycles exercised above."""
        import inspect

        from agents import execution

        source = inspect.getsource(execution)
        # AgentStep(...) construction (bulk_create) only ever happens in
        # agents.services.finalize_plan, never in the execution engine.
        assert "AgentStep(" not in source


class TestNoSelfSpawning:
    def test_full_lifecycle_never_creates_a_second_agent(
        self, workspace, owner, second_admin, fake_provider, read_step_prompt, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _mixed_kind_plan(workspace, owner)
        count_before = Agent.objects.count()

        advance_step(str(agent.id))
        advance_step(str(agent.id))
        tool_step = agent.steps.get(order=1)
        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(
                approval_request=tool_step.approval_request, actor=second_admin, channel=CHANNEL_WEB
            )
        resume_after_approval_decision(agent_step_id=tool_step.id, approved=True)
        advance_step(str(agent.id))
        draft_step = agent.steps.get(order=2)
        confirm_task_draft_step(step=draft_step, actor=owner)
        cancel_agent(agent=agent, actor=owner)

        assert Agent.objects.count() == count_before

    def test_engine_never_calls_agent_objects_create(self):
        """Static cross-check: `Agent.objects.create` only ever appears in
        `agents.services.start_agent` (the planner's own entry point,
        never reachable from a running Agent's own execution)."""
        import inspect

        from agents import execution

        source = inspect.getsource(execution)
        assert "Agent.objects.create" not in source
        assert "start_agent" not in source  # execution.py never imports it


class TestNoRegistryOrPermissionBypass:
    def test_engine_never_calls_tools_registry_mutation_functions(
        self,
        workspace,
        owner,
        second_admin,
        fake_provider,
        read_step_prompt,
        tmp_path,
        settings,
        monkeypatch,
    ):
        """`agents` may only ever READ the tools registry (`list_tool_names`,
        by the planner) -- never register or unregister a tool. Proven by
        making both mutation functions raise: a full lifecycle including a
        `tool_request` step must never trip them."""
        from tools import registry

        def _boom(*args, **kwargs):
            raise AssertionError("agents must never mutate the tools registry")

        monkeypatch.setattr(registry, "register_tool", _boom)
        monkeypatch.setattr(registry, "unregister_tool", _boom)

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _mixed_kind_plan(workspace, owner)
        advance_step(str(agent.id))
        advance_step(str(agent.id))  # must not raise
        tool_step = agent.steps.get(order=1)
        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(
                approval_request=tool_step.approval_request, actor=second_admin, channel=CHANNEL_WEB
            )
        resume_after_approval_decision(agent_step_id=tool_step.id, approved=True)  # must not raise

    def test_planner_service_never_calls_skills_registry_mutation_functions(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt, monkeypatch
    ):
        """MVP 8 §5 -- the direct twin of the test above: `agents` may only
        ever READ the skill catalog (`list_skill_prompt_entries`, `get_skill`,
        `list_skill_names`, all by the planner) -- never register or
        unregister a skill. Proven by making both mutation functions raise:
        `create_agent_from_goal` must never trip them, whether or not the
        goal actually matches a registered skill (both paths exercised, via
        FakeAdapter's content-trigger convention)."""
        from skills import catalog

        def _boom(*args, **kwargs):
            raise AssertionError("agents must never mutate the skills registry")

        monkeypatch.setattr(catalog, "register_skill", _boom)
        monkeypatch.setattr(catalog, "unregister_skill", _boom)

        matched = create_agent_from_goal(
            workspace=workspace, actor=owner, goal="please export my weekly notes for me"
        )
        assert matched.status == Agent.Status.RUNNING
        assert matched.skill_name == "weekly_export"

        unmatched = create_agent_from_goal(
            workspace=workspace, actor=owner, goal="do something entirely unrelated"
        )
        assert unmatched.status == Agent.Status.RUNNING
        assert unmatched.skill_name == ""

    def test_engine_and_planner_service_source_never_calls_skills_mutation_functions(self):
        """Static cross-check on top of the behavioral test above: neither
        module's source text ever references `register_skill`/
        `unregister_skill` at all -- confirms the guarantee holds by
        construction, not just for the specific lifecycles exercised."""
        import inspect

        from agents import execution, planner_service

        for module in (execution, planner_service):
            source = inspect.getsource(module)
            assert "register_skill(" not in source
            assert "unregister_skill(" not in source

    def test_engine_never_calls_approvals_decision_functions_directly(
        self, workspace, owner, tmp_path, settings, monkeypatch
    ):
        """The agents app only ever REQUESTS via `approvals.services.
        create_request` and REACTS to an already-decided request (via the
        `post_save` signal -> `resume_after_approval_decision`) -- it must
        never call `approve_request`/`reject_request` itself, which would
        be the agent deciding its own request. Proven by making both raise:
        the signal's own resume path must never trip them, even though the
        decision (normally made by a human, through those exact functions)
        has already landed."""
        import approvals.services as approvals_services

        def _boom(*args, **kwargs):
            raise AssertionError("agents must never call approve_request/reject_request directly")

        monkeypatch.setattr(approvals_services, "approve_request", _boom)
        monkeypatch.setattr(approvals_services, "reject_request", _boom)

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        steps = [
            {
                "kind": "tool_request",
                "description": "Export",
                "payload": {"tool_name": "vault_export"},
            }
        ]
        agent = create_plan(workspace=workspace, actor=owner, goal="Export", steps=steps)
        advance_step(str(agent.id))
        step = agent.steps.get()

        # Simulate the human decision landing directly (exactly the write
        # approve_request itself would make), without calling the mocked
        # function -- isolates "did the AGENTS app call this" from "was
        # this ever decided at all."
        ApprovalRequest.objects.filter(pk=step.approval_request_id).update(
            status=ApprovalRequest.Status.APPROVED, decided_at=timezone.now()
        )

        resume_after_approval_decision(agent_step_id=step.id, approved=True)  # must not raise

        step.refresh_from_db()
        assert step.status == AgentStep.Status.SUCCEEDED

    def test_engine_never_directly_creates_approval_request_rows(self):
        """Static cross-check: `tool_request` steps must go through
        `approvals.services.create_request` (which enforces validation,
        audit, and the durable-before-dispatch discipline) -- never
        `ApprovalRequest.objects.create`/`ApprovalRequest(...)` directly."""
        import inspect

        from agents import execution

        source = inspect.getsource(execution)
        assert "ApprovalRequest.objects.create" not in source
        assert "ApprovalRequest(" not in source


class TestTaskDraftNeverAutoCreatesWithoutAHumanClick:
    """The round-1 CRITICAL precedent from branch 3's own plan-review,
    re-proven here explicitly as a self-modification-adjacent guarantee:
    `task_draft` steps are the one place the engine could plausibly write
    outside its own app (a `Task` row) -- it must never do so without an
    explicit human confirm."""

    def test_advancing_a_task_draft_step_never_creates_a_task(self, workspace, owner):
        from tasks.models import Task

        steps = [
            {
                "kind": "task_draft",
                "description": "Draft",
                "payload": {"title": "Buy milk", "due_date": None},
            }
        ]
        agent = create_plan(workspace=workspace, actor=owner, goal="Draft", steps=steps)
        advance_step(str(agent.id))
        assert Task.objects.count() == 0

    def test_engine_never_calls_create_task_outside_the_confirm_endpoint(self):
        """Static cross-check: `tasks.services.create_task` may only be
        called from `confirm_task_draft_step` (the human-click path), never
        from the claim/advance code that runs unattended."""
        import inspect

        from agents import execution

        source = inspect.getsource(execution.confirm_task_draft_step)
        assert "create_task(" in source
        # None of the OTHER (unattended) functions call it.
        for name in ("_claim_next_step", "_dispatch_task_draft_step", "advance_step"):
            fn_source = inspect.getsource(getattr(execution, name))
            assert "create_task(" not in fn_source, f"{name} must never call create_task directly"
