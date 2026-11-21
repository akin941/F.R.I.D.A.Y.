"""The throwaway-worktree mode (Phase 10 brief D4, D11).

`worktree` mode creates a detached `git worktree` at `HEAD` outside the
source repo, runs the mapped command there, and deletes it; afterwards
`git worktree list` of the source repo is unchanged (an escape-suite
check). Git runs with a minimal environment and hooks disabled, so nothing
in the repo runs during setup or teardown.
"""

import os
import shutil
import subprocess
import uuid

_GIT_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LANG": "C"}


class WorktreeError(RuntimeError):
    pass


def _git(repo_root, *args):
    git = shutil.which("git", path=_GIT_ENV["PATH"])
    if git is None:
        raise WorktreeError("git is not installed")
    env = {**_GIT_ENV, "HOME": os.path.dirname(repo_root), "GIT_CONFIG_NOSYSTEM": "1"}
    completed = subprocess.run(  # noqa: S603 - argv list, no shell
        [git, "-c", "core.hooksPath=/dev/null", "-C", repo_root, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise WorktreeError(f"git {args[0]} failed")
    return completed.stdout


def list_worktrees(repo_root) -> list:
    """`git worktree list --porcelain` paths of `repo_root`."""
    out = _git(repo_root, "worktree", "list", "--porcelain")
    return [line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")]


def create_worktree(repo_root, base_dir) -> str:
    """A detached worktree at HEAD in a fresh directory under `base_dir`."""
    path = os.path.join(base_dir, f"wt-{uuid.uuid4().hex[:12]}")
    _git(repo_root, "worktree", "add", "--detach", path, "HEAD")
    return os.path.realpath(path)


def remove_worktree(repo_root, path) -> None:
    """Remove the worktree and prune its metadata; the directory is gone
    afterwards whatever the command left in it."""
    try:
        _git(repo_root, "worktree", "remove", "--force", "--force", path)
    except WorktreeError:
        pass
    shutil.rmtree(path, ignore_errors=True)
    _git(repo_root, "worktree", "prune")
