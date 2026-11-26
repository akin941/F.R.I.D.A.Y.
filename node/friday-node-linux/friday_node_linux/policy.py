"""The Linux node's local policy (Phase 13 brief D10; ADR-013 decisions 5
and 11).

`~/.config/friday-node/policy.json`, mode 0600, owned by the node's user:

    {"v": 1,
     "grants": {"claude": false, "codex": false},
     "repos": [{"name": "acme", "root": "~/work/acme"}]}

- `grants` are the per-directory read grants (off by default): `claude`
  lets the readers open `~/.claude` (Claude Code sessions, usage, owner
  turns), `codex` opens `~/.codex` (Codex threads, owner turns);
- `repos` are the registered repositories `git.status` may report on (the
  vendored reference `LocalPolicy` resolves them: a path must equal a
  registered root's realpath).

**Local only.** The daemon only reads this file (at start and on `SIGHUP`,
between sessions); no protocol frame reaches it (the conformance suite's
policy-shaped frames prove the hash and mtime are unchanged). It is changed
only by `friday-node-linux grant|revoke|repo add|repo remove` at an
interactive TTY (`cli.py`), which shows the exact change and asks the local
user to type the directory's basename -- the Linux stand-in for Touch ID.
A file with a mode wider than 0600, another owner, a symlink, unknown keys
or a malformed body is refused at load (the daemon does not start). A
missing file is the default: nothing granted, no repos.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from friday_node_linux import paths
from friday_node_linux._vendor.nodes.protocol.canonical import sha256_hex
from friday_node_linux._vendor.nodes.reference.local_policy import LocalPolicy

POLICY_VERSION = 1
GRANT_DIRS = MappingProxyType({"claude": ".claude", "codex": ".codex"})
GRANT_LABELS = MappingProxyType(
    {
        "claude": "Claude Code sessions, usage and your own typed turns",
        "codex": "Codex threads and your own typed turns",
    }
)
MAX_REPOS = 64
_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class PolicyError(ValueError):
    """The policy document is malformed, or a requested change is invalid."""


def _frozen_grants(grants):
    return MappingProxyType({family: bool(grants.get(family, False)) for family in GRANT_DIRS})


@dataclass(frozen=True)
class Repo:
    name: str
    root: str  # "~/..." under the home, else absolute


@dataclass(frozen=True)
class LinuxPolicy:
    grants: MappingProxyType = field(default_factory=lambda: _frozen_grants({}))
    repos: tuple = ()

    # --- the document --------------------------------------------------------

    def document(self) -> dict:
        return {
            "v": POLICY_VERSION,
            "grants": dict(self.grants),
            "repos": [{"name": repo.name, "root": repo.root} for repo in self.repos],
        }

    def fingerprint(self) -> str:
        return sha256_hex(self.document())

    @classmethod
    def from_document(cls, document) -> "LinuxPolicy":
        if not isinstance(document, dict) or set(document) != {"v", "grants", "repos"}:
            raise PolicyError("policy.json needs exactly v, grants and repos")
        if document["v"] != POLICY_VERSION:
            raise PolicyError("unsupported policy version")
        grants = document["grants"]
        if not isinstance(grants, dict) or set(grants) - set(GRANT_DIRS):
            raise PolicyError("unknown grant")
        if any(not isinstance(value, bool) for value in grants.values()):
            raise PolicyError("a grant is true or false")
        raw = document["repos"]
        if not isinstance(raw, list) or len(raw) > MAX_REPOS:
            raise PolicyError("repos must be a list")
        repos, names = [], set()
        for body in raw:
            if not isinstance(body, dict) or set(body) != {"name", "root"}:
                raise PolicyError("a repo needs exactly name and root")
            name, root = body["name"], body["root"]
            if not isinstance(name, str) or not _REPO_NAME.match(name) or name in names:
                raise PolicyError("a repo name is 1-64 letters, digits, . _ - and unique")
            if not isinstance(root, str) or not root.startswith(("/", "~/")) or "\x00" in root:
                raise PolicyError(f"{name}: root must be absolute or ~/")
            names.add(name)
            repos.append(Repo(name, root))
        return cls(grants=_frozen_grants(grants), repos=tuple(repos))

    # --- load and save ---------------------------------------------------------

    @classmethod
    def load(cls, path) -> "LinuxPolicy":
        path = Path(path)
        if not os.path.lexists(path):
            return cls()
        try:
            data = paths.read_private(path)
        except paths.UnsafePath as exc:
            raise PolicyError(str(exc)) from exc
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PolicyError("policy.json is not valid JSON") from exc
        return cls.from_document(document)

    def save(self, path) -> None:
        body = json.dumps(self.document(), indent=2, sort_keys=True) + "\n"
        paths.write_private_atomic(path, body.encode("utf-8"))

    # --- what the readers use ----------------------------------------------------

    def local_policy(self, home) -> LocalPolicy:
        """The vendored reference `LocalPolicy` (repos only: no commands,
        recipes or bundle ids -- the Linux node runs no action)."""
        return LocalPolicy.from_document(
            {"repos": [{"name": repo.name, "root": repo.root} for repo in self.repos]},
            home=str(home),
        )

    # --- changes (the CLI's, at a TTY) ---------------------------------------------

    def with_grant(self, family, granted: bool) -> "LinuxPolicy":
        if family not in GRANT_DIRS:
            raise PolicyError(f"unknown directory grant {family!r} (claude or codex)")
        return LinuxPolicy(
            grants=_frozen_grants({**self.grants, family: granted}), repos=self.repos
        )

    def with_repo(self, name, root) -> "LinuxPolicy":
        if not isinstance(name, str) or not _REPO_NAME.match(name):
            raise PolicyError("a repo name is 1-64 letters, digits, . _ -")
        if any(repo.name == name for repo in self.repos):
            raise PolicyError(f"a repo named {name!r} is already registered")
        if any(repo.root == root for repo in self.repos):
            raise PolicyError(f"{root} is already registered")
        if len(self.repos) >= MAX_REPOS:
            raise PolicyError("too many repos")
        return LinuxPolicy(grants=self.grants, repos=(*self.repos, Repo(name, root)))

    def without_repo(self, name) -> "LinuxPolicy":
        if not any(repo.name == name for repo in self.repos):
            raise PolicyError(f"no repo named {name!r}")
        return LinuxPolicy(
            grants=self.grants, repos=tuple(repo for repo in self.repos if repo.name != name)
        )


def display_root(path, home) -> str:
    """`path` (absolute, resolved) in `~/` form under `home`."""
    home = os.path.realpath(str(home))
    path = os.path.realpath(str(path))
    if path == home:
        return "~"
    if path.startswith(home.rstrip("/") + "/"):
        return "~/" + path[len(home.rstrip("/")) + 1 :]
    return path
