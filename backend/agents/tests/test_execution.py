import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone

from accounts.services import register_user
from agents.execution import (
    AgentInvalidTransition,
    AgentPermissionDenied,
    advance_step,
    cancel_agent,
    confirm_task_draft_step,
    discard_task_draft_step,
    resume_after_approval_decision,
)
from agents.models import Agent, AgentStep
from agents.services import create_plan
from ai_core.models import ModelProvider, PromptTemplate, PromptVersion
from approvals.models import ApprovalRequest
from approvals.services import approve_request, reject_request
from tasks.models import Task
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
def member(workspace):
    user = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.MEMBER)
    return user


@pytest.fixture
def viewer(workspace):
    user = register_user(email="viewer@test.dev", password=PASSWORD, name="Viewer")
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
def read_step_prompt():
    template = PromptTemplate.objects.create(
        name="jarvis_agent_read_step", purpose="agent.read_step"
    )
    version = PromptVersion.objects.create(template=template, version=1, content="Summarize.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


def _read_step(count, workspace, owner, goal="Do things"):
    steps = [{"kind": "read", "description": f"Step {i}", "payload": {}} for i in range(count)]
    return create_plan(workspace=workspace, actor=owner, goal=goal, steps=steps)


def _tool_request_step(
    workspace, owner, tool_name="vault_export", goal="Export", skill_name="", skill_content_hash=""
):
    steps = [
        {
            "kind": "tool_request",
            "description": "Export the vault",
            "payload": {"tool_name": tool_name},
        }
    ]
    return create_plan(
        workspace=workspace,
        actor=owner,
        goal=goal,
        steps=steps,
        skill_name=skill_name,
        skill_content_hash=skill_content_hash,
    )


def _task_draft_step(workspace, owner, title="Buy milk", due_date=None, goal="Draft a task"):
    steps = [
        {
            "kind": "task_draft",
            "description": "Draft a task",
            "payload": {"title": title, "due_date": due_date},
        }
    ]
    return create_plan(workspace=workspace, actor=owner, goal=goal, steps=steps)


def _run_to_completion(agent_id, max_iterations=10):
    """`advance_step` processes exactly one step per call -- real multi-step
    chaining happens via `transaction.on_commit` callbacks that pytest-django
    never fires under the default (non-durable) test transaction wrapping
    (the same issue `tools/tests/test_export.py` solves by calling its core
    functions directly). This loop stands in for what the on_commit-
    triggered dispatch chain would have done in production."""
    for _ in range(max_iterations):
        advance_step(agent_id)
        if Agent.objects.get(pk=agent_id).status != Agent.Status.RUNNING:
            return
    raise AssertionError(f"agent {agent_id} never left RUNNING after {max_iterations} steps")


# --- read step happy path + budget brake ---


class TestReadStep:
    def test_single_read_step_plan_runs_to_completion(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        agent = _read_step(1, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED
        assert agent.gateway_calls_used == 2
        step = agent.steps.get()
        assert step.status == AgentStep.Status.SUCCEEDED
        assert "summary" in step.result

    def test_multi_read_step_plan_chains_through_all_steps(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        agent = _read_step(3, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED
        assert agent.gateway_calls_used == 6
        statuses = list(agent.steps.order_by("order").values_list("status", flat=True))
        assert statuses == [AgentStep.Status.SUCCEEDED] * 3

    def test_gateway_error_marks_step_failed_but_agent_completes(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        import agents.execution as execution_module
        from ai_core.exceptions import GatewayError

        def _boom(*args, **kwargs):
            raise GatewayError("provider_error")

        monkeypatch.setattr(execution_module.InferenceGateway, "complete", _boom)
        agent = _read_step(1, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        step = agent.steps.get()
        assert step.status == AgentStep.Status.FAILED
        # A single, non-consecutive-threshold failure does not halt the
        # whole Agent -- "completed" means "every step was processed,"
        # not "every step succeeded." Only AGENT_MAX_CONSECUTIVE_FAILURES
        # in a row halts it (see TestConsecutiveFailureBrake).
        assert agent.status == Agent.Status.COMPLETED
        assert agent.consecutive_failures == 1

    @override_settings(AGENT_MAX_GATEWAY_CALLS=1)
    def test_budget_exceeded_halts_before_starting_a_new_read_step(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        agent = _read_step(2, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        # Bounded one-step overshoot (Codex round-5 fix to the brief): the
        # first read step is allowed to run to completion even though it
        # pushes usage from 0 to 2, past the cap of 1 -- but the SECOND
        # read step is refused before it ever starts.
        assert agent.gateway_calls_used == 2
        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.GATEWAY_BUDGET_EXCEEDED
        steps = list(agent.steps.order_by("order"))
        assert steps[0].status == AgentStep.Status.SUCCEEDED
        assert steps[1].status == AgentStep.Status.PENDING  # never started

    def test_soft_time_limit_exceeded_marks_step_failed_with_timeout(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        import agents.execution as execution_module

        def _timeout(step_id):
            raise SoftTimeLimitExceeded()

        monkeypatch.setattr(execution_module, "run_read_step", _timeout)
        agent = _read_step(1, workspace, owner)
        advance_step(str(agent.id))

        step = agent.steps.get()
        assert step.status == AgentStep.Status.FAILED
        assert step.result == {"error": "timeout"}
        agent.refresh_from_db()
        assert agent.consecutive_failures == 1

    def test_duplicate_task_delivery_never_claims_past_an_in_flight_step(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        """Codex round-1 P2 fix: Celery is at-least-once, so
        `advance_agent_step` can be redelivered while a prior invocation's
        read step is still `running` (its gateway calls not yet finalized).
        A second, concurrent `_claim_next_step` call must no-op rather than
        skipping ahead to the next `pending` step or marking the Agent
        `completed` early."""
        from agents.execution import _claim_next_step

        agent = _read_step(2, workspace, owner)
        # Simulate the first delivery's claim having already marked step 0
        # `running` (about to call `run_read_step`, gateway call in flight).
        first_step = agent.steps.order_by("order").first()
        first_step.status = AgentStep.Status.RUNNING
        first_step.save(update_fields=["status", "updated_at"])

        claim = _claim_next_step(str(agent.id))

        assert claim.outcome == "halted"
        agent.refresh_from_db()
        assert agent.status == Agent.Status.RUNNING
        steps = list(agent.steps.order_by("order"))
        assert steps[0].status == AgentStep.Status.RUNNING  # untouched
        assert steps[1].status == AgentStep.Status.PENDING  # never claimed


# --- consecutive-failure brake ---


class TestConsecutiveFailureBrake:
    @override_settings(AGENT_MAX_CONSECUTIVE_FAILURES=2)
    def test_halts_after_threshold_consecutive_failures(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        import agents.execution as execution_module
        from ai_core.exceptions import GatewayError

        def _boom(*args, **kwargs):
            raise GatewayError("provider_error")

        monkeypatch.setattr(execution_module.InferenceGateway, "complete", _boom)
        agent = _read_step(3, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.CONSECUTIVE_FAILURES
        assert agent.consecutive_failures == 2
        steps = list(agent.steps.order_by("order"))
        assert steps[0].status == AgentStep.Status.FAILED
        assert steps[1].status == AgentStep.Status.FAILED
        assert steps[2].status == AgentStep.Status.PENDING  # never started, zero retries

    def test_success_resets_the_counter(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        import agents.execution as execution_module
        from ai_core.exceptions import GatewayError

        calls = {"n": 0}

        def _fail_once_then_succeed(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise GatewayError("provider_error")
            from ai_core.adapters.base import AdapterResult

            return AdapterResult(text="ok", input_tokens=1, output_tokens=1, finish_reason="stop")

        monkeypatch.setattr(execution_module.InferenceGateway, "complete", _fail_once_then_succeed)
        agent = _read_step(2, workspace, owner)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED
        assert agent.consecutive_failures == 0  # reset by the second step's success

    @override_settings(AGENT_MAX_CONSECUTIVE_FAILURES=1)
    def test_duplicate_delivery_cannot_bypass_the_halt_right_after_a_read_step_fails(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        """Codex round-3 fix: `_finalize_read_step` now applies the halt-
        or-continue decision in the SAME transaction as the step's own
        failure -- a "duplicate delivery" claim attempt immediately after
        must see the Agent already `failed`, never a stale `running` with
        the failure not yet counted (which would let it wrongly claim the
        next step, bypassing the brake)."""
        from agents import execution

        agent = _read_step(2, workspace, owner)
        claim = execution._claim_next_step(str(agent.id))
        assert claim.outcome == "read"
        execution._finalize_read_step(claim.step_id, success=False, result={"error": "boom"})

        duplicate_claim = execution._claim_next_step(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.CONSECUTIVE_FAILURES
        assert duplicate_claim.outcome == "halted"
        steps = list(agent.steps.order_by("order"))
        assert steps[1].status == AgentStep.Status.PENDING  # never claimed

    @override_settings(AGENT_MAX_CONSECUTIVE_FAILURES=1)
    def test_tool_request_creation_failure_also_decides_halt_inline(
        self, workspace, owner, monkeypatch
    ):
        """Same race, the OTHER path: `_dispatch_tool_request_step`'s own
        `ApprovalServiceError` branch must also decide halt-or-continue
        within `_claim_next_step`'s own already-open transaction, not a
        separate later call."""
        from agents import execution

        monkeypatch.setattr("approvals.services._resolve_tool", lambda name: None)
        agent = _tool_request_step(workspace, owner, tool_name="not_a_real_tool")

        claim = execution._claim_next_step(str(agent.id))

        assert claim.outcome == "step_failed"
        agent.refresh_from_db()
        assert agent.status == Agent.Status.FAILED
        assert agent.failure_reason == Agent.FailureReason.CONSECUTIVE_FAILURES


# --- tool_request dispatch + resume via signal ---


class TestToolRequestStep:
    def test_dispatch_creates_pending_approval_with_agent_creator_as_requester(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.BLOCKED_ON_APPROVAL
        step = agent.steps.get()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL
        assert step.approval_request is not None
        assert step.approval_request.status == ApprovalRequest.Status.PENDING
        assert step.approval_request.requested_by_id == owner.id

    def test_approving_resumes_and_completes_the_agent(
        self, workspace, owner, second_admin, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(approval_request=step.approval_request, actor=second_admin)
        # `agents/signals.py`'s own receiver defers the actual resume via
        # `transaction.on_commit`, which never fires under this test's
        # non-durable transaction -- call it directly, standing in for what
        # the signal would have triggered in production.
        resume_after_approval_decision(agent_step_id=step.id, approved=True)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        step.refresh_from_db()
        assert step.status == AgentStep.Status.SUCCEEDED
        assert agent.status == Agent.Status.COMPLETED

    def test_rejecting_fails_the_step_but_agent_still_completes(
        self, workspace, owner, second_admin, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        reject_request(approval_request=step.approval_request, actor=second_admin)
        # Same on_commit-deferred signal issue as the approve path above.
        resume_after_approval_decision(agent_step_id=step.id, approved=False)
        _run_to_completion(str(agent.id))

        agent.refresh_from_db()
        step.refresh_from_db()
        assert step.status == AgentStep.Status.FAILED
        assert agent.status == Agent.Status.COMPLETED
        assert agent.consecutive_failures == 1

    def test_step_resolution_and_halt_decision_commit_atomically(
        self, workspace, owner, tmp_path, settings, monkeypatch
    ):
        """Codex round-6 fix: `resume_after_approval_decision` used to be
        two separate transactions (resolve the step, then decide halt-or-
        continue) -- a process death in between would leave the step
        resolved but the Agent stuck `blocked_on_approval` forever, past
        even the recovery scan (which only looks for steps still
        `blocked_on_approval`). Proven here by forcing the halt-decision
        to raise: if the two writes are truly one transaction, the whole
        thing rolls back together, never a split state."""
        from agents import execution

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        def _boom(agent, *, step_succeeded):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(execution, "_decide_halt_or_continue", _boom)

        with pytest.raises(RuntimeError):
            execution.resume_after_approval_decision(agent_step_id=step.id, approved=True)

        step.refresh_from_db()
        agent.refresh_from_db()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL  # rolled back together
        assert agent.status == Agent.Status.BLOCKED_ON_APPROVAL

    def test_requester_cannot_approve_their_own_agents_request(
        self, workspace, owner, tmp_path, settings
    ):
        """`requested_by=Agent.created_by` (round-1 provenance fix) means
        the goal-submitter is blocked from deciding their own agent's
        request exactly as a human-typed request already blocks them."""
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        from approvals.services import ApprovalPermissionDenied

        with pytest.raises(ApprovalPermissionDenied):
            approve_request(approval_request=step.approval_request, actor=owner)

    def test_requester_cannot_approve_their_own_skill_originated_agents_request(
        self, workspace, owner, tmp_path, settings
    ):
        """MVP 8 §4: the exact same invariant re-proven against a
        skill-originated plan (`skill_name`/`skill_content_hash` set,
        mirroring what `agents.planner_service.create_agent_from_goal`
        would set on a real match) instead of an LLM-freeform one -- a
        skill's step template can propose a `tool_request` step, but it
        goes through the identical `_dispatch_tool_request_step` ->
        `approvals.services.create_request` path as any other, with no new
        mechanism and no bypass of the requester != decider check."""
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(
            workspace,
            owner,
            skill_name="weekly_export",
            skill_content_hash="deadbeef" * 8,
        )
        assert agent.skill_name == "weekly_export"
        advance_step(str(agent.id))
        step = agent.steps.get()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL

        from approvals.services import ApprovalPermissionDenied

        with pytest.raises(ApprovalPermissionDenied):
            approve_request(approval_request=step.approval_request, actor=owner)

    def test_unregistered_tool_name_fails_the_step_not_the_whole_task(
        self, workspace, owner, monkeypatch
    ):
        import agents.execution as execution_module

        def _resolve_none(name):
            return None

        monkeypatch.setattr("approvals.services._resolve_tool", _resolve_none)
        agent = _tool_request_step(workspace, owner, tool_name="not_a_real_tool")
        execution_module.advance_step(str(agent.id))

        step = agent.steps.get()
        assert step.status == AgentStep.Status.FAILED
        assert step.result == {"error": "approval_request_failed"}


# --- task_draft confirm/discard ---


class TestTaskDraftStep:
    def test_dispatch_pauses_at_blocked_on_confirmation(self, workspace, owner):
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.BLOCKED_ON_CONFIRMATION
        step = agent.steps.get()
        assert step.status == AgentStep.Status.BLOCKED_ON_CONFIRMATION

    def test_confirm_creates_ai_suggested_task_and_resumes(self, workspace, owner):
        agent = _task_draft_step(workspace, owner, title="Buy milk", due_date="2026-07-20")
        advance_step(str(agent.id))
        step = agent.steps.get()

        task = confirm_task_draft_step(step=step, actor=owner)
        # Stands in for the on_commit-triggered dispatch that would detect
        # "no more pending steps" in production (see `_run_to_completion`).
        _run_to_completion(str(agent.id))

        assert task.title == "Buy milk"
        assert task.source == Task.Source.AI_SUGGESTED
        assert task.due_date.isoformat() == "2026-07-20"
        step.refresh_from_db()
        agent.refresh_from_db()
        assert step.status == AgentStep.Status.SUCCEEDED
        assert step.result == {"task_id": str(task.id)}
        assert agent.status == Agent.Status.COMPLETED

    def test_confirm_never_lets_the_engine_auto_create_without_a_click(self, workspace, owner):
        """The round-1 CRITICAL fix this whole mechanism exists for: a
        task_draft step must never produce a Task row purely from the
        engine's own advance."""
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))
        assert Task.objects.count() == 0

    def test_discard_skips_without_creating_a_task_and_resumes(self, workspace, owner):
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        discard_task_draft_step(step=step, actor=owner)
        _run_to_completion(str(agent.id))

        assert Task.objects.count() == 0
        step.refresh_from_db()
        agent.refresh_from_db()
        assert step.status == AgentStep.Status.SKIPPED
        assert agent.status == Agent.Status.COMPLETED
        assert agent.consecutive_failures == 0  # a discard is not a failure

    def test_viewer_cannot_confirm(self, workspace, owner, viewer):
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        with pytest.raises(AgentPermissionDenied):
            confirm_task_draft_step(step=step, actor=viewer)

    def test_curator_can_confirm_someone_elses_agent(self, workspace, owner, second_admin):
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        task = confirm_task_draft_step(step=step, actor=second_admin)
        assert task.source == Task.Source.AI_SUGGESTED

    def test_confirming_an_already_resolved_step_raises_invalid_transition(self, workspace, owner):
        agent = _task_draft_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()
        confirm_task_draft_step(step=step, actor=owner)

        step.refresh_from_db()
        with pytest.raises(AgentInvalidTransition):
            confirm_task_draft_step(step=step, actor=owner)


# --- kill switch ---


class TestCancelAgent:
    def test_cancel_running_agent(self, workspace, owner):
        agent = _read_step(1, workspace, owner)
        cancelled = cancel_agent(agent=agent, actor=owner)
        assert cancelled.status == Agent.Status.CANCELLED

    def test_cancel_is_idempotent_on_terminal_agents(self, workspace, owner):
        agent = _read_step(1, workspace, owner)
        cancel_agent(agent=agent, actor=owner)
        again = cancel_agent(agent=agent, actor=owner)
        assert again.status == Agent.Status.CANCELLED

    def test_viewer_cannot_cancel(self, workspace, owner, viewer):
        agent = _read_step(1, workspace, owner)
        with pytest.raises(AgentPermissionDenied):
            cancel_agent(agent=agent, actor=viewer)

    def test_member_cannot_cancel_someone_elses_agent(self, workspace, owner, member):
        agent = _read_step(1, workspace, owner)
        with pytest.raises(AgentPermissionDenied):
            cancel_agent(agent=agent, actor=member)

    def test_curator_can_cancel_someone_elses_agent(self, workspace, owner, second_admin):
        agent = _read_step(1, workspace, owner)
        cancelled = cancel_agent(agent=agent, actor=second_admin)
        assert cancelled.status == Agent.Status.CANCELLED

    def test_cancelled_agent_never_advances_to_next_step(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        agent = _read_step(2, workspace, owner)
        cancel_agent(agent=agent, actor=owner)
        advance_step(str(agent.id))

        agent.refresh_from_db()
        assert agent.status == Agent.Status.CANCELLED
        steps = list(agent.steps.order_by("order"))
        assert steps[0].status == AgentStep.Status.PENDING
        assert steps[1].status == AgentStep.Status.PENDING

    def test_cancel_during_blocked_on_approval_then_approving_anyway_stays_cancelled(
        self, workspace, owner, second_admin, tmp_path, settings
    ):
        """The brief's own kill-switch test requirement: cancel while
        blocked_on_approval, then approve the now-orphaned ApprovalRequest
        anyway -- the Agent must stay cancelled and no further step runs."""
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        cancel_agent(agent=agent, actor=owner)
        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(approval_request=step.approval_request, actor=second_admin)

        agent.refresh_from_db()
        step.refresh_from_db()
        assert agent.status == Agent.Status.CANCELLED
        # The approval itself is still recorded as approved (that part of
        # the state is real and outside the agent's control) -- but the
        # step/agent must never reflect that as forward progress.
        assert step.approval_request.status == ApprovalRequest.Status.APPROVED
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL

    def test_resume_after_approval_is_a_safe_noop_once_agent_is_cancelled(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()
        cancel_agent(agent=agent, actor=owner)

        resume_after_approval_decision(agent_step_id=step.id, approved=True)

        agent.refresh_from_db()
        step.refresh_from_db()
        assert agent.status == Agent.Status.CANCELLED
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL


# --- recovery scan (Codex round-2 fix) ---


class TestRecoveryScan:
    def test_stale_running_step_past_grace_is_reclaimed_as_worker_lost(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        """Simulates the worker that claimed this read step dying (OOM-kill,
        hard `time_limit` SIGKILL) before it could ever call
        `_finalize_read_step` -- the step is stuck `running` forever without
        the recovery scan."""
        from agents import execution

        agent = _read_step(1, workspace, owner)
        claim = execution._claim_next_step(str(agent.id))
        assert claim.outcome == "read"
        AgentStep.objects.filter(agent=agent).update(
            updated_at=timezone.now() - timedelta(seconds=200)
        )

        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 1,
            "redispatched_agents": 0,
            "resumed_approvals": 0,
        }
        step = agent.steps.get()
        assert step.status == AgentStep.Status.FAILED
        assert step.result == {"error": "worker_lost"}
        agent.refresh_from_db()
        assert agent.consecutive_failures == 1
        # Same "no on_commit under this test transaction" gap as everywhere
        # else in this file -- one more direct call to reach `completed`.
        _run_to_completion(str(agent.id))
        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED

    def test_fresh_running_step_within_grace_is_left_alone(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        from agents import execution

        agent = _read_step(1, workspace, owner)
        claim = execution._claim_next_step(str(agent.id))
        assert claim.outcome == "read"

        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 0,
        }
        step = agent.steps.get()
        assert step.status == AgentStep.Status.RUNNING

    def test_stale_agent_with_no_running_step_is_redispatched(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        """The other stranding mode (Codex round-2 finding): the Agent's own
        on_commit dispatch to its next pending step was lost (e.g. broker
        down at commit time) -- nothing is `running`, so this must be
        re-dispatched rather than silently left `running` forever."""
        from agents import execution

        agent = _read_step(2, workspace, owner)
        advance_step(str(agent.id))  # completes step 0; step 1 stays pending
        agent.refresh_from_db()
        assert agent.status == Agent.Status.RUNNING
        Agent.objects.filter(pk=agent.id).update(updated_at=timezone.now() - timedelta(seconds=120))

        calls = []
        monkeypatch.setattr(
            execution, "dispatch_agent_execution", lambda agent_id: calls.append(agent_id)
        )
        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 1,
            "resumed_approvals": 0,
        }
        assert calls == [str(agent.id)]

    def test_fresh_agent_with_no_running_step_within_grace_is_left_alone(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        from agents import execution

        agent = _read_step(2, workspace, owner)
        advance_step(str(agent.id))
        agent.refresh_from_db()
        assert agent.status == Agent.Status.RUNNING

        calls = []
        monkeypatch.setattr(
            execution, "dispatch_agent_execution", lambda agent_id: calls.append(agent_id)
        )
        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 0,
        }
        assert calls == []

    def test_agent_with_a_still_running_step_is_never_redispatched_even_if_agent_row_is_stale(
        self, workspace, owner, fake_provider, read_step_prompt, monkeypatch
    ):
        """The `_claim_next_step` "read" branch only touches the STEP's own
        `updated_at`, never the Agent's -- so a perfectly healthy in-flight
        read step can leave the Agent row looking stale by this same grace
        window. Must never be redispatched while its step is genuinely
        still running (the exclude in `recover_stale_agent_execution`)."""
        from agents import execution

        agent = _read_step(1, workspace, owner)
        claim = execution._claim_next_step(str(agent.id))
        assert claim.outcome == "read"
        Agent.objects.filter(pk=agent.id).update(updated_at=timezone.now() - timedelta(seconds=120))

        calls = []
        monkeypatch.setattr(
            execution, "dispatch_agent_execution", lambda agent_id: calls.append(agent_id)
        )
        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 0,
        }
        assert calls == []
        step = agent.steps.get()
        assert step.status == AgentStep.Status.RUNNING  # untouched

    def test_finalize_after_reclaim_is_a_safe_noop(
        self, workspace, owner, fake_provider, read_step_prompt
    ):
        """Codex round-2 fencing fix: whichever of {the recovery scan, the
        original (possibly just-slow, not actually dead) worker} finalizes
        first wins -- the other's own `_finalize_read_step` call must
        become a safe no-op rather than overwriting the outcome or
        double-counting `gateway_calls_used`."""
        from agents import execution

        agent = _read_step(1, workspace, owner)
        claim = execution._claim_next_step(str(agent.id))
        step_id = claim.step_id

        finalized_by_recovery = execution._finalize_read_step(
            step_id, success=False, result={"error": "worker_lost"}
        )
        assert finalized_by_recovery is True

        finalized_by_late_worker = execution._finalize_read_step(
            step_id, success=True, result={"summary": "a late, stale result"}
        )
        assert finalized_by_late_worker is False

        step = AgentStep.objects.get(pk=step_id)
        assert step.status == AgentStep.Status.FAILED
        assert step.result == {"error": "worker_lost"}
        agent.refresh_from_db()
        assert agent.gateway_calls_used == 2  # counted once, not twice

    def test_stale_decided_approval_past_grace_is_resumed(
        self, workspace, owner, second_admin, tmp_path, settings
    ):
        """Codex round-4 fix: `agents.signals`' own resume is ALSO only an
        in-memory `transaction.on_commit` callback (never fires under this
        test's non-durable transaction, exactly like it wouldn't if the
        process died right after the ApprovalRequest's own commit) -- the
        recovery scan must reconcile a step still `blocked_on_approval`
        once its linked ApprovalRequest has actually been decided."""
        from agents import execution

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(approval_request=step.approval_request, actor=second_admin)
        step.approval_request.refresh_from_db()
        assert step.approval_request.status == ApprovalRequest.Status.APPROVED
        ApprovalRequest.objects.filter(pk=step.approval_request_id).update(
            decided_at=timezone.now() - timedelta(seconds=120)
        )
        step.refresh_from_db()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL  # not yet resumed

        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 1,
        }
        step.refresh_from_db()
        assert step.status == AgentStep.Status.SUCCEEDED
        _run_to_completion(str(agent.id))
        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED

    def test_freshly_decided_approval_within_grace_is_left_alone(
        self, workspace, owner, second_admin, tmp_path, settings
    ):
        from agents import execution

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        with mock.patch("tools.tasks.run_vault_export.delay"):
            approve_request(approval_request=step.approval_request, actor=second_admin)

        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 0,
        }
        step.refresh_from_db()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL

    def test_stale_expired_approval_is_also_resumed(self, workspace, owner, tmp_path, settings):
        """Codex round-5 fix: the expiry sweep (`approvals.tasks._expire_one`
        / `approvals.services._expire_if_stale`) never sets `decided_at` --
        an `expired` (not approved/rejected) request whose own resume
        callback was lost must still be reconciled, anchored on the
        ApprovalRequest's own `updated_at` instead."""
        from agents import execution
        from approvals.tasks import _expire_one

        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        agent = _tool_request_step(workspace, owner)
        advance_step(str(agent.id))
        step = agent.steps.get()

        _expire_one(step.approval_request_id)
        step.approval_request.refresh_from_db()
        assert step.approval_request.status == ApprovalRequest.Status.EXPIRED
        assert step.approval_request.decided_at is None
        ApprovalRequest.objects.filter(pk=step.approval_request_id).update(
            updated_at=timezone.now() - timedelta(seconds=120)
        )
        step.refresh_from_db()
        assert step.status == AgentStep.Status.BLOCKED_ON_APPROVAL  # not yet resumed

        result = execution.recover_stale_agent_execution()

        assert result == {
            "reclaimed_steps": 0,
            "redispatched_agents": 0,
            "resumed_approvals": 1,
        }
        step.refresh_from_db()
        assert step.status == AgentStep.Status.FAILED  # expired treated as not-approved
        _run_to_completion(str(agent.id))
        agent.refresh_from_db()
        assert agent.status == Agent.Status.COMPLETED


# --- real on_commit dispatch chain (the one genuinely durable-transaction test) ---


@pytest.mark.django_db(transaction=True)
def test_real_on_commit_dispatch_chain_runs_a_multi_step_plan_to_completion(settings):
    """Everything above calls `advance_step`/`execution` functions directly,
    bypassing the `transaction.on_commit`-triggered dispatch chain (which
    never fires under the default non-durable test transaction). This is
    the one dedicated test, mirroring `tools/tests/test_export.py`'s own
    single dedicated on_commit test, proving the REAL chain -- `create_plan`
    -> on_commit -> Celery (eager) -> `advance_step` -> on_commit -> ...
    -- actually walks a multi-step plan to completion with no manual
    `advance_step` calls at all."""
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.CELERY_TASK_ALWAYS_EAGER = True
    owner = register_user(email="oncommit-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )
    template = PromptTemplate.objects.create(
        name="jarvis_agent_read_step", purpose="agent.read_step"
    )
    version = PromptVersion.objects.create(template=template, version=1, content="Summarize.")
    template.active_version = version
    template.save(update_fields=["active_version"])

    steps = [{"kind": "read", "description": f"Step {i}", "payload": {}} for i in range(2)]
    agent = create_plan(workspace=workspace, actor=owner, goal="Chain", steps=steps)

    agent.refresh_from_db()
    assert agent.status == Agent.Status.COMPLETED
    statuses = list(agent.steps.order_by("order").values_list("status", flat=True))
    assert statuses == [AgentStep.Status.SUCCEEDED] * 2


@pytest.mark.django_db(transaction=True)
def test_cancel_racing_the_next_claim_never_lets_a_step_start_after_cancellation():
    """Kill-switch fencing at the START boundary (brief §3, brake #3),
    mirroring `tools.tests.test_export`'s
    `test_concurrent_exports_for_the_same_workspace_are_serialized` shape:
    one thread holds the `Agent` row lock mid-transaction while writing
    `cancelled` (simulating `cancel_agent` in flight), a second thread's
    REAL `_claim_next_step` races to start the next step. The racer's own
    `select_for_update` must block until the holder commits -- proven by
    asserting it only ever completes after the release fires -- so by the
    time it reads the row, cancellation has already landed and it must
    refuse to start a step rather than observing a stale `running` status.
    """
    from agents import execution

    owner = register_user(email="cancelrace-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    steps = [{"kind": "read", "description": "Step", "payload": {}}]
    # `conftest.py` sets `CELERY_TASK_ALWAYS_EAGER = True` for all tests, and
    # this test runs under `transaction=True` (real commits) -- without this
    # patch, `create_plan`'s own on_commit dispatch would synchronously run
    # the step to completion immediately, before the race below even starts.
    with mock.patch("agents.tasks.advance_agent_step.delay"):
        agent = create_plan(workspace=workspace, actor=owner, goal="Race", steps=steps)

    lock_held = threading.Event()
    release_lock = threading.Event()
    errors = []
    racer_result = {}

    def holder():
        try:
            with transaction.atomic():
                locked = Agent.objects.select_for_update().get(pk=agent.id)
                locked.status = Agent.Status.CANCELLED
                locked.save(update_fields=["status", "updated_at"])
                lock_held.set()
                release_lock.wait(timeout=5)
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    def racer():
        try:
            assert lock_held.wait(timeout=5)
            claim = execution._claim_next_step(str(agent.id))
            racer_result["outcome"] = claim.outcome
            racer_result["completed_after_release"] = release_lock.is_set()
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t_holder = threading.Thread(target=holder)
    t_racer = threading.Thread(target=racer)
    t_holder.start()
    assert lock_held.wait(timeout=5)
    t_racer.start()
    time.sleep(0.2)  # let the racer reach (and block on) its own select_for_update
    release_lock.set()
    t_holder.join(timeout=10)
    t_racer.join(timeout=10)

    assert not errors, errors
    assert racer_result["completed_after_release"] is True
    assert racer_result["outcome"] == "halted"
    agent.refresh_from_db()
    assert agent.status == Agent.Status.CANCELLED
    step = AgentStep.objects.get(agent=agent)
    assert step.status == AgentStep.Status.PENDING
