"""Harness's external-CLI-agent runner (Phase 2 branch 2, brief §2/§3).

Every `git`/`docker` subprocess invocation lives behind the small functions
below so `harness.tasks` never shells out directly. `harness.tasks` calls
these through the `harness.runner` module (not `from harness.runner import
X`), so tests can `monkeypatch.setattr("harness.runner.<name>", fake)` and
never invoke a real subprocess or Docker daemon -- mirrors CLAUDE.md rule
5's "LLM-calling code must be mockable" extended to this out-of-process
call. A real Docker-stack smoke test against the actual CLI binary is
deliberately NOT part of the automated suite (nightly/manual only, same
carve-out rule 5 already gives a real-LLM test).

Container policy this module enforces (brief, Codex round-3/4 fixes):
argv-only invocation (never a shell string -- the job's `goal` and repo
content are both untrusted text), an explicit env allowlist for the CLI
process (never the worker's inherited environment), the clone bind-mounted
read-only with no other host path exposed, a job-scoped HOME/config
directory living inside the container's own ephemeral filesystem, a
restricted-egress Docker network, and per-container resource limits.

Phase 3 branch 1 (`docs/prompts/phase-3-harness-v2/brief.md`) adds the
writable counterparts used only by `HarnessImplementationJob`
(`clone_repo_writable`/`launch_implementation_container`, a read-write
mount -- never `HarnessJob`/`HarnessReview`, which stay on the read-only
path above) and the bundle-only clean-clone push primitives
(`resolve_commit_sha`/`create_bundle`/`push_bundled_commit`): the actual
`git push` never runs against the agent-writable worktree, only from a
freshly, host-only-cloned copy of the repo, after an 8-round plan-review
arc converged on this as the only design that closes every
`.git/config`/hook-based attack an agent-writable directory could mount
against a host-side push (see the brief's boundary section for the full
history). `read_implementation_result` reads the wrapper's own result
file directly off the host filesystem -- never through container
stdout/stderr, which the CLI agent or the configured test command could
freely pollute with a spoofed-looking result.
"""

import json
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)

# Mirrors `harness/serializers.py`'s identical scp-like detection --
# purposely not imported from there (this module stays independent of
# the request-validation layer), used here for a different reason: git
# ignores `GIT_SSH_COMMAND` entirely for non-SSH remotes (Codex
# code-review P1, branch 1 round 5 -- see `_reject_non_ssh_remote`'s
# docstring below).
_SCP_LIKE_RE = re.compile(r"^[^@/:]+@[^/:]+:")

_GIT_CLONE_TIMEOUT_SECONDS = 120
_CONTAINER_LAUNCH_TIMEOUT_SECONDS = 30
_DOCKER_CONTROL_TIMEOUT_SECONDS = 30
_GIT_PLUMBING_TIMEOUT_SECONDS = 30
_BUNDLE_TIMEOUT_SECONDS = 60
_GIT_FETCH_TIMEOUT_SECONDS = 60
_GIT_PUSH_TIMEOUT_SECONDS = 60

# Codex code-review P2, branch 3 round 7: git consults the runner HOST's
# own global (`~/.gitconfig`)/system (`/etc/gitconfig`) config for things
# like filter-driver commands, in addition to whatever repo-local config
# a clean clone's own `git clone`/`fetch` wrote -- a host with e.g. Git
# LFS installed globally has `filter.lfs.*` defined there, and a
# `.gitattributes` entry naming that driver (entirely attacker-controlled
# when it's part of a reviewed commit) would have it invoked during
# checkout. `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null` are git's
# own documented mechanism for excluding both from a given invocation,
# used by `prepare_review_clone` so only the clean clone's own local
# config (never written by reviewed content) is ever consulted.
_ISOLATED_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}

_AGENT_IMAGES = {
    "claude_code": lambda: settings.HARNESS_IMAGE_CLAUDE_CODE,
    "codex_cli": lambda: settings.HARNESS_IMAGE_CODEX_CLI,
}


class RunnerError(Exception):
    """A runner-level operation (clone/launch/stop) failed unrecoverably."""


def image_for_agent(agent):
    return _AGENT_IMAGES[agent]()


