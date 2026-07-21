"""Every test here mocks `subprocess.run` -- no test in this module (or
anywhere else in the suite) invokes a real `docker`/`git` process (CLAUDE.md
rule 5, extended to this out-of-process call per the brief's own test
strategy)."""

import os
import subprocess
from unittest import mock

import pytest

from harness import runner


class TestCloneRepo:
    def test_clone_returns_directory_and_cleans_up_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)
        with pytest.raises(runner.RunnerError):
            runner.clone_repo(git_url="git@x:y.git", default_branch="main", clone_env={})
        rmtree.assert_called_once()

    def test_clone_success_returns_a_real_directory(self, monkeypatch, tmp_path):
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        directory = runner.clone_repo(git_url="git@x:y.git", default_branch="main", clone_env={})
        try:
            assert directory
            argv = run_mock.call_args.args[0]
            assert argv[:2] == ["git", "clone"]
            assert "--branch" in argv
            assert "main" in argv
            assert "git@x:y.git" in argv
        finally:
            runner.cleanup_directory(directory)

    def test_argv_has_option_terminator_before_git_url(self, monkeypatch, tmp_path):
        """Codex code-review P2, branch 2 round 7: a curator-supplied
        `git_url` starting with `-` is accepted by `harness.serializers`'
        validator (it only rejects embedded credentials/query-fragments,
        not a leading dash) -- without a `--` option-terminator right
        before it, git would parse it as another option instead of the
        repository to clone."""
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        directory = runner.clone_repo(git_url="git@x:y.git", default_branch="main", clone_env={})
        try:
            argv = run_mock.call_args.args[0]
            assert argv.index("--") == argv.index("git@x:y.git") - 1
        finally:
            runner.cleanup_directory(directory)

    def test_timeout_cleans_up_and_raises(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.TimeoutExpired("git", 1)),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)
        with pytest.raises(runner.RunnerError):
            runner.clone_repo(git_url="git@x:y.git", default_branch="main", clone_env={})
        rmtree.assert_called_once()


