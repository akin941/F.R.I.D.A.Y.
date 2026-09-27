"""Phase 3 branch 5 (2026-09-27 audit) regressions: runner helper failure
conversion, cleanup of agent-locked trees, the diff secret scan, the
framed CLI output, and the implementation-job paths added in this branch
(`no_changes`, `secret_leak`, atomic review row, orphaned `pending_review`
recovery, review teardown on cancel)."""

import base64
import json
import os
import shutil
import subprocess
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import ModelUsage
from harness import implementation_execution, review_execution, runner
from harness.models import HarnessImplementationJob, HarnessRepo, HarnessReview
from harness.services import HarnessInvalidTransition
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
PREFIX = implementation_execution.CLI_OUTPUT_PREFIX


def _frame(text):
    return f"{PREFIX} {base64.b64encode(text.encode()).decode()}"


# --- framed CLI output --------------------------------------------------


def test_extract_uses_only_the_last_frame():
    logs = "\n".join(
        [_frame('{"total_cost_usd": 0}'), "noise", _frame('{"total_cost_usd": 3}'), ""]
    )
    assert implementation_execution.extract_framed_cli_output(logs) == '{"total_cost_usd": 3}'


def test_extract_fails_closed_on_malformed_last_frame_even_with_a_good_earlier_one():
    logs = "\n".join([_frame('{"total_cost_usd": 0}'), f"{PREFIX} !!!not-base64!!!"])
    assert implementation_execution.extract_framed_cli_output(logs) is None


@pytest.mark.parametrize("logs", ["", '{"total_cost_usd": 0}', "plain text only"])
def test_extract_returns_none_without_a_frame(logs):
    assert implementation_execution.extract_framed_cli_output(logs) is None


# --- runner helper failure conversion -----------------------------------


@pytest.mark.parametrize(
    "exc", [subprocess.TimeoutExpired(cmd="docker", timeout=30), FileNotFoundError("docker")]
)
def test_capture_logs_converts_timeouts_and_missing_binary(monkeypatch, exc):
    monkeypatch.setattr(runner.subprocess, "run", mock.Mock(side_effect=exc))
    with pytest.raises(runner.RunnerError):
        runner.capture_logs("c1")


def test_capture_logs_replaces_invalid_utf8(monkeypatch):
    run = mock.Mock(return_value=mock.Mock(stdout="ok�"))
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.capture_logs("c1") == "ok�"
    assert run.call_args.kwargs["errors"] == "replace"


def test_container_is_running_raises_instead_of_guessing_stopped(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30)),
    )
    with pytest.raises(runner.RunnerError):
        runner.container_is_running("c1")


def test_container_exit_code_is_unknown_on_timeout(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=30)),
    )
    assert runner.container_exit_code("c1") is None


def test_invalid_utf8_result_file_is_a_runner_error(tmp_path):
    (tmp_path / "result.json").write_bytes(b'{"changed_files": ["\xff\xfe"]}')
    with pytest.raises(runner.RunnerError):
        runner.read_implementation_result(str(tmp_path))


@pytest.mark.parametrize("launch", ["launch_container", "launch_implementation_container"])
def test_containers_drop_all_capabilities_and_privilege_gain(monkeypatch, settings, launch):
    run = mock.Mock(return_value=mock.Mock(stdout="cid\n"))
    monkeypatch.setattr(runner.subprocess, "run", run)
    kwargs = {"image": "img", "goal": "g", "cli_env": {}, "name": "n"}
    if launch == "launch_container":
        kwargs["clone_dir"] = "/tmp/c"
    else:
        kwargs.update(workspace_dir="/tmp/w", test_command="t", base_sha="b")
    getattr(runner, launch)(**kwargs)
    argv = run.call_args.args[0]
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_cleanup_removes_a_tree_the_agent_locked(tmp_path):
    root = tmp_path / "worktree"
    locked = root / "repo" / "locked"
    locked.mkdir(parents=True)
    (locked / "file").write_text("x")
    os.chmod(locked, 0o500)
    os.chmod(root / "repo", 0o500)
    runner.cleanup_directory(str(root))
    assert not root.exists()


