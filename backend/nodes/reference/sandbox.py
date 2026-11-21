"""The `srt` sandbox policy generator (Phase 10 brief D4).

`build_sandbox(command, ...)` turns one node-local command mapping
(`local_policy.CommandMapping`) into:

- the `srt` (Anthropic sandbox-runtime) **settings document**: network
  (`allowedDomains` empty unless the mapping names domains, no Unix
  sockets, no local binding) and filesystem (`denyRead` of every credential
  and browser-profile directory and the node's own data, `allowWrite` of the
  write root and a per-run private temp dir only, `denyWrite` of the write
  root's `.git`, so a command cannot plant a hook that runs later outside
  the sandbox; `/tmp` itself is never writable);
- the exact **process spec**: an argv list (no shell), the working
  directory (the write root: the repo root in `in_place` mode, the
  throwaway worktree in `worktree` mode) and an environment stripped to an
  allowlist (`PATH` fixed per mapping, `HOME`, `LANG`, `LC_ALL`,
  `TERM=dumb`, `CI=1`, `NO_COLOR=1`, `TMPDIR` = the private temp dir, plus
  names the mapping lists, taken from the node's environment) that never
  carries a name matching `(?i)token|secret|key|password|credential|auth`;
- the **limits**: wall time = min(mapping, contract) <= 900 s, the same CPU
  time, output <= 1 MiB (then truncated), at most 256 processes, and a 5 s
  SIGTERM -> SIGKILL grace.

The generator is the spec: `settings_bytes()` is the canonical JSON (JCS
subset, `nodes.protocol.canonical`) of the settings document, and
`nodes/conformance/vectors/sandbox.json` pins it for a fixed set of
mappings; the Swift `SandboxPolicy` (Mac session M2) must produce
byte-identical documents. Both modes are gate-class in Phase 10 (ADR-014).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from nodes.capabilities import MAX_RUNTIME_SECONDS
from nodes.protocol.canonical import canonicalize, sha256_hex
from nodes.reference.local_policy import SANDBOX_MODES, SECRET_NAME, CommandMapping
from nodes.reference.stream_scrub import MAX_OUTPUT_BYTES

# Home-relative directories no sandboxed command may read (D4). The macOS
# paths are the spec; the Linux equivalents keep the opt-in `-m srt` run on
# Linux (bubblewrap) meaningful.
DENY_READ_HOME = (
    ".ssh",
    ".aws",
    ".config/gh",
    ".claude",
    ".codex",
    ".gnupg",
    ".netrc",
    ".docker",
    "Library/Keychains",
    "Library/Safari",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Application Support/Arc",
    "Library/Cookies",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
)
# The node's own container and data (its credential, policy, spool, logs).
DEFAULT_NODE_DIRS = (
    "Library/Containers/ai.friday.node",
    "Library/Application Support/Friday Node",
    ".friday-node",
)
FIXED_ENV = (
    ("LANG", "en_US.UTF-8"),
    ("LC_ALL", "en_US.UTF-8"),
    ("TERM", "dumb"),
    ("CI", "1"),
    ("NO_COLOR", "1"),
)
MAX_PROCESSES = 256
KILL_GRACE_SECONDS = 5


class SandboxError(ValueError):
    """The inputs cannot produce a safe sandbox (refused before anything
    runs)."""


@dataclass(frozen=True)
class SandboxSpec:
    mode: str
    settings: dict = field(repr=False)
    argv: tuple = ()
    cwd: str = ""
    env: dict = field(default_factory=dict, repr=False)
    limits: dict = field(default_factory=dict)
    write_root: str = ""
    temp_dir: str = ""

    @property
    def network_enabled(self) -> bool:
        return bool(self.settings["network"]["allowedDomains"])

    def settings_bytes(self) -> bytes:
        """The settings document the executor writes for `srt`: canonical
        JSON, byte-identical across implementations."""
        return canonicalize(self.settings)

    def document(self) -> dict:
        return {
            "mode": self.mode,
            "settings": self.settings,
            "process": {"argv": list(self.argv), "cwd": self.cwd, "env": dict(self.env)},
            "limits": dict(self.limits),
            "network_enabled": self.network_enabled,
            "settings_sha256": sha256_hex(self.settings),
        }


def _abs(path, label):
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        raise SandboxError(f"{label} must be an absolute path")
    return os.path.normpath(path)


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def build_sandbox(
    command: CommandMapping,
    *,
    home,
    repo_root,
    write_root,
    temp_dir,
    contract_max_runtime=MAX_RUNTIME_SECONDS,
    node_env: Mapping | None = None,
    node_dirs=DEFAULT_NODE_DIRS,
) -> SandboxSpec:
    """The sandbox for one run of `command`. `write_root` is `repo_root` in
    `in_place` mode and the throwaway worktree in `worktree` mode;
    `temp_dir` is a fresh private directory for this run."""
    if command.mode not in SANDBOX_MODES:
        raise SandboxError("unknown sandbox mode")
    home = _abs(home, "home")
    repo_root = _abs(repo_root, "repo_root")
    write_root = _abs(write_root, "write_root")
    temp_dir = _abs(temp_dir, "temp_dir")
    if command.mode == "in_place" and write_root != repo_root:
        raise SandboxError("in_place mode writes to the repo root only")
    if command.mode == "worktree" and _under(write_root, repo_root):
        raise SandboxError("the worktree must live outside the source repo")
    if write_root in ("/", home) or temp_dir in ("/", home, "/tmp", "/private/tmp"):
        raise SandboxError("the write root and temp dir must be dedicated directories")
    deny_read = [os.path.join(home, rel) for rel in DENY_READ_HOME]
    deny_read += [os.path.join(home, rel) for rel in node_dirs]
    for path in (write_root, temp_dir):
        if any(_under(path, denied) or _under(denied, path) for denied in deny_read):
            raise SandboxError("a writable directory overlaps a denied directory")
    wall = min(command.max_runtime_seconds, contract_max_runtime, MAX_RUNTIME_SECONDS)
    env = {"PATH": command.path, "HOME": home, **dict(FIXED_ENV), "TMPDIR": temp_dir}
    for name in command.env_names:
        if SECRET_NAME.search(name) or name in env:
            continue
        value = (node_env or {}).get(name)
        if isinstance(value, str) and "\x00" not in value:
            env[name] = value
    if any(SECRET_NAME.search(name) for name in env):  # defence in depth
        raise SandboxError("a secret-shaped environment name reached the sandbox")
    settings = {
        "network": {
            "allowedDomains": sorted(set(command.network_domains)),
            "deniedDomains": [],
            "allowUnixSockets": [],
            "allowLocalBinding": False,
        },
        "filesystem": {
            "denyRead": deny_read,
            "allowWrite": [write_root, temp_dir],
            "denyWrite": [os.path.join(write_root, ".git")],
        },
    }
    limits = {
        "wall_seconds": wall,
        "cpu_seconds": wall,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_processes": MAX_PROCESSES,
        "kill_grace_seconds": KILL_GRACE_SECONDS,
    }
    return SandboxSpec(
        mode=command.mode,
        settings=settings,
        argv=tuple(command.argv),
        cwd=write_root,
        env=env,
        limits=limits,
        write_root=write_root,
        temp_dir=temp_dir,
    )