class TestLaunchContainer:
    def test_argv_is_never_a_shell_string(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        settings.HARNESS_CONTAINER_MEMORY = "2g"
        settings.HARNESS_CONTAINER_CPUS = "1.0"
        settings.HARNESS_CONTAINER_PIDS_LIMIT = 256
        run_mock = mock.Mock(return_value=mock.Mock(stdout="containerid123\n"))
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        runner.launch_container(
            image="jarvis-harness-claude-code:1",
            clone_dir="/tmp/clone",
            goal="; rm -rf / #",
            cli_env={"ANTHROPIC_API_KEY": "secret"},
            name="harness-job-abc123",
        )

        argv = run_mock.call_args.args[0]
        assert isinstance(argv, list)
        assert run_mock.call_args.kwargs.get("shell", False) is False
        assert "--name" in argv and "harness-job-abc123" in argv
        # Codex round-4 P2: never let a missing/typoed tag silently pull
        # from a registry -- only a prebuilt, already-reviewed local
        # image may ever run.
        assert "--pull" in argv
        assert argv[argv.index("--pull") + 1] == "never"
        # Codex round-6 P2: the container runs as the SAME uid/gid that
        # cloned the repo on the host, not the image's own baked-in
        # non-root user -- the fix for the read-only-mount permission
        # mismatch, without widening the clone's own permissions.
        assert "--user" in argv
        assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert "--network" in argv and "harness-net" in argv
        assert "--memory" in argv and "2g" in argv
        assert "--cpus" in argv and "1.0" in argv
        assert "--pids-limit" in argv and "256" in argv
        assert any("readonly" in part and "/tmp/clone" in part for part in argv)
        # The secret value itself must never appear in argv (Codex round-2
        # P2: visible via `ps`/process listings on the runner host) -- only
        # an `--env-file` path does, and the file it points to is written
        # with the real value, mode 0600, then deleted right after this
        # call returns.
        assert not any("secret" in part for part in argv)
        assert "--env-file" in argv
        env_file_index = argv.index("--env-file") + 1
        env_file_path = argv[env_file_index]
        assert not os.path.exists(env_file_path)  # deleted after the call
        # The untrusted goal is passed as its own argv element, never
        # interpolated into a shell string.
        assert argv[-1] == "; rm -rf / #"
        assert argv[-2] == "--goal"

    def test_env_file_contains_the_real_value_mode_0600_at_call_time(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        captured = {}

        def fake_run(argv, **kwargs):
            env_file_path = argv[argv.index("--env-file") + 1]
            captured["path"] = env_file_path
            captured["mode"] = oct(os.stat(env_file_path).st_mode & 0o777)
            captured["contents"] = open(env_file_path).read()
            return mock.Mock(stdout="cid\n")

        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        runner.launch_container(
            image="img",
            clone_dir="/tmp/clone",
            goal="analyze",
            cli_env={"ANTHROPIC_API_KEY": "secret-value"},
            name="harness-job-abc123",
        )

        assert captured["mode"] == "0o600"
        assert captured["contents"] == "ANTHROPIC_API_KEY=secret-value\n"
        assert not os.path.exists(captured["path"])

    def test_no_env_file_created_when_cli_env_is_empty(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        run_mock = mock.Mock(return_value=mock.Mock(stdout="cid\n"))
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        runner.launch_container(
            image="img", clone_dir="/tmp/clone", goal="analyze", cli_env={}, name="harness-job-x"
        )

        argv = run_mock.call_args.args[0]
        assert "--env-file" not in argv

    def test_launch_failure_raises_runner_error(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "docker")),
        )
        with pytest.raises(runner.RunnerError):
            runner.launch_container(
                image="img",
                clone_dir="/tmp/clone",
                goal="analyze",
                cli_env={},
                name="harness-job-x",
            )


class TestContainerLifecycle:
    def test_capture_logs_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess, "run", mock.Mock(return_value=mock.Mock(stdout="hello"))
        )
        assert runner.capture_logs("cid") == "hello"

    def test_container_exit_code_zero(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=0, stdout="0\n")),
        )
        assert runner.container_exit_code("cid") == 0

    def test_container_exit_code_nonzero(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=0, stdout="1\n")),
        )
        assert runner.container_exit_code("cid") == 1

    def test_container_exit_code_none_when_inspect_fails(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=1, stdout="")),
        )
        assert runner.container_exit_code("cid") is None

    def test_container_is_running_true(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=0, stdout="true\n")),
        )
        assert runner.container_is_running("cid") is True

    def test_container_is_running_false_when_stopped(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=0, stdout="false\n")),
        )
        assert runner.container_is_running("cid") is False

    def test_container_is_running_false_when_gone(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=1, stdout="")),
        )
        assert runner.container_is_running("cid") is False

    def test_stop_container_uses_configured_grace_period(self, monkeypatch):
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        runner.stop_container("cid", grace_seconds=7)
        argv = run_mock.call_args.args[0]
        assert argv == ["docker", "stop", "--time", "7", "cid"]

    def test_kill_container(self, monkeypatch):
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        runner.kill_container("cid")
        assert run_mock.call_args.args[0] == ["docker", "kill", "cid"]

    def test_remove_container_success(self, monkeypatch):
        run_mock = mock.Mock(return_value=mock.Mock(returncode=0, stderr=""))
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        runner.remove_container("cid")  # must not raise
        assert run_mock.call_args.args[0] == ["docker", "rm", "-f", "cid"]

    def test_remove_container_tolerates_already_gone(self, monkeypatch):
        run_mock = mock.Mock(
            return_value=mock.Mock(returncode=1, stderr="Error: No such container: cid")
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        runner.remove_container("cid")  # must not raise

    def test_remove_container_raises_on_genuine_failure(self, monkeypatch):
        """Codex code-review P2, branch 2 round 6: an earlier draft never
        inspected the return code at all, so a genuine removal failure
        (the daemon refusing, not "already gone") looked identical to
        success -- callers cleared HarnessJob.container_id regardless,
        orphaning a still-running container with no handle left."""
        run_mock = mock.Mock(
            return_value=mock.Mock(returncode=1, stderr="Error: a driver error occurred")
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        with pytest.raises(runner.RunnerError):
            runner.remove_container("cid")


class TestImageForAgent:
    def test_resolves_configured_image_per_agent(self, settings):
        settings.HARNESS_IMAGE_CLAUDE_CODE = "claude-image:1"
        settings.HARNESS_IMAGE_CODEX_CLI = "codex-image:1"
        assert runner.image_for_agent("claude_code") == "claude-image:1"
        assert runner.image_for_agent("codex_cli") == "codex-image:1"


class TestCloneRepoWritable:
    def test_returns_workspace_dir_with_repo_subdir_and_branch_checked_out(self, monkeypatch):
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        workspace_dir = runner.clone_repo_writable(
            git_url="git@x:y.git", default_branch="main", branch_name="harness/p3-1", clone_env={}
        )
        try:
            clone_argv = run_mock.call_args_list[0].args[0]
            assert clone_argv[:2] == ["git", "clone"]
            assert clone_argv[-1] == os.path.join(workspace_dir, "repo")
            checkout_argv = run_mock.call_args_list[1].args[0]
            assert checkout_argv == [
                "git",
                "-C",
                os.path.join(workspace_dir, "repo"),
                "checkout",
                "-b",
                "harness/p3-1",
            ]
        finally:
            runner.cleanup_directory(workspace_dir)

    def test_cleans_up_on_clone_failure(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)
        with pytest.raises(runner.RunnerError):
            runner.clone_repo_writable(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                clone_env={},
            )
        rmtree.assert_called_once()

    def test_cleans_up_on_checkout_failure(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(
                side_effect=[mock.Mock(), subprocess.CalledProcessError(1, "git")],
            ),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)
        with pytest.raises(runner.RunnerError):
            runner.clone_repo_writable(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                clone_env={},
            )
        rmtree.assert_called_once()


class TestLaunchImplementationContainer:
    def test_argv_uses_a_readwrite_workspace_mount_never_readonly(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        settings.HARNESS_CONTAINER_MEMORY = "2g"
        settings.HARNESS_CONTAINER_CPUS = "1.0"
        settings.HARNESS_CONTAINER_PIDS_LIMIT = 256
        run_mock = mock.Mock(return_value=mock.Mock(stdout="containerid123\n"))
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        runner.launch_implementation_container(
            image="jarvis-harness-claude-code:1",
            workspace_dir="/tmp/workspace",
            goal="implement the feature",
            test_command="pytest -q",
            base_sha="base111",
            cli_env={"ANTHROPIC_API_KEY": "secret"},
            name="harness-impl-abc123",
        )

        argv = run_mock.call_args.args[0]
        # The security-critical split (brief §boundary): HarnessJob/
        # HarnessReview never get a writable mount, HarnessImplementationJob
        # never gets a read-only one -- this is that other half, asserted
        # directly rather than only inferred from the function's own name.
        mount_arg = next(part for part in argv if part.startswith("type=bind"))
        assert "readonly" not in mount_arg
        assert "target=/workspace" in mount_arg
        assert "/tmp/workspace" in mount_arg
        assert "--workdir" in argv
        assert argv[argv.index("--workdir") + 1] == "/workspace/repo"
        assert "--test-command" in argv
        assert argv[argv.index("--test-command") + 1] == "pytest -q"
        # Codex code-review P2, branch 1 rounds 2 and 4: the wrapper can't
        # infer a diff base on its own (no upstream configured), and a
        # ref name (round 2's original design) would be mutable from
        # inside the agent-writable worktree -- the host must resolve and
        # pass an immutable SHA explicitly instead.
        assert "--base-commit" in argv
        assert argv[argv.index("--base-commit") + 1] == "base111"
        assert not any("secret" in part for part in argv)

    def test_launch_failure_raises_runner_error(self, monkeypatch, settings):
        settings.HARNESS_DOCKER_NETWORK = "harness-net"
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "docker")),
        )
        with pytest.raises(runner.RunnerError):
            runner.launch_implementation_container(
                image="img",
                workspace_dir="/tmp/workspace",
                goal="implement",
                test_command="pytest -q",
                base_sha="base111",
                cli_env={},
                name="harness-impl-x",
            )


