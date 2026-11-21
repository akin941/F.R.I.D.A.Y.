"""An implementation run through the Codex app-server driver (Phase 10 brief
D10, branch 6), end to end with the fake app-server double over pipes: the
escalations become `harness_agent_action` approvals decided on the
approvals service, usage is one ModelUsage row per turn, every robustness
fault fails the job closed with a deterministic reason and tears the
container down, and with drivers disabled the CLI path runs unchanged. No
Docker, no git, no Codex, no LLM."""

from unittest import mock

import pytest

from accounts.services import register_user
from ai_core.models import ModelUsage
from approvals.models import ApprovalRequest
from approvals.services import approve_request, reject_request
from core.channels import CHANNEL_WEB
from harness import implementation_execution, review_execution
from harness.drivers import approval_mapping
from harness.models import HarnessAgentAction, HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.tests.doubles.codex_app_server import (
    FakeContainer,
    load_transcript,
    script_from_transcript,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
FAILURE = HarnessImplementationJob.FailureReason


@pytest.fixture(autouse=True)
def driver_settings(settings):
    settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_CODEX = "app_server"
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
        agent=HarnessImplementationJob.Agent.CODEX_CLI,
        status=HarnessImplementationJob.Status.IMPLEMENTING,
        created_by=owner,
    )


WRAPPER_RESULT = {
    "tests": {"ran": True, "exit_code": 0, "passed": True, "command": "pytest -q"},
    "commit_sha": "abc123",
    "changed_files": ["auth/throttle.py"],
}


class Run:
    """Patches the runner seams and records what the job did with them."""

    def __init__(self, monkeypatch, steps, *, result=None, exit_code=0):
        self.containers = []
        runner = implementation_execution.runner

        def launch(**kwargs):
            self.launch_kwargs = kwargs
            container = FakeContainer(steps, name=kwargs["name"])
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


def owner_decides(monkeypatch, owner, decide):
    """Stands in for the owner on /approvals: the first time the driver
    waits on a request, `decide(request)` returns "approve", "reject" or a
    callable; the row then decides on the next poll (no Redis key)."""
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


# --- done when: one approved and one declined escalation, then Gate B -------


def test_fixture_run_reaches_gate_b_with_one_approved_and_one_declined_escalation(
    job, owner, monkeypatch
):
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps)
    owner_decides(monkeypatch, owner, npm_yes_curl_no)

    implementation_execution.run_harness_implementation_job(job.id)

    job.refresh_from_db()
    container = run.container
    assert container.errors == []
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW, job.failure_reason
    assert job.driver == "app_server"
    assert container.answer(100) == "accept"
    assert container.answer(101) == "decline"
    assert container.closed_stdin is True and container.killed is False
    requests = ApprovalRequest.objects.filter(tool_name="harness_agent_action").order_by(
        "created_at"
    )
    assert [r.status for r in requests] == ["approved", "rejected"]
    assert all(r.origin == "harness" and r.requested_channel == "system" for r in requests)
    [action] = HarnessAgentAction.objects.all()
    assert (action.approval_id, action.answer) == (requests[0].id, "accept")
    # The CLI path never ran; container stdout was never read as a transcript.
    run.cli_launch.assert_not_called()
    run.capture_logs.assert_not_called()
    assert run.launch_kwargs["driver"] == "app_server"
    assert run.launch_kwargs["test_command"] == "pytest -q"
    assert "login throttle" in job.raw_transcript
    # One ModelUsage row for the one turn, nothing from the CLI path.
    [usage] = _usage_rows()
    assert usage.backend == "harness_app_server:codex_cli"
    assert (usage.input_tokens, usage.output_tokens, usage.success) == (1200, 500, True)
    assert usage.context_report["turn_id"] == "turn_1"

    # Gate B: the cross-vendor review (coder != reviewer) approves.
    review = HarnessReview.objects.get(implementation_job=job)
    assert review.agent == "claude_code"
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
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    Run(monkeypatch, steps)
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    response = web_client(owner).get(
        f"/api/v1/harness/implementation-jobs/{job.id}", {"workspace": str(job.workspace_id)}
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["driver"] == "app_server"
    assert body["agent_actions"] == {"requested": 2, "approved": 1, "accepted": 1}
    assert "curl" not in str(body["agent_actions"])


# --- approval mapping inside the run ------------------------------------------


def test_escalation_still_pending_at_ten_minutes_is_declined(job, owner, monkeypatch):
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps)

    def age_past_ttl(request):
        from datetime import timedelta

        return lambda: ApprovalRequest.objects.filter(pk=request.pk).update(
            created_at=request.created_at - timedelta(seconds=601)
        )

    owner_decides(monkeypatch, owner, age_past_ttl)
    implementation_execution.run_harness_implementation_job(job.id)
    assert run.container.answer(100) == "decline"
    assert run.container.answer(101) == "decline"
    assert not HarnessAgentAction.objects.exists()


def test_job_cancel_while_an_escalation_is_pending_declines_and_kills(job, owner, monkeypatch):
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps)

    def cancel(_request):
        return lambda: HarnessImplementationJob.objects.filter(pk=job.pk).update(
            cancel_requested=True
        )

    owner_decides(monkeypatch, owner, cancel)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.CANCELLED
    assert job.failure_reason == FAILURE.KILLED
    assert run.container.answer(100) == "decline"
    assert run.container.killed is True
    [request] = ApprovalRequest.objects.filter(tool_name="harness_agent_action")
    assert request.status == ApprovalRequest.Status.EXPIRED  # revoked, no longer actionable
    run.remove.assert_called()
    run.cleanup.assert_called()