def clone_repo(*, git_url, default_branch, clone_env):
    """Read-only, shallow clone into a fresh ephemeral host directory.
    Returns the directory path -- the caller must call
    `cleanup_directory()` on every exit path, cancelled or not.

    `clone_env` carries only the clone credential (resolved from env by
    the caller per `HarnessRepo`'s auth convention, CLAUDE.md "secrets
    only via env") -- it is never passed on to the container launch below,
    which gets its own, separate CLI-auth env allowlist.
    """
    directory = tempfile.mkdtemp(prefix="harness-clone-")
    try:
        # `--` terminates option parsing (standard git/getopt convention):
        # everything after it is positional, so a curator-supplied
        # `git_url` starting with `-` (accepted by `harness.serializers`'
        # validator today -- it only rejects embedded credentials/
        # query-fragments, not a leading dash) can never be parsed as a
        # git option instead of the repository to clone (Codex code-review
        # P2, branch 2 round 7).
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", default_branch, "--", git_url, directory],
            check=True,
            capture_output=True,
            env={**os.environ, **clone_env},
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        cleanup_directory(directory)
        raise RunnerError(f"git clone failed: {exc}") from exc
    return directory


def cleanup_directory(path):
    """Best-effort recursive delete that never raises; callers confirm the
    outcome themselves with `os.path.exists` afterwards.

    Phase 3 branch 5 (2026-09-27 audit): the agent owns every path in its
    worktree and can strip owner permissions from directories (`chmod 500`
    / `000`) so their entries can't be listed or unlinked -- a plain
    `rmtree(..., ignore_errors=True)` then left the tree behind forever.
    The host runs as that same uid, and chmod(2) checks ownership rather
    than mode bits, so a pre-pass restores owner rwx on every directory
    inside `path` (never following symlinks, never touching anything
    outside it) before the delete. Replaces an `onexc` retry handler that
    could itself raise when rmtree reported an `os.open` failure (Codex
    code-review P1, branch 5 round 3)."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            _restore_owner_access(path)
    except Exception:
        logger.exception("Restoring owner access under %s failed", path)
    shutil.rmtree(path, ignore_errors=True)


def _restore_owner_access(root):
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            os.chmod(directory, stat.S_IRWXU)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            continue


def _resolve_git_push_env():
    """The push/clean-clone credential (Phase 3 brief §boundary), resolved
    from env only -- never stored on `HarnessRepo`. Raises `RunnerError`
    if unconfigured (Codex code-review P1, branch 1 round 3: an earlier
    version returned `{}` here, which the caller then merged into
    `os.environ` -- on a runner host that already has ambient repo access
    (an `SSH_AUTH_SOCK` from a running agent, a default `~/.ssh` key),
    the push could silently succeed using THOSE credentials instead of
    being refused, contradicting this setting's own "fails closed, not a
    silent no-auth attempt" contract). A repo needing push access
    genuinely cannot be used for that until this is configured -- there
    is no safe ambient fallback."""
    key_path = settings.HARNESS_GIT_SSH_KEY_PATH
    if not key_path:
        raise RunnerError(
            "HARNESS_GIT_SSH_KEY_PATH is not configured; cannot clone/push with write credentials"
        )
    # Codex code-review P2, branch 1 round 7: `GIT_SSH_COMMAND` is parsed by
    # a shell when git invokes it -- an unquoted key_path containing spaces
    # or shell metacharacters (e.g. a macOS home directory with a space)
    # would be split/expanded before `ssh` ever sees it. shlex.quote makes
    # this safe regardless of what the configured path contains.
    quoted_key_path = shlex.quote(key_path)
    return {
        "GIT_SSH_COMMAND": (
            f"ssh -i {quoted_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )
    }


def _reject_non_ssh_remote(git_url):
    """Push credentials are SSH-only (Codex code-review P1, branch 1
    round 5): `GIT_SSH_COMMAND` -- the whole mechanism
    `_resolve_git_push_env()` uses -- has no effect at all on an HTTPS
    remote; Git uses its own separate credential-helper/`~/.netrc`/global-
    config mechanism for HTTPS, none of which this module's env-only
    credential contract has any control over. A registered repo with a
    credential-free HTTPS `git_url` would otherwise silently fall through
    to the runner host's own ambient HTTPS credentials for push -- exactly
    the "fails closed, no ambient fallback" property
    `_resolve_git_push_env()` exists to guarantee, just for a different
    protocol. Raises `RunnerError` for anything that isn't `ssh://` or the
    scp-like `user@host:path` form; HTTPS-remote repos simply cannot be
    used for `HarnessImplementationJob` until explicit HTTPS credential
    support is designed as its own feature, not silently half-supported."""
    if git_url.startswith("ssh://") or _SCP_LIKE_RE.match(git_url):
        return
    raise RunnerError(
        f"push credentials are SSH-only; {git_url!r} is not an SSH remote "
        "(GIT_SSH_COMMAND has no effect on HTTPS, which would silently fall "
        "back to the runner host's own ambient credentials)"
    )


def clone_repo_writable(*, git_url, default_branch, branch_name, clone_env):
    """The writable counterpart to `clone_repo` above -- used only for
    `HarnessImplementationJob` (never `HarnessJob`/`HarnessReview`, which
    stay on the read-only shallow-clone path above). Returns a workspace
    directory whose `repo/` subdirectory is a full (non-shallow) clone
    with `branch_name` already checked out. The workspace directory's
    other contents (never inside `repo/`) are where the wrapper's own
    result file lands -- see `launch_implementation_container` below; a
    sibling path outside the repo's own working tree can never collide
    with or be shadowed by a file the agent's own commit added. The
    caller must call `cleanup_directory()` on the returned workspace
    directory on every exit path, same as `clone_repo`."""
    workspace_dir = tempfile.mkdtemp(prefix="harness-worktree-")
    repo_dir = os.path.join(workspace_dir, "repo")
    try:
        subprocess.run(
            ["git", "clone", "--branch", default_branch, "--", git_url, repo_dir],
            check=True,
            capture_output=True,
            env={**os.environ, **clone_env},
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
        )
        subprocess.run(
            ["git", "-C", repo_dir, "checkout", "-b", branch_name],
            check=True,
            capture_output=True,
            timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        cleanup_directory(workspace_dir)
        raise RunnerError(f"writable clone/branch setup failed: {exc}") from exc
    return workspace_dir


def resolve_commit_sha(directory, ref):
    """`git rev-parse --verify` a ref inside `directory` -- returns the
    resolved SHA, or raises `RunnerError` if the ref doesn't resolve
    (never silently returns something a caller might mistake for
    success).

    A timeout is also converted to `RunnerError` (Codex code-review P2,
    branch 3 round 14): both of this function's callers (`push_bundled_
    commit`, `prepare_review_clone`) wrap their own clone-preparation
    steps in `except RunnerError: cleanup_directory(...); raise` -- a raw
    `subprocess.TimeoutExpired` bypasses that entirely, leaking the full
    clean-clone checkout on the runner host with no caller left holding
    its path."""
    try:
        result = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"resolving {ref!r} in {directory} timed out: {exc}") from exc
    if result.returncode != 0:
        raise RunnerError(f"failed to resolve {ref!r} in {directory}: {result.stderr.strip()}")
    return result.stdout.strip()


def create_bundle(directory, *, base_sha, ref, bundle_path):
    """`git bundle create` for the incremental range `base_sha..ref`
    (Phase 3 brief §boundary, rounds 6-8: a NAMED ref, never a bare SHA
    -- `git bundle create` itself rejects a bare revision with no
    accompanying ref) -- a local, non-networked operation run against
    the agent's own worktree: no hook fires for it, no credential
    helper/SSH-override/URL-rewrite rule applies, since none of those
    are relevant to an operation that never touches a remote. Safe to
    run here specifically because bundling is local-only; the actual
    network operation (push) never runs against this directory -- see
    `push_bundled_commit` below."""
    try:
        subprocess.run(
            [
                "git",
                "-C",
                directory,
                "-c",
                "core.fsmonitor=",
                "bundle",
                "create",
                bundle_path,
                f"{base_sha}..{ref}",
            ],
            check=True,
            capture_output=True,
            # Host global/system config never applies to a read of the
            # agent's worktree (Phase 3 branch 5), same isolation as the
            # clean-clone operations below.
            env={**os.environ, **_ISOLATED_GIT_ENV, "GIT_NO_REPLACE_OBJECTS": "1"},
            timeout=_BUNDLE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(f"git bundle create failed: {exc}") from exc


def diff_contains_any(directory, *, base_sha, head_sha, needles):
    """`True` if any object reachable from `head_sha` but not from
    `base_sha` -- commits (messages, authors), trees (file names) and
    blobs (raw file bytes, binary included) -- contains any of `needles`
    as a literal substring (Phase 3 branch 5, 2026-09-27 audit: the
    implementing agent's shell holds the CLI API key, and everything in
    that range is bundled for review and later pushed). Scans raw object
    content via `git cat-file --batch`, not diff text: a binary patch or a
    commit message would never show the literal value in a diff (Codex
    code-review P1, branch 5 round 1). A literal scan is best-effort -- an
    encoded key is not caught -- which is why the key is also kept out of
    the test command's environment. Host global/system config is isolated
    and repo-configured fsmonitor disabled. Raises `RunnerError` if the
    objects can't be read -- callers fail closed."""
    needle_bytes = [n.encode("utf-8") for n in needles if n]
    if not needle_bytes:
        return False
    git_env = {**os.environ, **_ISOLATED_GIT_ENV, "GIT_NO_REPLACE_OBJECTS": "1"}
    base_git = ["git", "-C", directory, "-c", "core.fsmonitor="]
    try:
        listing = subprocess.run(
            [*base_git, "rev-list", "--objects", head_sha, f"^{base_sha}", "--"],
            capture_output=True,
            env=git_env,
            timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
        )
        if listing.returncode != 0:
            raise RunnerError(f"listing objects {base_sha}..{head_sha} failed")
        object_ids = [line.split(b" ", 1)[0] for line in listing.stdout.splitlines() if line]
        if not object_ids:
            return False
        contents = subprocess.run(
            [*base_git, "cat-file", "--batch"],
            input=b"\n".join(object_ids) + b"\n",
            capture_output=True,
            env=git_env,
            timeout=_BUNDLE_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(f"reading objects {base_sha}..{head_sha} failed: {exc}") from exc
    if contents.returncode != 0:
        raise RunnerError(f"reading objects {base_sha}..{head_sha} failed")
    return any(
        needle in body for body in _iter_cat_file_batch(contents.stdout) for needle in needle_bytes
    )


def _iter_cat_file_batch(stream):
    """Yields each object body from `git cat-file --batch` output, parsed
    by its `<sha> <type> <size>` header (Codex code-review P2, branch 5
    round 2: a substring check for the `missing` marker over the raw stream
    false-positived on ordinary file content). A `missing` header or a
    truncated stream raises `RunnerError`."""
    position = 0
    while position < len(stream):
        header_end = stream.find(b"\n", position)
        if header_end == -1:
            raise RunnerError("truncated git cat-file output")
        header = stream[position:header_end].split(b" ")
        if len(header) != 3:
            raise RunnerError(f"unexpected git cat-file header: {header!r}")
        try:
            size = int(header[2])
        except ValueError as exc:
            raise RunnerError(f"unexpected git cat-file header: {header!r}") from exc
        body_start = header_end + 1
        body_end = body_start + size
        if body_end > len(stream):
            raise RunnerError("truncated git cat-file output")
        yield stream[body_start:body_end]
        position = body_end + 1


def diff_stat(directory, *, base_sha, head_sha):
    """`git diff --stat` for `base_sha..head_sha` in the agent's worktree,
    with the same isolation as `diff_contains_any` (Phase 3 branch 6: the
    input to the gateway-produced `diff_summary`). Raises `RunnerError` on
    failure -- the caller falls back to a deterministic summary."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                directory,
                "-c",
                "core.fsmonitor=",
                "-c",
                "core.quotePath=true",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--stat=200,150",
                base_sha,
                head_sha,
                "--",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **_ISOLATED_GIT_ENV, "GIT_NO_REPLACE_OBJECTS": "1"},
            timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(f"git diff --stat failed: {exc}") from exc
    if result.returncode != 0:
        raise RunnerError(f"git diff --stat failed: {result.stderr.strip()}")
    return result.stdout


def _clone_full(*, git_url, default_branch, clone_env):
    """A non-shallow clone -- the clean-clone counterpart to `clone_repo`
    above, used by `push_bundled_commit` and `prepare_review_clone`
    (Codex code-review P2, branch 1 round 1: `clone_repo`'s `--depth 1`
    shallow clone only contains `default_branch`'s CURRENT tip commit;
    if the remote has advanced since the implementation job's `base_sha`
    was recorded, a shallow clone doesn't actually contain `base_sha` at
    all, and fetching a bundle whose prerequisite is `base_sha` fails
    even though the produced commit is perfectly valid and reachable on
    the real remote). Full clone cost is acceptable here: this only runs
    once, at push/review time, not per-discovery-job."""
    directory = tempfile.mkdtemp(prefix="harness-clean-clone-")
    try:
        subprocess.run(
            ["git", "clone", "--branch", default_branch, "--", git_url, directory],
            check=True,
            capture_output=True,
            env={**os.environ, **clone_env},
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        cleanup_directory(directory)
        raise RunnerError(f"clean clone failed: {exc}") from exc
    return directory


def push_bundled_commit(
    *, git_url, default_branch, branch_name, bundle_path, base_sha, produced_sha
):
    """The only function in this module that ever pushes to a remote --
    and it does so from a freshly, host-only-cloned copy of the repo,
    never the agent's own worktree (Phase 3 brief §boundary, round-3
    CRITICAL fix): an agent-planted `.git/hooks/pre-push` or a rewritten
    `.git/config` (`credential.helper`, `core.sshCommand`,
    `url.*.insteadOf`, remote URL) in the agent's own worktree is never
    consulted, because this function never runs `git push`/`git fetch`
    with that worktree as its `-C`/cwd argument at all.

    Steps: a full (non-shallow) clone of `default_branch` via
    `_clone_full` (`base_sha` must actually be present in this clone's
    history, not just reachable in principle -- see that function's own
    docstring); fetch the bundle's named ref; verify the fetched commit's
    own SHA is exactly `produced_sha` (a direct string comparison, not
    inferred) and that `base_sha` is a real ancestor of it; only then
    push by explicit SHA with a create-only compare-and-swap
    (`--force-with-lease=<ref>:`, empty expected value -- Git's own
    documented form for "the ref must not already exist", fails closed
    rather than force-overwriting an existing remote branch).

    Raises `RunnerError` on any failure. The caller is responsible for
    cleaning up the returned-nothing clean-clone directory -- this
    function cleans up its own temporary clone on every exit path
    itself, since (unlike `clone_repo_writable`'s caller, who needs the
    directory to persist across the Gate-B wait) nothing outside this
    function ever needs it once this call returns."""
    _reject_non_ssh_remote(git_url)
    clean_clone_dir = _clone_full(
        git_url=git_url, default_branch=default_branch, clone_env=_resolve_git_push_env()
    )
    try:
        try:
            subprocess.run(
                ["git", "-C", clean_clone_dir, "fetch", bundle_path, f"refs/heads/{branch_name}"],
                check=True,
                capture_output=True,
                timeout=_GIT_FETCH_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"fetching bundle into clean clone failed: {exc}") from exc

        fetched_sha = resolve_commit_sha(clean_clone_dir, "FETCH_HEAD")
        if fetched_sha != produced_sha:
            raise RunnerError(
                f"fetched commit {fetched_sha} does not match reported "
                f"produced_commit_sha {produced_sha}"
            )
        try:
            ancestor_check = subprocess.run(
                [
                    "git",
                    "-C",
                    clean_clone_dir,
                    "merge-base",
                    "--is-ancestor",
                    base_sha,
                    "FETCH_HEAD",
                ],
                capture_output=True,
                timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(f"checking ancestry of {base_sha} timed out: {exc}") from exc
        if ancestor_check.returncode != 0:
            raise RunnerError(f"{base_sha} is not an ancestor of the produced commit")

        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    clean_clone_dir,
                    "push",
                    git_url,
                    f"FETCH_HEAD:refs/heads/{branch_name}",
                    f"--force-with-lease=refs/heads/{branch_name}:",
                ],
                check=True,
                capture_output=True,
                env={**os.environ, **_resolve_git_push_env()},
                timeout=_GIT_PUSH_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"git push failed: {exc}") from exc
    finally:
        cleanup_directory(clean_clone_dir)


def prepare_review_clone(
    *, git_url, default_branch, branch_name, bundle_path, base_sha, produced_sha
):
    """A clean, agent-config-untouched checkout of exactly the reviewed
    commits -- for `HarnessReview` (Phase 3 branch 3) to mount into the
    review container, NEVER the agent's own writable worktree directly
    (Codex code-review P1, branch 3 round 1): that worktree's
    `.git/config`/`.gitattributes` are fully agent-writable by the time
    review runs (implementation already completed), and `git diff`
    itself consults config-driven mechanisms like `diff.external`/
    `textconv` -- an agent-configured one could hide the real diff from
    the reviewer, or worse, execute arbitrary code inside the review
    container, which has the review CLI's own credentials in its
    environment. Mirrors `push_bundled_commit`'s exact same clean-clone
    threat model and its same three verification steps (fetch the named
    ref, confirm the fetched SHA is exactly `produced_sha`, confirm
    `base_sha` is a real ancestor of it) -- but checks the result out
    instead of pushing it, since nothing here ever touches the remote.

    A tracked `.gitattributes` naming a filter/diff driver only reaches a
    REPO-local `.git/config` no content from the reviewed commits ever
    writes to -- but git also consults the runner HOST's own global/system
    config for the same lookup, and that one this function doesn't
    control (Codex code-review P2, branch 3 round 7): if the host happens
    to have a filter driver configured there (Git LFS being the common
    case -- `git lfs install` writes `filter.lfs.*` into global/system
    config, not per-repo), a `.gitattributes` entry naming that driver,
    entirely attacker-controlled since it's part of the reviewed commit,
    would have the checkout below invoke it -- a host-side command
    execution/network-fetch the agent effectively triggers. `_ISOLATED_GIT_ENV`
    (`GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null`, git's own
    documented mechanism for this) is threaded through every subprocess
    call this function makes, so only the clean clone's own local config
    -- written exclusively by this function's `clone`/`fetch` calls, never
    by reviewed content -- is ever consulted; a filter named but not
    configured there is correctly treated as a no-op by git itself.

    Caller must call `cleanup_directory()` on the returned directory on
    every exit path, same as every other clone primitive in this
    module."""
    _reject_non_ssh_remote(git_url)
    directory = _clone_full(
        git_url=git_url,
        default_branch=default_branch,
        clone_env={**_resolve_git_push_env(), **_ISOLATED_GIT_ENV},
    )
    try:
        try:
            subprocess.run(
                ["git", "-C", directory, "fetch", bundle_path, f"refs/heads/{branch_name}"],
                check=True,
                capture_output=True,
                env={**os.environ, **_ISOLATED_GIT_ENV},
                timeout=_GIT_FETCH_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"fetching review bundle into clean clone failed: {exc}") from exc

        fetched_sha = resolve_commit_sha(directory, "FETCH_HEAD")
        if fetched_sha != produced_sha:
            raise RunnerError(
                f"fetched commit {fetched_sha} does not match reported "
                f"produced_commit_sha {produced_sha}"
            )
        try:
            ancestor_check = subprocess.run(
                ["git", "-C", directory, "merge-base", "--is-ancestor", base_sha, "FETCH_HEAD"],
                capture_output=True,
                env={**os.environ, **_ISOLATED_GIT_ENV},
                timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            # Codex code-review P2, branch 3 round 10: unlike the fetch/
            # checkout calls above and below, this one had no timeout
            # handling of its own -- a `TimeoutExpired` propagated
            # straight past the `except RunnerError:` cleanup at the
            # bottom of this function, leaking the full clean-clone
            # checkout (a copy of the reviewed repo) in /tmp with nothing
            # left to remove it.
            raise RunnerError(f"checking ancestry of {base_sha} timed out: {exc}") from exc
        if ancestor_check.returncode != 0:
            raise RunnerError(f"{base_sha} is not an ancestor of the produced commit")

        try:
            subprocess.run(
                ["git", "-C", directory, "checkout", "--detach", "FETCH_HEAD"],
                check=True,
                capture_output=True,
                env={**os.environ, **_ISOLATED_GIT_ENV},
                timeout=_GIT_PLUMBING_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"checking out reviewed commit failed: {exc}") from exc
    except RunnerError:
        cleanup_directory(directory)
        raise
    return directory


def read_implementation_result(workspace_dir):
    """Reads the wrapper's own result file directly off the host
    filesystem -- never through container stdout/stderr (Phase 3 brief
    §boundary, round-3 HIGH fix: the CLI agent or the test command could
    freely print text that *looks like* the expected result). Returns
    the parsed dict, or raises `RunnerError` if the path is missing, a
    symlink, or anything other than a plain regular file -- a FIFO or
    device node planted in this agent-writable workspace (Codex
    code-review P2, branch 1 round 5) could otherwise block this `open()`
    indefinitely or hand back data from something that isn't a real file
    at all, if the wrapper's own quiescence-then-remove-then-write
    sequence (round 3) somehow didn't run or didn't finish -- or if the
    result is simply unreadable or not valid JSON. `lstat` (not `stat`)
    is what makes the symlink check meaningful -- it reports the link
    itself, never follows it. Every one of these is a fail-closed case
    for the caller, never silently treated as "no result produced"."""
    result_path = os.path.join(workspace_dir, "result.json")
    try:
        file_stat = os.lstat(result_path)
    except OSError as exc:
        raise RunnerError(f"implementation result file unavailable: {exc}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise RunnerError("implementation result path is a symlink, refusing to read")
    if not stat.S_ISREG(file_stat.st_mode):
        raise RunnerError("implementation result path is not a regular file, refusing to read")
    try:
        with open(result_path, "rb") as f:
            raw_bytes = f.read()
    except OSError as exc:
        raise RunnerError(f"implementation result file unavailable: {exc}") from exc
    # Decoded inside the same fail-closed boundary as the JSON parse
    # (Phase 3 branch 5, 2026-09-27 audit): invalid UTF-8 -- reachable by
    # an agent committing a non-UTF-8 file name -- used to escape as a
    # raw `UnicodeDecodeError`, past every caller's `except RunnerError`,
    # leaving the job to the 35-minute recovery path with 0 recorded cost.
    try:
        return json.loads(raw_bytes.decode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RunnerError(f"implementation result file is not valid JSON: {exc}") from exc


def launch_container(*, image, clone_dir, goal, cli_env, name):
    """Starts the per-job container **detached** (the caller polls/tears
    it down itself, see `container_is_running`/`stop_container`/
    `kill_container`/`remove_container` below):

    - `name` is a deterministic identifier the caller has already
      persisted to `HarnessJob.container_id` BEFORE this call (Codex
      code-review P1, branch 2 round 3) -- docker addresses a container
      by name interchangeably with its actual id, so a crash between
      `docker run` succeeding and any return value being captured still
      leaves the recovery scan a working handle. This function's return
      value is not itself the identifier the rest of the system uses.
    - `clone_dir` is bind-mounted read-only at `/repo` -- no other host
      path is ever exposed. The container runs as the **same uid/gid**
      that cloned it (`os.getuid()`/`os.getgid()`, not the image's own
      baked-in non-root user) via `--user`, so the mount is readable
      without widening the clone's permissions at all (Codex code-review
      P2, branch 2 round 6: an earlier fix made every clone
      world-readable instead, which would have exposed a `sensitive`
      repo's content to any other local user on a shared runner, or for
      as long as cleanup takes if it's ever delayed -- aligning the
      runtime uid is the actual fix the permission mismatch called for,
      not a workaround around it).
    - `HOME`/XDG config live inside the container's own ephemeral
      filesystem (never bind-mounted from the host), so nothing persists
      once the container is removed and the invocation can't reach the
      runner's real `~/.claude`/`~/.codex` state.
    - `cli_env` is the CLI's own auth material only (resolved from env per
      O-2) -- never the worker's full inherited environment, never the
      git clone credential above. Passed via a transient `--env-file`
      (Codex round-2 P2: an earlier draft passed each pair as a bare
      `--env KEY=value` argv element, which puts the secret value
      directly in the `docker run` command line -- visible to any other
      process on the runner host via `ps`/process listings, and to
      whatever logs the invoked argv for debugging. A `--env-file` path
      is the only thing that appears in argv; the file itself is
      mode-0600, written fresh per launch, and deleted immediately after
      the (detached) `docker run` call returns -- by then the daemon has
      already read it into the container's own environment).
    - joins `HARNESS_DOCKER_NETWORK` (the restricted-egress network) and
      gets the configured CPU/memory/pids-limit caps -- never run with
      default (unrestricted) networking or resources.
    - no permission-bypass/"skip confirmation" flag is ever part of this
      argv.

    Returns the launched container's id.
    """
    argv = [
        "docker",
        "run",
        "--detach",
        # Codex code-review P2, branch 2 round 4: a missing/typoed image
        # tag would otherwise have `docker run` silently pull it from a
        # registry before launching -- bypassing the reviewed/local-image
        # pinning policy entirely, and doing so on the host, outside the
        # restricted-egress network this same launch is about to join.
        # Only a prebuilt, already-reviewed local image may ever run here.
        "--pull",
        "never",
        # No capabilities and no privilege gain through setuid binaries
        # the base image ships (Phase 3 branch 5, 2026-09-27 audit): a
        # root-in-container escalation could otherwise leave root-owned
        # files in a bind mount or processes quiescence can't kill.
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--name",
        name,
        "--network",
        settings.HARNESS_DOCKER_NETWORK,
        "--memory",
        settings.HARNESS_CONTAINER_MEMORY,
        "--cpus",
        settings.HARNESS_CONTAINER_CPUS,
        "--pids-limit",
        str(settings.HARNESS_CONTAINER_PIDS_LIMIT),
        "--mount",
        f"type=bind,source={clone_dir},target=/repo,readonly",
        "--workdir",
        "/repo",
    ]
    env_file_path = None
    if cli_env:
        fd, env_file_path = tempfile.mkstemp(prefix="harness-envfile-")
        os.chmod(env_file_path, 0o600)
        with os.fdopen(fd, "w") as env_file:
            for key, value in cli_env.items():
                env_file.write(f"{key}={value}\n")
        argv.extend(["--env-file", env_file_path])
    argv.extend([image, "--goal", goal])
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=_CONTAINER_LAUNCH_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"container launch failed: {exc}") from exc
    finally:
        if env_file_path:
            os.unlink(env_file_path)
    return result.stdout.strip()


def launch_implementation_container(
    *, image, workspace_dir, goal, test_command, base_sha, cli_env, name
):
    """The read-write counterpart to `launch_container` above -- the ONLY
    harness container launch that gets a writable mount, and only for
    `HarnessImplementationJob`. Mirrors every other policy
    `launch_container` already enforces (argv-only, `--pull never`,
    `--user {host_uid}:{host_gid}`, the restricted-egress network,
    resource caps, `--env-file` for secrets) except the mount itself:
    `workspace_dir` (from `clone_repo_writable`) is bind-mounted
    **read-write** at `/workspace` -- the repo lives at `/workspace/repo`,
    the wrapper's own result file lands at `/workspace/result.json`, a
    sibling the agent's own commit can never shadow since it's outside
    `/workspace/repo` entirely. `test_command` is the curator-configured
    `HarnessRepo.test_command`, an opaque shell string the wrapper runs
    as its own subprocess -- never interpreted, validated, or executed
    by anything on the host side; this function only threads it through
    to the container's own entrypoint as a single argv element.

    `base_sha` (Codex code-review P2, branch 1 rounds 2 and 4) is the
    caller-resolved base commit SHA the wrapper diffs the produced
    commit against. Round 2 originally passed a *ref* (`origin/
    <default_branch>`) here since the branch `clone_repo_writable` checks
    out has no upstream configured (so the wrapper can't infer a diff
    base on its own -- `@{upstream}` fails); round 4 found a ref is still
    the wrong choice even passed explicitly, since it lives inside
    `/workspace/repo/.git`, which the agent has full write access to --
    rewriting `refs/remotes/origin/<default_branch>` (or `packed-refs`)
    could make the diff wrongly report zero changed files. A resolved
    SHA is immutable in a way a ref name never is.

    Returns the launched container's id."""
    argv = [
        "docker",
        "run",
        "--detach",
        "--pull",
        "never",
        # No capabilities and no privilege gain through setuid binaries
        # the base image ships (Phase 3 branch 5, 2026-09-27 audit): a
        # root-in-container escalation could otherwise leave root-owned
        # files in a bind mount or processes quiescence can't kill.
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--name",
        name,
        "--network",
        settings.HARNESS_DOCKER_NETWORK,
        "--memory",
        settings.HARNESS_CONTAINER_MEMORY,
        "--cpus",
        settings.HARNESS_CONTAINER_CPUS,
        "--pids-limit",
        str(settings.HARNESS_CONTAINER_PIDS_LIMIT),
        "--mount",
        f"type=bind,source={workspace_dir},target=/workspace",
        "--workdir",
        "/workspace/repo",
    ]
    env_file_path = None
    if cli_env:
        fd, env_file_path = tempfile.mkstemp(prefix="harness-envfile-")
        os.chmod(env_file_path, 0o600)
        with os.fdopen(fd, "w") as env_file:
            for key, value in cli_env.items():
                env_file.write(f"{key}={value}\n")
        argv.extend(["--env-file", env_file_path])
    argv.extend([image, "--goal", goal, "--test-command", test_command, "--base-commit", base_sha])
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=_CONTAINER_LAUNCH_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"implementation container launch failed: {exc}") from exc
    finally:
        if env_file_path:
            os.unlink(env_file_path)
    return result.stdout.strip()


def capture_logs(container_id):
    """Whatever the container has emitted so far -- always safe to call,
    running or already exited."""
    # `errors="replace"`: container stdout is agent-writable, and invalid
    # UTF-8 must never surface as a `UnicodeDecodeError` past callers'
    # `except RunnerError` (Phase 3 branch 5, 2026-09-27 audit). Timeouts
    # and a missing docker binary are converted for the same reason.
    try:
        result = subprocess.run(
            ["docker", "logs", container_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(f"docker logs {container_id} failed: {exc}") from exc
    return result.stdout


def container_is_running(container_id):
    """Raises `RunnerError` when the daemon can't be asked at all (timeout,
    no docker binary) -- never guesses "stopped", which would let a wait
    loop treat a still-running container as finished."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RunnerError(f"docker inspect {container_id} failed: {exc}") from exc
    return result.returncode == 0 and result.stdout.strip() == "true"


def container_exit_code(container_id):
    """The container's own exit code once it has stopped running --
    distinguishes a CLI that finished cleanly from one that crashed/errored,
    which `container_is_running` alone can't (both are simply "not
    running"). Returns `None` if the container's exit state can't be
    determined at all (already removed, or the inspect call itself fails)."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id],
            capture_output=True,
            text=True,
            timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def stop_container(container_id, *, grace_seconds):
    """SIGTERM, waiting up to `grace_seconds` for a clean exit. A "no such
    container" result is treated as already-gone, not an error -- mirrors
    every other exit-path cleanup in this module."""
    subprocess.run(
        ["docker", "stop", "--time", str(grace_seconds), container_id],
        capture_output=True,
        timeout=grace_seconds + _DOCKER_CONTROL_TIMEOUT_SECONDS,
    )


def kill_container(container_id):
    """SIGKILL -- only called after `stop_container`'s grace period has
    already elapsed and the container is still running."""
    subprocess.run(
        ["docker", "kill", container_id],
        capture_output=True,
        timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )


def remove_container(container_id):
    """Called on every exit path (success, failure, timeout, cancellation).
    A "no such container" result is treated as already-gone, not an error
    -- but any OTHER non-zero exit raises `RunnerError` (Codex code-review
    P2, branch 2 round 6: an earlier draft never inspected the return
    code at all, so a genuine removal failure looked identical to success
    -- callers cleared `HarnessJob.container_id` regardless, orphaning a
    still-running container with no handle left to find it by)."""
    result = subprocess.run(
        ["docker", "rm", "-f", container_id],
        capture_output=True,
        text=True,
        timeout=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 and "No such container" not in (result.stderr or ""):
        raise RunnerError(f"docker rm -f {container_id} failed: {result.stderr.strip()}")