class TestResolveCommitSha:
    def test_returns_resolved_sha(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=0, stdout="abc123\n")),
        )
        assert runner.resolve_commit_sha("/tmp/repo", "HEAD") == "abc123"

    def test_raises_when_ref_does_not_resolve(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(return_value=mock.Mock(returncode=1, stderr="unknown revision")),
        )
        with pytest.raises(runner.RunnerError):
            runner.resolve_commit_sha("/tmp/repo", "refs/heads/missing")

    def test_timeout_raises_runner_error_not_timeout_expired(self, monkeypatch):
        """Codex code-review P2, branch 3 round 14: both callers
        (`push_bundled_commit`, `prepare_review_clone`) wrap their own
        clone-preparation steps in `except RunnerError: cleanup_
        directory(...); raise` -- a raw `subprocess.TimeoutExpired`
        bypassed that entirely, leaking the clean-clone checkout with no
        caller left holding its path."""
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="git rev-parse", timeout=30)),
        )
        with pytest.raises(runner.RunnerError):
            runner.resolve_commit_sha("/tmp/repo", "FETCH_HEAD")


class TestCreateBundle:
    def test_argv_bundles_a_named_ref_range_not_a_bare_sha(self, monkeypatch):
        """Codex plan-review round 6/7: `git bundle create` rejects a bare
        revision with no accompanying ref -- the range form's right-hand
        side must be a named ref."""
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        runner.create_bundle(
            "/tmp/agent-worktree",
            base_sha="base111",
            ref="refs/heads/harness/p3-1",
            bundle_path="/tmp/out.bundle",
        )

        argv = run_mock.call_args.args[0]
        assert argv == [
            "git",
            "-C",
            "/tmp/agent-worktree",
            "bundle",
            "create",
            "/tmp/out.bundle",
            "base111..refs/heads/harness/p3-1",
        ]

    def test_failure_raises_runner_error(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        with pytest.raises(runner.RunnerError):
            runner.create_bundle(
                "/tmp/agent-worktree",
                base_sha="base111",
                ref="refs/heads/harness/p3-1",
                bundle_path="/tmp/out.bundle",
            )


class TestCloneFull:
    def test_clones_full_history_not_shallow(self, monkeypatch):
        """Codex code-review P2, branch 1 round 1: `clone_repo`'s
        `--depth 1` shallow clone would only contain the remote's
        CURRENT tip -- if it advanced since `base_sha` was recorded, a
        shallow clean-clone genuinely wouldn't have `base_sha` at all,
        breaking the bundle fetch even for a perfectly valid produced
        commit. This clone must never pass `--depth`."""
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        directory = runner._clone_full(git_url="git@x:y.git", default_branch="main", clone_env={})
        try:
            argv = run_mock.call_args.args[0]
            assert "--depth" not in argv
            assert argv[:2] == ["git", "clone"]
            assert "main" in argv
        finally:
            runner.cleanup_directory(directory)

    def test_cleans_up_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)
        with pytest.raises(runner.RunnerError):
            runner._clone_full(git_url="git@x:y.git", default_branch="main", clone_env={})
        rmtree.assert_called_once()