def test_escalation_cap_declines_without_another_card(job, owner, monkeypatch, settings):
    settings.HARNESS_DRIVER_MAX_ESCALATIONS = 1
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps)
    owner_decides(monkeypatch, owner, lambda request: "approve")
    implementation_execution.run_harness_implementation_job(job.id)
    assert run.container.answer(100) == "accept"
    assert run.container.answer(101) == "decline"
    assert ApprovalRequest.objects.filter(tool_name="harness_agent_action").count() == 1


def test_driver_never_accepts_without_an_approved_row(job, owner, monkeypatch):
    """An execution row planted without approval, and a job whose creator
    cannot request anything: the driver still declines."""
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps)

    def plant(request):
        return lambda: HarnessAgentAction.objects.create(
            workspace=job.workspace, job=job, approval=request, agent="codex_cli", kind="command"
        )

    owner_decides(monkeypatch, owner, plant)
    with mock.patch.object(approval_mapping, "approval_ttl") as ttl:
        from datetime import timedelta

        ttl.return_value = timedelta(seconds=0)  # end each pending wait at once
        implementation_execution.run_harness_implementation_job(job.id)
    assert run.container.answer(100) == "decline"
    assert run.container.answer(101) == "decline"


# --- usage ---------------------------------------------------------------------


def test_turn_without_usage_records_a_failure_row_and_fails_closed(job, owner, monkeypatch):
    steps = script_from_transcript(load_transcript("file_change_no_usage.jsonl"))
    run = Run(monkeypatch, steps)
    owner_decides(monkeypatch, owner, lambda request: "reject")
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert run.container.answer("fc-1") == "decline"
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    [usage] = _usage_rows()
    assert (usage.success, usage.input_tokens, usage.output_tokens) == (False, 0, 0)
    assert usage.error_type == "usage_unreported"


def test_token_brake_fails_budget_exceeded(job, owner, monkeypatch, settings):
    settings.HARNESS_DRIVER_MAX_TOKENS = 100
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    Run(monkeypatch, steps)
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.BUDGET_EXCEEDED
    assert len(_usage_rows()) == 1  # the turn's own row, no extra CLI row


# --- robustness: each fails closed, deterministic reason, container torn down --

HANDSHAKE = script_from_transcript(load_transcript("two_escalations.jsonl"))[:4]


@pytest.mark.parametrize(
    ("tail", "reason"),
    [
        ([{"raw": b"{oops\n"}], FAILURE.DRIVER_MALFORMED_JSON),
        ([{"send": {"method": "codex/whatever", "params": {}}}], FAILURE.DRIVER_UNKNOWN_METHOD),
        ([{"exit": True}], FAILURE.DRIVER_EARLY_EXIT),
        (
            [{"send": {"method": "turn/completed", "params": {"turn": {"id": "turn_1"}}}}],
            FAILURE.DRIVER_PROTOCOL_ERROR,
        ),
        (
            [
                {
                    "send": {
                        "method": "turn/completed",
                        "params": {"turn": {"id": "turn_1", "status": "failed"}},
                    }
                }
            ],
            FAILURE.DRIVER_TURN_FAILED,
        ),
    ],
)
def test_protocol_faults_fail_the_job_closed(job, monkeypatch, tail, reason):
    run = Run(monkeypatch, [*HANDSHAKE, *tail])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == reason
    assert run.container.killed is True
    run.stop.assert_called()
    run.remove.assert_called()
    run.cleanup.assert_called()
    [usage] = _usage_rows()
    assert usage.success is False


def test_oversize_line_fails_the_job_closed(job, monkeypatch):
    from harness.drivers.codex_app_server import CodexAppServerDriver
    from harness.drivers.schema import load_codex_snapshot

    def small_driver(driver_name, *, deadline):
        return CodexAppServerDriver(
            snapshot=load_codex_snapshot("0.1.0"), deadline=deadline, max_line_bytes=64
        )

    monkeypatch.setattr(implementation_execution, "build_driver", small_driver)
    run = Run(monkeypatch, [{"raw": b'{"id": 1, "result": {"pad": "' + b"x" * 500 + b'"}}\n'}])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.DRIVER_OVERSIZE_LINE
    assert run.container.killed is True


def test_deadline_fails_the_job_as_timeout(job, monkeypatch, settings):
    settings.HARNESS_MAX_JOB_SECONDS = 1
    run = Run(monkeypatch, [*HANDSHAKE, {"hang": True}])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == FAILURE.TIMEOUT
    assert run.container.killed is True


def test_failing_wrapper_after_the_protocol_phase_still_fails_closed(job, owner, monkeypatch):
    steps = script_from_transcript(load_transcript("two_escalations.jsonl"))
    run = Run(monkeypatch, steps, exit_code=1)
    owner_decides(monkeypatch, owner, npm_yes_curl_no)
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == FAILURE.RUNNER_ERROR
    assert run.container.killed is True
    assert len(_usage_rows()) == 1


# --- selection --------------------------------------------------------------------


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
    assert usage.backend == "harness_cli:codex_cli"


def test_claude_jobs_keep_the_cli_path_with_drivers_on(job, monkeypatch):
    from harness.tests.test_implementation_execution import (
        _patch_happy_path_runner,
        _wrapper_result,
    )

    HarnessImplementationJob.objects.filter(pk=job.pk).update(agent="claude_code")
    _patch_happy_path_runner(monkeypatch, result=_wrapper_result())
    interactive = mock.Mock()
    monkeypatch.setattr(
        implementation_execution.runner, "launch_interactive_container", interactive
    )
    monkeypatch.setattr(implementation_execution, "dispatch_created_review", mock.Mock())
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    interactive.assert_not_called()


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
    [usage] = _usage_rows()
    assert usage.success is False
