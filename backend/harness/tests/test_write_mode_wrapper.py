"""Real-git tests for the shared write-mode wrapper logic
(`harness/docker/harness_write_mode.sh`, Phase 3 branch 5 -- plan-review
round 9 item 1).

Each test builds a throwaway repo under `tmp_path`, sources the library
exactly as the production wrappers do, stubs `run_cli` with a scripted
"agent" and runs `harness_write_mode_main`. `quiesce` is overridden to a
no-op: outside a container's PID namespace it would signal every process
the test runner's uid owns. Everything else -- git state restoration,
wrapper-owned commit, pristine test checkout, dirty-tree check, result
file and output framing -- runs for real.
"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.implementation_execution import CLI_OUTPUT_PREFIX

LIBRARY = Path(__file__).resolve().parents[1] / "docker" / "harness_write_mode.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("node") is None or shutil.which("base64") is None,
    reason="write-mode wrapper tests need git, node and base64 on PATH",
)

_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_ENV},
    ).stdout


def _make_workspace(tmp_path, files):
    workspace = tmp_path / "workspace"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    return workspace, repo, base


def _run_wrapper(
    workspace, *, base, agent_script, test_command, cli_output='{"ok": true}', cli_exit=0, env=None
):
    driver = workspace.parent / "driver.sh"
    driver.write_text(
        "set -eu\n"
        f'. "{LIBRARY}"\n'
        f'HARNESS_WORKSPACE_DIR="{workspace}"\n'
        "quiesce() { :; }\n"
        'goal="$HW_GOAL"\n'
        'test_command="$HW_TEST_COMMAND"\n'
        'base_commit="$HW_BASE"\n'
        "run_cli() {\n"
        '    (cd "$repo_dir" && sh -c "$HW_AGENT_SCRIPT") >/dev/null 2>&1 || true\n'
        '    printf "%s" "$HW_CLI_OUTPUT"\n'
        '    return "$HW_CLI_EXIT"\n'
        "}\n"
        "harness_write_mode_main\n"
    )
    run_env = {
        **os.environ,
        **_GIT_ENV,
        "HW_GOAL": "Implement the thing\nsecond line",
        "HW_TEST_COMMAND": test_command,
        "HW_BASE": base,
        "HW_AGENT_SCRIPT": agent_script,
        "HW_CLI_OUTPUT": cli_output,
        "HW_CLI_EXIT": str(cli_exit),
        **(env or {}),
    }
    proc = subprocess.run(
        ["sh", str(driver)], capture_output=True, text=True, env=run_env, timeout=60
    )
    result = json.loads((workspace / "result.json").read_text())
    return proc, result


def _framed_output(stdout):
    lines = [line for line in stdout.splitlines() if line.startswith(CLI_OUTPUT_PREFIX + " ")]
    assert lines, stdout
    return base64.b64decode(lines[-1].split(" ", 1)[1]).decode()


def test_happy_path_commits_everything_and_frames_cli_output(tmp_path):
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py && printf 'x\\n' > 'b c.py'",
        test_command="grep -q new a.py && test -f 'b c.py'",
        cli_output='{"total_cost_usd": 0.1}',
    )
    assert result["tests"] == {
        "ran": True,
        "exit_code": 0,
        "passed": True,
        "command": "grep -q new a.py && test -f 'b c.py'",
    }
    assert result["no_changes"] is False
    assert sorted(result["changed_files"]) == ["a.py", "b c.py"]
    assert result["commit_sha"] == _git(repo, "rev-parse", "HEAD").strip()
    assert result["commit_sha"] != base
    assert _git(repo, "show", f"{result['commit_sha']}:a.py") == "new\n"
    assert "Implement the thing" in _git(repo, "log", "-1", "--format=%B")
    assert _framed_output(proc.stdout) == '{"total_cost_usd": 0.1}'


def test_no_change_run_fails_closed_without_running_tests(tmp_path):
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace, base=base, agent_script="true", test_command="touch ran-marker"
    )
    assert result["no_changes"] is True
    assert result["tests"]["ran"] is False
    assert result["tests"]["passed"] is False
    assert not (workspace / "repo" / "ran-marker").exists()


def test_ignored_helper_file_is_removed_before_tests(tmp_path):
    workspace, _repo, base = _make_workspace(
        tmp_path, {"a.py": "old\n", ".gitignore": "helper.txt\n"}
    )
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py && touch helper.txt",
        test_command="test -f helper.txt",
    )
    assert "helper.txt" not in result["changed_files"]
    assert result["tests"]["ran"] is True
    assert result["tests"]["passed"] is False


def test_info_exclude_cannot_hide_a_file_from_the_commit(tmp_path):
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script=(
            "printf 'new\\n' > a.py && touch conftest.py && echo conftest.py >> .git/info/exclude"
        ),
        test_command="test -f conftest.py",
    )
    # The file the tests depended on is part of what gets reviewed/pushed.
    assert "conftest.py" in result["changed_files"]
    assert result["tests"]["passed"] is True
    _git(repo, "cat-file", "-e", f"{result['commit_sha']}:conftest.py")


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_cannot_hide_a_tracked_modification(tmp_path, flag):
    workspace, repo, base = _make_workspace(tmp_path, {"a b.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script=f"printf 'new\\n' > 'a b.py' && git update-index {flag} 'a b.py'",
        test_command="grep -q new 'a b.py'",
    )
    assert result["changed_files"] == ["a b.py"]
    assert result["tests"]["passed"] is True
    assert _git(repo, "show", f"{result['commit_sha']}:a b.py") == "new\n"


def test_lying_clean_filter_is_dropped_before_commit(tmp_path):
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n", "b.py": "x\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script=(
            "printf 'new\\n' > a.py && mkdir -p .git/info "
            "&& echo 'a.py filter=lie' > .git/info/attributes "
            "&& git config filter.lie.clean 'sed s/new/old/' && printf 'y\\n' > b.py"
        ),
        test_command="grep -q new a.py",
    )
    assert _git(repo, "show", f"{result['commit_sha']}:a.py") == "new\n"
    assert result["tests"]["passed"] is True


def test_test_command_modifying_tracked_files_fails_closed(tmp_path):
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py",
        test_command="printf 'changed-by-tests\\n' > a.py",
    )
    assert result["tests"]["ran"] is True
    assert result["tests"]["passed"] is False


def test_failed_cli_never_commits_or_tests(tmp_path):
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py",
        test_command="true",
        cli_exit=3,
    )
    assert result["tests"]["ran"] is False
    assert result["tests"]["passed"] is False
    assert _git(repo, "rev-parse", "HEAD").strip() == base


def test_test_command_does_not_see_cli_api_keys(tmp_path):
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py",
        test_command='test -z "${ANTHROPIC_API_KEY:-}" && test -z "${CODEX_API_KEY:-}"',
        env={"ANTHROPIC_API_KEY": "sk-test-not-real", "CODEX_API_KEY": "sk-test-not-real"},
    )
    assert result["tests"]["passed"] is True


def test_replaced_git_dir_fails_closed(tmp_path):
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script=("printf 'new\\n' > a.py && mv .git ../real-git && ln -s ../real-git .git"),
        test_command="true",
    )
    assert result["tests"]["passed"] is False
    assert result["commit_sha"] == ""


def test_newline_in_path_is_reported_as_one_changed_file(tmp_path):
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'x' > \"$(printf 'we\\nird.py')\"",
        test_command="true",
    )
    assert result["changed_files"] == ["we\nird.py"]
    assert result["tests"]["passed"] is True


def test_agent_commits_are_squashed_into_one_wrapper_commit(tmp_path):
    """Codex code-review P1, branch 5 round 1: commits the agent made itself
    (whose messages or intermediate trees could carry data the final diff
    doesn't show) must never be bundled -- the produced history is exactly
    one wrapper commit whose parent is the base."""
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script=(
            "printf 'secret\\n' > tmp.txt && git add -A && "
            "git -c user.name=a -c user.email=a@x.invalid commit -qm 'sk-hidden-in-message' && "
            "git rm -q tmp.txt && printf 'new\\n' > a.py && git add -A && "
            "git -c user.name=a -c user.email=a@x.invalid commit -qm second"
        ),
        test_command="grep -q new a.py",
    )
    head = result["commit_sha"]
    assert _git(repo, "rev-parse", f"{head}^").strip() == base
    assert _git(repo, "rev-list", "--count", f"{base}..{head}").strip() == "1"
    message = _git(repo, "log", "-1", "--format=%B", head)
    assert "sk-hidden-in-message" not in message
    assert message.startswith("harness: implementation")
    assert _git(repo, "log", "-1", "--format=%an", head).strip() == "Friday Harness"
    assert result["changed_files"] == ["a.py"]
    assert result["tests"]["passed"] is True


def test_agent_switching_branches_cannot_redirect_the_commit(tmp_path):
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="git checkout -q -b elsewhere && printf 'new\\n' > a.py",
        test_command="grep -q new a.py",
    )
    assert _git(repo, "rev-parse", "main").strip() == result["commit_sha"]
    assert result["tests"]["passed"] is True


def test_failing_git_diff_after_tests_fails_closed(tmp_path):
    """Codex code-review P2, branch 5 round 3: a `git diff` failure must not
    be masked by the pipeline's last command into an empty change list."""
    workspace, _repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    base_object = f".git/objects/{base[:2]}/{base[2:]}"
    _proc, result = _run_wrapper(
        workspace,
        base=base,
        agent_script="printf 'new\\n' > a.py",
        # The test command itself removes the base commit object after the
        # wrapper committed on top of it.
        test_command=f"chmod u+w {base_object}; rm -f {base_object}",
    )
    assert result["tests"]["passed"] is False


def test_driver_mode_streams_the_agent_stdio_then_runs_the_wrapper_sequence(tmp_path):
    """Phase 10 D10: in driver mode the agent's server speaks over the
    container's stdin/stdout (here: echoes each stdin line back with a
    prefix until EOF) -- nothing is captured -- and once the host closes
    stdin the wrapper commits, runs the tests and writes the result exactly
    as on the CLI path, with an empty final frame."""
    workspace, repo, base = _make_workspace(tmp_path, {"a.py": "old\n"})
    driver = workspace.parent / "driver.sh"
    driver.write_text(
        "set -eu\n"
        f'. "{LIBRARY}"\n'
        f'HARNESS_WORKSPACE_DIR="{workspace}"\n'
        "quiesce() { :; }\n"
        'goal="$HW_GOAL"\n'
        'test_command="$HW_TEST_COMMAND"\n'
        'base_commit="$HW_BASE"\n'
        "harness_driver_mode=true\n"
        "run_cli() {\n"
        '    (cd "$repo_dir" && printf "new\\n" > a.py)\n'
        '    while IFS= read -r line; do printf "AGENT %s\\n" "$line"; done\n'
        "}\n"
        "harness_write_mode_main\n"
    )
    proc = subprocess.run(
        ["sh", str(driver)],
        input='{"id": 1, "method": "initialize"}\n{"method": "initialized"}\n',
        capture_output=True,
        text=True,
        env={
            **os.environ,
            **_GIT_ENV,
            "HW_GOAL": "driver goal",
            "HW_TEST_COMMAND": "grep -q new a.py",
            "HW_BASE": base,
        },
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == 'AGENT {"id": 1, "method": "initialize"}'
    assert lines[1] == 'AGENT {"method": "initialized"}'
    result = json.loads((workspace / "result.json").read_text())
    assert result["tests"]["passed"] is True
    assert result["changed_files"] == ["a.py"]
    assert result["commit_sha"] == _git(repo, "rev-parse", "HEAD").strip()
    assert _framed_output(proc.stdout) == ""


def test_codex_wrapper_runs_the_app_server_only_in_driver_mode():
    script = (LIBRARY.parent / "codex_cli_run.sh").read_text()
    assert "codex app-server 2>/dev/null" in script
    assert '"$driver" = "app_server"' in script
    assert "unknown driver" in script
    # The one-shot path is unchanged.
    assert 'codex exec --sandbox workspace-write "$goal" 2>&1' in script