class TestPushBundledCommit:
    """The security-critical property under test throughout this class:
    the push/fetch subprocess calls never reference the agent's own
    worktree directory at all -- only the freshly host-only-cloned
    directory `clone_repo` (mocked here) returns. Codex plan-review
    rounds 3-8 converged on this exact design after finding every
    push-from-the-agent's-own-worktree variant vulnerable to some
    agent-controlled `.git/config`/hook mechanism."""

    @pytest.fixture(autouse=True)
    def git_push_credential(self, settings):
        # Every test in this class exercises the configured-credential
        # path; the fail-closed-when-unset case gets its own dedicated
        # test below instead.
        settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"

    def _mock_clean_clone(self, monkeypatch, clean_clone_dir):
        monkeypatch.setattr(runner, "_clone_full", mock.Mock(return_value=clean_clone_dir))

    def test_raises_when_push_credential_is_not_configured(self, monkeypatch, settings, tmp_path):
        """Codex code-review P1, branch 1 round 3: an earlier version
        resolved an unconfigured credential to `{}`, which was then
        merged into `os.environ` -- Git still saw the runner host's own
        ambient SSH agent/default keys, so a push could silently succeed
        using unintended credentials instead of being refused. There is
        no safe ambient fallback; this must fail before any clone/fetch/
        push is even attempted."""
        settings.HARNESS_GIT_SSH_KEY_PATH = ""
        clone_full_mock = mock.Mock()
        monkeypatch.setattr(runner, "_clone_full", clone_full_mock)
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        with pytest.raises(runner.RunnerError):
            runner.push_bundled_commit(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        clone_full_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_never_references_the_agent_worktree_directory(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="produced999\n"),  # rev-parse FETCH_HEAD
                mock.Mock(returncode=0),  # merge-base --is-ancestor
                mock.Mock(returncode=0),  # git push
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)

        runner.push_bundled_commit(
            git_url="git@x:y.git",
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )

        for call in run_mock.call_args_list:
            argv = call.args[0]
            assert "agent-worktree" not in " ".join(argv)
            assert (
                clean_clone_dir in " ".join(argv)
                or "/tmp/out.bundle" in argv
                or "git@x:y.git" in argv
            )
        rmtree.assert_called_once_with(clean_clone_dir, ignore_errors=True)

    def test_push_uses_explicit_url_and_create_only_lease(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),
                mock.Mock(returncode=0, stdout="produced999\n"),
                mock.Mock(returncode=0),
                mock.Mock(returncode=0),
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        runner.push_bundled_commit(
            git_url="git@x:y.git",
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )

        push_argv = run_mock.call_args_list[3].args[0]
        assert push_argv == [
            "git",
            "-C",
            clean_clone_dir,
            "push",
            "git@x:y.git",
            "FETCH_HEAD:refs/heads/harness/p3-1",
            "--force-with-lease=refs/heads/harness/p3-1:",
        ]

    def test_sha_mismatch_raises_and_never_pushes(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="unexpected-sha\n"),  # rev-parse FETCH_HEAD
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        with pytest.raises(runner.RunnerError):
            runner.push_bundled_commit(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        assert run_mock.call_count == 2  # never reached merge-base or push

    def test_non_ancestor_base_raises_and_never_pushes(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="produced999\n"),  # rev-parse FETCH_HEAD
                mock.Mock(returncode=1),  # merge-base --is-ancestor fails
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        with pytest.raises(runner.RunnerError):
            runner.push_bundled_commit(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        assert run_mock.call_count == 3  # never reached push

    def test_cleans_up_clean_clone_even_on_failure(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)

        with pytest.raises(runner.RunnerError):
            runner.push_bundled_commit(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        rmtree.assert_called_once_with(clean_clone_dir, ignore_errors=True)

    @pytest.mark.parametrize(
        "git_url",
        ["https://github.com/org/repo.git", "http://github.com/org/repo.git"],
    )
    def test_raises_for_non_ssh_remote(self, monkeypatch, tmp_path, git_url):
        """Codex code-review P1, branch 1 round 5: `GIT_SSH_COMMAND` (the
        whole credential mechanism this module uses) has no effect at all
        on an HTTPS remote -- Git falls back to its own separate
        credential-helper/netrc/global-config mechanism, silently using
        the runner host's own ambient HTTPS credentials instead of being
        refused. Must fail before any clone/fetch/push is attempted."""
        clone_full_mock = mock.Mock()
        monkeypatch.setattr(runner, "_clone_full", clone_full_mock)
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        with pytest.raises(runner.RunnerError):
            runner.push_bundled_commit(
                git_url=git_url,
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        clone_full_mock.assert_not_called()
        run_mock.assert_not_called()

    @pytest.mark.parametrize("git_url", ["ssh://git@github.com/org/repo.git", "git@x:y.git"])
    def test_allows_ssh_and_scp_like_remotes(self, monkeypatch, tmp_path, git_url):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),
                mock.Mock(returncode=0, stdout="produced999\n"),
                mock.Mock(returncode=0),
                mock.Mock(returncode=0),
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        runner.push_bundled_commit(
            git_url=git_url,
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )  # must not raise


class TestPrepareReviewClone:
    """Codex code-review P1, branch 3 round 1: the security-critical
    property under test throughout this class is the same as
    `TestPushBundledCommit`'s -- the checkout produced here must be
    fully agent-config-untouched, never derived from or overlapping with
    the agent's own writable worktree directory."""

    @pytest.fixture(autouse=True)
    def git_push_credential(self, settings):
        settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"

    def _mock_clean_clone(self, monkeypatch, clean_clone_dir):
        monkeypatch.setattr(runner, "_clone_full", mock.Mock(return_value=clean_clone_dir))

    def test_raises_when_push_credential_is_not_configured(self, monkeypatch, settings):
        settings.HARNESS_GIT_SSH_KEY_PATH = ""
        clone_full_mock = mock.Mock()
        monkeypatch.setattr(runner, "_clone_full", clone_full_mock)
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        clone_full_mock.assert_not_called()
        run_mock.assert_not_called()

    @pytest.mark.parametrize(
        "git_url",
        ["https://github.com/org/repo.git", "http://github.com/org/repo.git"],
    )
    def test_raises_for_non_ssh_remote(self, monkeypatch, git_url):
        clone_full_mock = mock.Mock()
        monkeypatch.setattr(runner, "_clone_full", clone_full_mock)
        run_mock = mock.Mock()
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url=git_url,
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        clone_full_mock.assert_not_called()

    def test_checks_out_fetch_head_not_pushing(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="produced999\n"),  # rev-parse FETCH_HEAD
                mock.Mock(returncode=0),  # merge-base --is-ancestor
                mock.Mock(returncode=0),  # git checkout --detach FETCH_HEAD
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        result = runner.prepare_review_clone(
            git_url="git@x:y.git",
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )

        assert result == clean_clone_dir
        checkout_argv = run_mock.call_args_list[3].args[0]
        assert checkout_argv == [
            "git",
            "-C",
            clean_clone_dir,
            "checkout",
            "--detach",
            "FETCH_HEAD",
        ]
        # Never a push -- `git push` must not appear in any call this
        # function makes at all.
        for call in run_mock.call_args_list:
            assert "push" not in call.args[0]

    def test_isolates_host_global_and_system_git_config(self, monkeypatch, tmp_path):
        """Codex code-review P2, branch 3 round 7: a runner host with a
        globally/system-configured filter driver (Git LFS being the
        common case) would have the checkout below invoke it for a
        `.gitattributes` entry naming that driver -- entirely
        attacker-controlled since it's part of the reviewed commit.
        `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` must be set
        on every git subprocess call this function makes after the
        initial clone, not just the checkout itself."""
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="produced999\n"),  # rev-parse FETCH_HEAD
                mock.Mock(returncode=0),  # merge-base --is-ancestor
                mock.Mock(returncode=0),  # git checkout --detach FETCH_HEAD
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)

        runner.prepare_review_clone(
            git_url="git@x:y.git",
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )

        # `resolve_commit_sha`'s own `rev-parse` call is a read-only ref
        # lookup -- it never triggers a working-tree filter/smudge driver,
        # so it's the one call in this sequence that legitimately doesn't
        # need this isolation.
        checked_calls = [
            call for call in run_mock.call_args_list if "rev-parse" not in call.args[0]
        ]
        assert len(checked_calls) == 3
        for call in checked_calls:
            env = call.kwargs.get("env") or {}
            assert env.get("GIT_CONFIG_NOSYSTEM") == "1"
            assert env.get("GIT_CONFIG_GLOBAL") == "/dev/null"

    def test_clone_isolates_host_global_and_system_git_config(self, monkeypatch, settings):
        """Same isolation, but for the initial `_clone_full` call itself
        -- verified by checking the `clone_env` this function passes
        through, since `_clone_full` merges it into the actual clone
        subprocess's own environment."""
        settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"
        clone_full_mock = mock.Mock(return_value="/tmp/clean-clone")
        monkeypatch.setattr(runner, "_clone_full", clone_full_mock)
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(
                side_effect=[
                    mock.Mock(returncode=0),
                    mock.Mock(returncode=0, stdout="produced999\n"),
                    mock.Mock(returncode=0),
                    mock.Mock(returncode=0),
                ]
            ),
        )

        runner.prepare_review_clone(
            git_url="git@x:y.git",
            default_branch="main",
            branch_name="harness/p3-1",
            bundle_path="/tmp/out.bundle",
            base_sha="base111",
            produced_sha="produced999",
        )

        clone_env = clone_full_mock.call_args.kwargs["clone_env"]
        assert clone_env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert clone_env["GIT_CONFIG_GLOBAL"] == "/dev/null"

    def test_sha_mismatch_raises_and_never_checks_out(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="unexpected-sha\n"),  # rev-parse FETCH_HEAD
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        assert run_mock.call_count == 2

    def test_non_ancestor_base_raises_and_never_checks_out(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),
                mock.Mock(returncode=0, stdout="produced999\n"),
                mock.Mock(returncode=1),  # merge-base --is-ancestor fails
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        monkeypatch.setattr(runner.shutil, "rmtree", mock.Mock())

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        assert run_mock.call_count == 3

    def test_ancestor_check_timeout_still_cleans_up(self, monkeypatch, tmp_path):
        """Codex code-review P2, branch 3 round 10: unlike the fetch/
        checkout calls, the merge-base ancestor check had no timeout
        handling of its own -- a raw `subprocess.TimeoutExpired`
        propagated past the `except RunnerError:` cleanup at the bottom
        of this function, leaking the clean-clone checkout in /tmp with
        nothing left to remove it."""
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        run_mock = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0),  # git fetch
                mock.Mock(returncode=0, stdout="produced999\n"),  # rev-parse FETCH_HEAD
                subprocess.TimeoutExpired(cmd="git merge-base", timeout=30),
            ]
        )
        monkeypatch.setattr(runner.subprocess, "run", run_mock)
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        rmtree.assert_called_once()

    def test_cleans_up_clean_clone_on_failure(self, monkeypatch, tmp_path):
        clean_clone_dir = str(tmp_path / "clean-clone")
        self._mock_clean_clone(monkeypatch, clean_clone_dir)
        monkeypatch.setattr(
            runner.subprocess,
            "run",
            mock.Mock(side_effect=subprocess.CalledProcessError(1, "git")),
        )
        rmtree = mock.Mock()
        monkeypatch.setattr(runner.shutil, "rmtree", rmtree)

        with pytest.raises(runner.RunnerError):
            runner.prepare_review_clone(
                git_url="git@x:y.git",
                default_branch="main",
                branch_name="harness/p3-1",
                bundle_path="/tmp/out.bundle",
                base_sha="base111",
                produced_sha="produced999",
            )
        rmtree.assert_called_once_with(clean_clone_dir, ignore_errors=True)


