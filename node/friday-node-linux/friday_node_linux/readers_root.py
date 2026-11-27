"""The Linux node's readers over the **real** home directory (Phase 13
brief D10).

`LinuxReaders` wraps the vendored reference `Readers` (the spec of every v1
reader: bounded, scrubbed, `cwd_prefixes` first, never tool results or file
contents, no symlink out of the granted root) and adds what is
platform-specific:

- the directory grants come from the local policy (`policy.json`, off by
  default); a family without its grant is advertised `not_granted` and the
  reader refuses too;
- `usage.ccusage` is advertised `granted` only when `ccusage` is on `PATH`
  and the `claude` grant is on, else `unsupported`; the amounts are computed
  by the reference reader from the granted transcripts (the spec the server
  recomputes; ccusage itself is never executed);
- `git.status` is `not_granted` until a repo is registered locally;
- `apps.frontmost` and `windows.list` are advertised `unsupported`: there
  is no portable, consent-respecting Wayland equivalent (X11 is not worth a
  special case). Only `friday-node-linux conformance` hands in the fixture
  desktop, standing in for the platform as the Swift Conformance build does;
- the 8B owner-turn reads (`claude.transcripts.owner_turns`,
  `codex.transcripts.owner_turns`, server contracts since runbook A12) run
  the vendored reference readers' dispatch (`nodes.reference.owner_turns`
  over the readers' own vetted files: regular files only, never through a
  symlink, inside the real `~/.claude/projects` / `~/.codex/sessions`, at
  most `MAX_FILES`, re-read per call; home-aware `cwd_prefixes`), behind the
  same directory grants. `vetted_files` states the same file rule for the
  tests.

No v2 (action) capability exists on this node.
"""

import os
import shutil
from pathlib import Path
from types import MappingProxyType

from friday_node_linux._vendor.nodes import capabilities
from friday_node_linux._vendor.nodes.reference import owner_turns
from friday_node_linux._vendor.nodes.reference.readers import MAX_FILES, Readers, ReadOutcome

DESKTOP_CAPABILITIES = ("apps.frontmost", "windows.list")
OWNER_TURN_FAMILY = MappingProxyType({owner_turns.CLAUDE: "claude", owner_turns.CODEX: "codex"})
# Where each family lives under the home, and under the owner-turn view.
TRANSCRIPT_DIRS = MappingProxyType(
    {
        "claude": (Path(".claude") / "projects", Path("claude") / "projects", "*/*.jsonl"),
        "codex": (Path(".codex") / "sessions", Path("codex") / "sessions", "**/*.jsonl"),
    }
)


def owner_turn_contracts():
    """`{name: CapabilityContract}` for the two 8B owner-turn reads (server
    contracts in `nodes/capabilities/v1` since runbook A12, vendored with the
    rest)."""
    contracts = capabilities.load_contracts()
    return {name: contracts[name] for name in owner_turns.CAPABILITIES}


def vetted_files(home, family) -> list:
    """`[(relative path under the family root, real path)]`: regular files
    only, never through a symlink, inside the real root, at most MAX_FILES."""
    source, _target, pattern = TRANSCRIPT_DIRS[family]
    root = Path(home) / source
    if not root.is_dir() or root.is_symlink():
        return []  # the reference rule: the family root itself is never a symlink
    real_root = os.path.realpath(root)
    out = []
    for path in sorted(root.glob(pattern)):
        if path.is_symlink() or not path.is_file():
            continue
        if any(parent.is_symlink() for parent in path.relative_to(root).parents if parent.parts):
            continue
        real = os.path.realpath(path)
        if not real.startswith(real_root + os.sep):
            continue
        out.append((path.relative_to(root), real))
        if len(out) >= MAX_FILES:
            break
    return out


class LinuxReaders:
    """The v1 readers plus the owner-turn reads, over one real home."""

    def __init__(
        self,
        *,
        home,
        policy,
        desktop=None,
        window_titles=False,
        which=shutil.which,
        scratch_dir=None,
        availability_overrides=None,
    ):
        self.home = os.path.realpath(str(home))
        self.policy = policy
        self.grants = dict(policy.grants)
        self.desktop_supported = desktop is not None
        self.ccusage_on_path = which("ccusage") is not None
        self.scratch_dir = scratch_dir  # unused since runbook A12 (no owner-turn view)
        self.availability_overrides = dict(availability_overrides or {})
        self.inner = Readers(
            home=self.home,
            policy=policy.local_policy(self.home),
            grants=self.grants,
            window_titles=window_titles,
            desktop=desktop if desktop is not None else {},
        )
        self.calls = self.inner.calls  # one list: every reader run, counted once
        self.owner_turn_contracts = owner_turn_contracts()

    # --- availability ----------------------------------------------------------

    def availability(self) -> dict:
        out = dict(self.inner.availability())
        if not self.desktop_supported:
            for name in DESKTOP_CAPABILITIES:
                out[name] = "unsupported"
        if not self.policy.repos and out.get("git.status") == "granted":
            out["git.status"] = "not_granted"  # no repo registered locally
        if not self.ccusage_on_path and out.get("usage.ccusage") == "granted":
            out["usage.ccusage"] = "unsupported"
        for name, family in OWNER_TURN_FAMILY.items():
            out[name] = "granted" if self.grants.get(family) else "not_granted"
        out.update(self.availability_overrides)
        return out

    def catalogue(self) -> list:
        """The `hello` catalogue: the v1 read contracts and the two owner-turn
        reads, each with its availability. Never a v2 (action) contract."""
        availability = self.availability()
        contracts = {**capabilities.load_contracts(), **self.owner_turn_contracts}
        return [
            contracts[name].catalogue_entry(availability.get(name, "unsupported"))
            for name in sorted(contracts)
        ]

    # --- reads ---------------------------------------------------------------------

    def read(self, capability, args, *, now) -> ReadOutcome:
        if self.availability().get(capability) != "granted":
            self.calls.append(capability)
            return ReadOutcome("error")
        return self.inner.read(capability, args, now=now)