def test_cleanup_never_touches_the_parent_of_the_tree(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    os.chmod(parent, 0o755)
    runner.cleanup_directory(str(parent / "missing"))
    assert oct(parent.stat().st_mode & 0o777) == "0o755"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_diff_contains_any_finds_a_committed_secret(tmp_path):
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "a.txt").write_text("base\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "a.txt").write_text("key = sk-ant-live-0123456789abcdef\n")
    git("commit", "-qam", "leak")
    head = git("rev-parse", "HEAD")
    assert runner.diff_contains_any(
        str(tmp_path), base_sha=base, head_sha=head, needles=["sk-ant-live-0123456789abcdef"]
    )
    assert not runner.diff_contains_any(
        str(tmp_path), base_sha=base, head_sha=head, needles=["some-other-long-value"]
    )
    assert not runner.diff_contains_any(str(tmp_path), base_sha=base, head_sha=head, needles=[])


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("where", ["binary_blob", "commit_message", "file_name"])
def test_diff_contains_any_scans_raw_objects_not_diff_text(tmp_path, where):
    """Codex code-review P1, branch 5 round 1: a binary blob, a commit
    message or a file name never shows the literal value in `git diff`
    text -- the scan reads raw object content instead."""
    secret = "sk-ant-live-0123456789abcdef"
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x.invalid",
    }

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "a.txt").write_text("base\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    message = "change"
    if where == "binary_blob":
        (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + secret.encode() + b"\xff" * 50)
    elif where == "commit_message":
        (tmp_path / "a.txt").write_text("changed\n")
        message = f"leak {secret}"
    else:
        (tmp_path / f"{secret}.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-q", "-m", message)
    head = git("rev-parse", "HEAD")
    assert runner.diff_contains_any(str(tmp_path), base_sha=base, head_sha=head, needles=[secret])


# --- implementation job paths ------------------------------------------


@pytest.fixture
def owner(db):
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="r",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


@pytest.fixture(autouse=True)
def git_push_credential(settings):
    settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"


def _job(workspace, repo, owner, **overrides):
    fields = {
        "workspace": workspace,
        "repo": repo,
        "goal": "fix the bug",
        "agent": HarnessImplementationJob.Agent.CLAUDE_CODE,
        "status": HarnessImplementationJob.Status.IMPLEMENTING,
        "created_by": owner,
    }
    fields.update(overrides)
    return HarnessImplementationJob.objects.create(**fields)


def _patch_runner(monkeypatch, *, result, leaked=False):
    cli = json.dumps({"total_cost_usd": "0.01", "usage": {"input_tokens": 3, "output_tokens": 2}})
    patches = {
        "clone_repo_writable": mock.Mock(return_value="/tmp/harness-worktree-x"),
        "resolve_commit_sha": mock.Mock(return_value="base-sha"),
        "image_for_agent": mock.Mock(return_value="img:1"),
        "launch_implementation_container": mock.Mock(),
        "container_is_running": mock.Mock(return_value=False),
        "container_exit_code": mock.Mock(return_value=0),
        "capture_logs": mock.Mock(return_value="spoof\n" + _frame(cli) + "\n"),
        "read_implementation_result": mock.Mock(return_value=result),
        "remove_container": mock.Mock(),
        "cleanup_directory": mock.Mock(),
        "diff_contains_any": mock.Mock(return_value=leaked),
    }
    for name, value in patches.items():
        monkeypatch.setattr(implementation_execution.runner, name, value)
    return patches


def _passing_result(**extra):
    return {
        "tests": {"ran": True, "exit_code": 0, "passed": True, "command": "pytest -q"},
        "commit_sha": "abc123",
        "changed_files": ["foo.py"],
        "no_changes": False,
        **extra,
    }


@pytest.mark.django_db
def test_no_changes_result_fails_the_job_with_real_cost(workspace, repo, owner, monkeypatch):
    job = _job(workspace, repo, owner)
    _patch_runner(
        monkeypatch,
        result={
            "tests": {"ran": False, "exit_code": 1, "passed": False, "command": "pytest -q"},
            "commit_sha": "base-sha",
            "changed_files": [],
            "no_changes": True,
        },
    )
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.NO_CHANGES
    assert ModelUsage.objects.get(purpose="harness.implementation").input_tokens == 3


@pytest.mark.django_db
def test_secret_in_the_diff_fails_the_job_before_review(workspace, repo, owner, monkeypatch):
    job = _job(workspace, repo, owner)
    _patch_runner(monkeypatch, result=_passing_result(), leaked=True)
    with mock.patch.object(implementation_execution, "dispatch_created_review") as dispatch:
        implementation_execution.run_harness_implementation_job(job.id)
    dispatch.assert_not_called()
    job.refresh_from_db()
    assert job.failure_reason == HarnessImplementationJob.FailureReason.SECRET_LEAK
    assert not HarnessReview.objects.filter(implementation_job=job).exists()


@pytest.mark.django_db
def test_secret_scan_receives_the_cli_credentials(workspace, repo, owner, monkeypatch, settings):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789")
    job = _job(workspace, repo, owner)
    patches = _patch_runner(monkeypatch, result=_passing_result())
    with mock.patch.object(implementation_execution, "dispatch_created_review"):
        implementation_execution.run_harness_implementation_job(job.id)
    call = patches["diff_contains_any"].call_args
    assert "sk-ant-test-0123456789" in call.kwargs["needles"]
    assert call.kwargs["head_sha"] == "abc123"


@pytest.mark.django_db
def test_pending_review_commits_together_with_its_review_row(workspace, repo, owner, monkeypatch):
    job = _job(workspace, repo, owner)
    _patch_runner(monkeypatch, result=_passing_result())
    with mock.patch.object(implementation_execution, "dispatch_created_review") as dispatch:
        implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    review = HarnessReview.objects.get(implementation_job=job)
    assert review.status == HarnessReview.Status.PENDING
    assert review.agent == HarnessReview.Agent.CODEX_CLI
    dispatch.assert_called_once_with(review)


@pytest.mark.django_db
def test_unselectable_reviewer_fails_the_job_instead_of_stranding_it(
    workspace, repo, owner, monkeypatch
):
    job = _job(workspace, repo, owner)
    _patch_runner(monkeypatch, result=_passing_result())
    with (
        mock.patch.object(
            review_execution,
            "select_review_agent",
            side_effect=HarnessInvalidTransition("no reviewer"),
        ),
        mock.patch.object(implementation_execution, "dispatch_created_review") as dispatch,
    ):
        implementation_execution.run_harness_implementation_job(job.id)
    dispatch.assert_not_called()
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE
    usage = ModelUsage.objects.get(purpose="harness.implementation")
    assert usage.input_tokens == 3


@pytest.mark.django_db
def test_recovery_fails_a_pending_review_job_with_no_active_review(
    workspace, repo, owner, monkeypatch
):
    monkeypatch.setattr(implementation_execution.runner, "cleanup_directory", mock.Mock())
    orphan = _job(workspace, repo, owner, status=HarnessImplementationJob.Status.PENDING_REVIEW)
    healthy = _job(workspace, repo, owner, status=HarnessImplementationJob.Status.PENDING_REVIEW)
    HarnessReview.objects.create(
        workspace=workspace,
        implementation_job=healthy,
        agent=HarnessReview.Agent.CODEX_CLI,
        status=HarnessReview.Status.RUNNING,
    )
    old = timezone.now() - timedelta(hours=1)
    HarnessImplementationJob.objects.filter(pk__in=[orphan.pk, healthy.pk]).update(updated_at=old)

    implementation_execution.recover_stale_harness_implementation_jobs()

    orphan.refresh_from_db()
    healthy.refresh_from_db()
    assert orphan.status == HarnessImplementationJob.Status.FAILED
    assert orphan.failure_reason == HarnessImplementationJob.FailureReason.REVIEW_UNAVAILABLE
    assert healthy.status == HarnessImplementationJob.Status.PENDING_REVIEW


@pytest.mark.django_db
def test_review_container_is_torn_down_once_the_job_is_cancelled(
    workspace, repo, owner, monkeypatch, settings
):
    settings.HARNESS_CANCEL_POLL_SECONDS = 0
    job = _job(workspace, repo, owner, status=HarnessImplementationJob.Status.CANCELLED)
    monkeypatch.setattr(
        review_execution.runner, "container_is_running", mock.Mock(return_value=True)
    )
    monkeypatch.setattr(review_execution.runner, "capture_logs", mock.Mock(return_value="partial"))
    teardown = mock.Mock()
    monkeypatch.setattr(review_execution, "_teardown_container", teardown)
    status, transcript = review_execution._wait_for_review_container(
        "harness-review-x", started_monotonic=review_execution.time.monotonic(), job_id=job.pk
    )
    assert status == "failed"
    assert transcript == "partial"
    teardown.assert_called_once_with("harness-review-x")


def test_cat_file_parser_ignores_missing_marker_inside_content():
    body = b"this line is missing\nand more"
    stream = b"abc blob %d\n" % len(body) + body + b"\n"
    assert list(runner._iter_cat_file_batch(stream)) == [body]


@pytest.mark.parametrize(
    "stream", [b"abc missing\n", b"abc blob 10\nshort", b"garbage-without-newline"]
)
def test_cat_file_parser_fails_closed_on_missing_or_truncated(stream):
    with pytest.raises(runner.RunnerError):
        list(runner._iter_cat_file_batch(stream))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_cleanup_handles_directories_without_any_owner_permission(tmp_path):
    """Codex code-review P1, branch 5 round 3: a chmod-000 directory made the
    old rmtree retry handler raise TypeError (os.open needs flags)."""
    root = tmp_path / "worktree"
    locked = root / "a" / "b"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    os.chmod(locked, 0)
    os.chmod(root / "a", 0)
    runner.cleanup_directory(str(root))
    assert not root.exists()


def test_harness_tasks_are_routed_to_the_runner_queue(settings):
    """Phase 3 branch 8: only a runner-host worker (with Docker) consumes
    harness tasks; the compose worker listens on the default queue only."""
    from config.celery import app

    route = app.amqp.router.route({}, "harness.tasks.run_harness_implementation_job")
    assert route["queue"].name == settings.HARNESS_CELERY_QUEUE == "harness"
    other = app.amqp.router.route({}, "memory.tasks.extract_memories")
    assert other["queue"].name == "celery"
