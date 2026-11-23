"""An implementation run through the Claude Agent SDK driver and the bridge
(Phase 10 brief D10, branch 7), end to end with the real bridge script and a
fake SDK over pipes: the done-when run reaches Gate B with one approved and
one declined escalation, usage and cost are one ModelUsage row per turn,
faults fail the job closed and tear the container down, and with drivers
disabled the CLI path runs unchanged. No Docker, no git, no SDK, no LLM."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from approvals.services import approve_request, reject_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from harness import implementation_execution, review_execution
from harness.drivers import approval_mapping
from harness.models import HarnessAgentAction, HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.tests.doubles.claude_agent_sdk import BridgeContainer, load_script, make_fake_sdk
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
FAILURE = HarnessImplementationJob.FailureReason


@pytest.fixture(autouse=True)
def driver_settings(settings):
    settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-20"
    settings.HARNESS_CANCEL_POLL_SECONDS = 1
    settings.HARNESS_AGENT_ACTION_POLL_SECONDS = 0
    settings.HARNESS_AGENT_POLICY = {
        "discovery": {"default": "claude_code", "selectable": ["claude_code"]},
        "implementation": {
            "default": "claude_code",
            "selectable": ["claude_code", "codex_cli"],
            "reviewers": {"claude_code": "codex_cli", "codex_cli": "claude_code"},
        },
    }
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="auth-service",
        git_url="git@example.com:x/auth.git",
        enabled=True,
        test_command="pytest -q",
    )


@pytest.fixture
def job(workspace, repo, owner):
    return HarnessImplementationJob.objects.create(
        workspace=workspace,
        repo=repo,
        goal="fix the login throttle test",
        agent=HarnessImplementationJob.Agent.CLAUDE_CODE,
        status=HarnessImplementationJob.Status.IMPLEMENTING,
        created_by=owner,
    )


WRAPPER_RESULT = {
    "tests": {"ran": True, "exit_code": 0, "passed": True, "command": "pytest -q"},
    "commit_sha": "abc123",
    "changed_files": ["auth/throttle.py"],
}


class Run:
    """Patches the runner seams; each interactive launch starts the real
    bridge with a fake SDK replaying `script`."""

    def __init__(self, monkeypatch, script, *, result=None, exit_code=0):
        self.containers = []
        self.sdk = make_fake_sdk(script)
        runner = implementation_execution.runner

        def launch(**kwargs):
            self.launch_kwargs = kwargs
            container = BridgeContainer(
                self.sdk, test_command=kwargs["test_command"], name=kwargs["name"]
            )
            self.containers.append(container)
            return container

        self.launch = mock.Mock(side_effect=launch)
        self.cli_launch = mock.Mock()
        self.capture_logs = mock.Mock(return_value="")
        self.remove = mock.Mock()
        self.cleanup = mock.Mock()
        self.stop = mock.Mock()
        patches = {
            "clone_repo_writable": mock.Mock(return_value="/tmp/harness-worktree-x"),
            "resolve_commit_sha": mock.Mock(return_value="base-sha"),
            "image_for_agent": mock.Mock(return_value="img:1"),
            "launch_interactive_container": self.launch,
            "launch_implementation_container": self.cli_launch,
            "container_is_running": mock.Mock(return_value=False),
            "container_exit_code": mock.Mock(return_value=exit_code),
            "capture_logs": self.capture_logs,
            "read_implementation_result": mock.Mock(return_value=result or WRAPPER_RESULT),
            "remove_container": self.remove,
            "cleanup_directory": self.cleanup,
            "diff_contains_any": mock.Mock(return_value=False),
            "diff_stat": mock.Mock(side_effect=runner.RunnerError("no stat in tests")),
            "stop_container": self.stop,
            "kill_container": mock.Mock(),
        }
        for name, value in patches.items():
            monkeypatch.setattr(runner, name, value)
        self.dispatch_review = mock.Mock()
        monkeypatch.setattr(
            implementation_execution, "dispatch_created_review", self.dispatch_review
        )

    @property
    def container(self):
        return self.containers[0]

    def answers(self):
        return [
            (call["tool"], call["input"].get("command"), call["answer"]) for call in self.sdk.calls
        ]


def owner_decides(monkeypatch, owner, decide):
    """The owner on /approvals: the first wait on a request runs
    `decide(request)` ("approve", "reject" or a callable)."""
    seen = set()

    def wait(approval_id, timeout):
        if approval_id in seen:
            return False
        seen.add(approval_id)
        request = ApprovalRequest.objects.get(pk=approval_id)
        action = decide(request)
        if action == "approve":
            with mock.patch.object(approval_mapping, "push_wake_hint", return_value=False):
                approve_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        elif action == "reject":
            reject_request(approval_request=request, actor=owner, channel=CHANNEL_WEB)
        elif callable(action):
            action()
        return False

    monkeypatch.setattr(approval_mapping, "wait_wake_hint", wait)


def npm_yes_curl_no(request):
    return "approve" if "npm" in request.payload["command"] else "reject"


def _usage_rows():
    return list(ModelUsage.objects.filter(purpose="harness.implementation"))


def _script():
    return load_script("two_escalations.json")


# --- done when: the branch-6 fixture run, through the Claude driver ---------------


def test_fixture_run_reaches_gate_b_with_one_approved_and_one_declined_escalation(
    job, owner, monkeypatch
):
    run = Run(monkeypatch, _script())
    owner_decides(monkeypatch, owner, npm_yes_curl_no)

    implementation_execution.run_harness_implementation_job(job.id)

    job.refresh_from_db()
    container = run.container
    assert container.errors == []
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW, job.failure_reason
    assert job.driver == "agent_sdk"
    assert run.answers() == [
        ("Read", None, "allow"),
        ("Grep", None, "allow"),
        ("Edit", None, "allow"),
        ("Bash", "pytest -q", "allow"),
        ("Bash", "npm install left-pad", "allow"),
        ("Bash", "curl https://example.invalid/x", "deny"),
    ]
    assert container.closed_stdin is True and container.killed is False
    assert container.exit_code == 0
    requests = ApprovalRequest.objects.filter(tool_name="harness_agent_action").order_by(
        "created_at"
    )
    assert [(r.status, r.payload["command"]) for r in requests] == [
        ("approved", "npm install left-pad"),
        ("rejected", "curl https://example.invalid/x"),
    ]
    assert all(r.origin == "harness" and r.requested_channel == "system" for r in requests)
    assert all(r.payload["agent"] == "claude_code" for r in requests)
    assert requests[0].payload["cwd"] == "/workspace/repo"
    [action] = HarnessAgentAction.objects.all()
    assert (action.approval_id, action.answer, action.agent) == (
        requests[0].id,
        "accept",
        "claude_code",
    )
    run.cli_launch.assert_not_called()
    run.capture_logs.assert_not_called()
    assert run.launch_kwargs["driver"] == "agent_sdk"
    assert run.launch_kwargs["test_command"] == "pytest -q"
    assert "login throttle" in job.raw_transcript
    # One ModelUsage row for the one turn, with the SDK's reported cost.
    [usage] = _usage_rows()
    assert usage.backend == "harness_agent_sdk:claude_code"
    assert (usage.input_tokens, usage.output_tokens, usage.success) == (1200, 500, True)
    assert usage.estimated_cost == Decimal("0.42")
    assert usage.context_report["cost_source"] == "driver_reported"
    assert usage.context_report["cached_input_tokens"] == 300
    assert usage.context_report["turn_id"] == "turn_1"

    # Gate B: the cross-vendor review (coder != reviewer) approves.
    review = HarnessReview.objects.get(implementation_job=job)
    assert review.agent == "codex_cli"
    run.dispatch_review.assert_called_once()
    rr = review_execution.runner
    monkeypatch.setattr(rr, "create_bundle", mock.Mock())
    monkeypatch.setattr(rr, "prepare_review_clone", mock.Mock(return_value="/tmp/clean"))
    monkeypatch.setattr(rr, "launch_container", mock.Mock())
    monkeypatch.setattr(rr, "container_is_running", mock.Mock(side_effect=[False]))
    monkeypatch.setattr(rr, "capture_logs", mock.Mock(return_value="review transcript"))
    with mock.patch.object(
        review_execution, "_produce_review_summary", return_value=("looks good", [], "approved")
    ):
        review_execution.run_harness_review_job(review.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_GATE_B
    assert job.gate_b_request.tool_name == "harness_push"


def test_job_page_shows_the_driver_and_the_escalation_counts(job, owner, monkeypatch, web_client):
    Run(monkeypatch, _script())
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    response = web_client(owner).get(
        f"/api/v1/harness/implementation-jobs/{job.id}", {"workspace": str(job.workspace_id)}
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["driver"] == "agent_sdk"
    assert body["agent_actions"] == {"requested": 2, "approved": 1, "accepted": 1}


# --- approval mapping inside the run --------------------------------------------------


def test_escalation_still_pending_at_ten_minutes_is_declined(job, owner, monkeypatch):
    run = Run(monkeypatch, _script())

    def age_past_ttl(request):
        return lambda: ApprovalRequest.objects.filter(pk=request.pk).update(
            created_at=request.created_at - timedelta(seconds=601)
        )

    owner_decides(monkeypatch, owner, age_past_ttl)
    implementation_execution.run_harness_implementation_job(job.id)
    assert [answer for *_, answer in run.answers()][-2:] == ["deny", "deny"]
    assert not HarnessAgentAction.objects.exists()
    statuses = set(
        ApprovalRequest.objects.filter(tool_name="harness_agent_action").values_list(
            "status", flat=True
        )
    )
    assert statuses == {ApprovalRequest.Status.EXPIRED}


def test_job_cancel_while_an_escalation_is_pending_declines_and_kills(job, owner, monkeypatch):
    run = Run(monkeypatch, _script())

    def cancel(_request):
        return lambda: HarnessImplementationJob.objects.filter(pk=job.pk).update(
            cancel_requested=True
        )

    owner_decides(monkeypatch, owner, cancel)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.CANCELLED
    assert job.failure_reason == FAILURE.KILLED
    assert run.container.killed is True
    run.container.wait()
    assert run.sdk.calls[4]["answer"] == "deny"
    [request] = ApprovalRequest.objects.filter(tool_name="harness_agent_action")
    assert request.status == ApprovalRequest.Status.EXPIRED
    run.remove.assert_called()
    run.cleanup.assert_called()


def test_driver_never_allows_without_an_approved_row(job, owner, monkeypatch):
    run = Run(monkeypatch, _script())

    def plant(request):
        return lambda: HarnessAgentAction.objects.create(
            workspace=job.workspace,
            job=job,
            approval=request,
            agent="claude_code",
            kind="command",
        )

    owner_decides(monkeypatch, owner, plant)
    with mock.patch.object(approval_mapping, "approval_ttl", return_value=timedelta(seconds=0)):
        implementation_execution.run_harness_implementation_job(job.id)
    assert [answer for *_, answer in run.answers()][-2:] == ["deny", "deny"]


def test_a_command_the_card_cannot_show_verbatim_is_denied_without_a_card(job, owner, monkeypatch):
    script = [
        {"tool": "Bash", "input": {"command": "echo ok‮; rm -rf /"}},
        {"tool": "WebFetch", "input": {"url": "https://example.invalid/" + "x" * 3000}},
        {"result": {"subtype": "success", "is_error": False, "usage": None}},
    ]
    run = Run(monkeypatch, script)
    owner_decides(monkeypatch, owner, lambda request: "approve")
    implementation_execution.run_harness_implementation_job(job.id)
    assert [answer for *_, answer in run.answers()] == ["deny", "deny"]
    assert not ApprovalRequest.objects.filter(tool_name="harness_agent_action").exists()


def test_a_write_outside_the_repo_is_a_file_change_card_with_its_whole_input(
    job, owner, monkeypatch
):
    run = Run(monkeypatch, load_script("no_usage.json"))
    owner_decides(monkeypatch, owner, lambda request: "reject")
    implementation_execution.run_harness_implementation_job(job.id)
    [request] = ApprovalRequest.objects.filter(tool_name="harness_agent_action")
    assert request.payload["kind"] == "file_change"
    assert request.payload["command"] == (
        'Write {"content":"* * * * * true","file_path":"/etc/cron.d/x"}'
    )
    assert run.answers()[0][2] == "deny"


# --- usage and cost ---------------------------------------------------------------------


def test_turn_without_usage_records_a_failure_row_and_fails_closed(job, owner, monkeypatch):
    Run(monkeypatch, load_script("no_usage.json"))
    owner_decides(monkeypatch, owner, lambda request: "reject")
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    [usage] = _usage_rows()
    assert (usage.success, usage.input_tokens, usage.output_tokens) == (False, 0, 0)
    assert usage.estimated_cost == Decimal("0")
    assert usage.error_type == "usage_unreported"


def test_cost_brake_fails_budget_exceeded(job, owner, monkeypatch, settings):
    settings.HARNESS_MAX_JOB_COST = 0.10
    Run(monkeypatch, _script())
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.BUDGET_EXCEEDED
    [usage] = _usage_rows()  # the turn's own row, no extra CLI row
    assert usage.estimated_cost == Decimal("0.42")


def test_token_brake_also_bounds_agent_sdk_runs(job, owner, monkeypatch, settings):
    settings.HARNESS_DRIVER_MAX_TOKENS = 100
    Run(monkeypatch, _script())
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.BUDGET_EXCEEDED


# --- robustness ---------------------------------------------------------------------------


def test_an_sdk_failure_mid_turn_fails_the_job_closed(job, monkeypatch):
    run = Run(monkeypatch, [{"text": "starting"}, {"raise": "boom"}])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.DRIVER_TURN_FAILED
    assert run.container.killed is True
    run.remove.assert_called()
    run.cleanup.assert_called()
    [usage] = _usage_rows()
    assert usage.success is False


def test_an_error_result_with_usage_is_never_a_successful_usage_row(job, monkeypatch):
    script = [
        {
            "result": {
                "subtype": "error_max_turns",
                "is_error": True,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "total_cost_usd": 0.01,
            }
        }
    ]
    Run(monkeypatch, script)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.DRIVER_TURN_FAILED
    [usage] = _usage_rows()
    assert (usage.success, usage.error_type, usage.input_tokens) == (False, "turn_failed", 10)


def test_a_bridge_on_another_sdk_version_fails_closed(job, monkeypatch, settings):
    run = Run(monkeypatch, _script())
    run.sdk.__version__ = "9.9.9"
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.DRIVER_PROTOCOL_ERROR
    assert run.container.killed is True
    assert run.sdk.calls == []  # no turn ever started


def test_deadline_fails_the_job_as_timeout(job, monkeypatch, settings):
    settings.HARNESS_MAX_JOB_SECONDS = 1
    run = Run(monkeypatch, [{"hang": True}])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.TIMEOUT
    assert run.container.killed is True


def test_failing_wrapper_after_the_protocol_phase_still_fails_closed(job, owner, monkeypatch):
    run = Run(monkeypatch, _script(), exit_code=1)
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    assert run.container.killed is True
    assert len(_usage_rows()) == 1


def test_a_missing_surface_snapshot_fails_closed_before_launch(job, monkeypatch, settings):
    settings.HARNESS_CLAUDE_AGENT_SDK_VERSION = "7.7.7"
    run = Run(monkeypatch, _script())
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    run.launch.assert_not_called()


# --- selection ----------------------------------------------------------------------------


def test_drivers_disabled_runs_the_cli_path(job, monkeypatch, settings):
    from harness.tests.test_implementation_execution import (
        _patch_happy_path_runner,
        _wrapper_result,
    )

    settings.HARNESS_DRIVERS_ENABLED = False
    _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
    interactive = mock.Mock()
    monkeypatch.setattr(
        implementation_execution.runner, "launch_interactive_container", interactive
    )
    monkeypatch.setattr(implementation_execution, "dispatch_created_review", mock.Mock())
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    assert job.driver == "cli"
    interactive.assert_not_called()
    implementation_execution.runner.launch_implementation_container.assert_called_once()
    [usage] = _usage_rows()
    assert usage.backend == "harness_cli:claude_code"


def test_enabled_driver_without_d4_fails_closed_before_cloning(job, monkeypatch, settings):
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = ""
    run = Run(monkeypatch, [])
    clone = implementation_execution.runner.clone_repo_writable
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    clone.assert_not_called()
    run.launch.assert_not_called()


def test_every_answer_is_audited(job, owner, monkeypatch):
    Run(monkeypatch, _script())
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    rows = AuditLog.objects.filter(action="harness.agent_action_answered").order_by("created_at")
    assert [(r.metadata["decision"], r.metadata["agent"]) for r in rows] == [
        ("accept", "claude_code"),
        ("decline", "claude_code"),
    ]