class TestReadImplementationResult:
    def test_reads_and_parses_a_valid_result_file(self, tmp_path):
        (tmp_path / "result.json").write_text('{"tests": {"passed": true}}')
        result = runner.read_implementation_result(str(tmp_path))
        assert result == {"tests": {"passed": True}}

    def test_missing_file_raises_runner_error(self, tmp_path):
        with pytest.raises(runner.RunnerError):
            runner.read_implementation_result(str(tmp_path))

    def test_malformed_json_raises_runner_error(self, tmp_path):
        (tmp_path / "result.json").write_text("not json")
        with pytest.raises(runner.RunnerError):
            runner.read_implementation_result(str(tmp_path))

    def test_symlink_result_path_raises_runner_error(self, tmp_path):
        """A pre-planted symlink at the result path must never be followed
        -- host-side defense in depth mirroring the wrapper's own
        O_NOFOLLOW discipline (brief §boundary)."""
        real_target = tmp_path / "elsewhere.json"
        real_target.write_text('{"tests": {"passed": true}}')
        symlink_path = tmp_path / "result.json"
        symlink_path.symlink_to(real_target)
        with pytest.raises(runner.RunnerError):
            runner.read_implementation_result(str(tmp_path))

    def test_fifo_result_path_raises_runner_error(self, tmp_path):
        """Codex code-review P2, branch 1 round 5: a FIFO planted at the
        result path (in this agent-writable workspace) could block this
        function's `open()` indefinitely, or hand back data from
        something that isn't a real file at all -- must be refused via a
        regular-file check before ever attempting to open it."""
        fifo_path = tmp_path / "result.json"
        os.mkfifo(fifo_path)
        with pytest.raises(runner.RunnerError):
            runner.read_implementation_result(str(tmp_path))
