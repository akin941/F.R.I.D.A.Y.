"""The node-local policy (ADR-013 decision 5; Phase 10 brief D4, D5 check
22).

Which repos and commands exist, which recipes are enabled, which bundle ids
may be opened, and the sandbox's network domains per command: all of it is
node-local. On the Mac it is edited only in the policy UI behind
LocalAuthentication (Mac session M2); here it is a frozen object loaded from
a JSON document when the node starts. **Nothing in the protocol reaches
it**: `LocalPolicy` has no setter, the node has no handler that changes it,
and a conformance test asserts its fingerprint is unchanged after every
hostile case.

The server sends typed intents (`command_kind=test`, `recipe=...`,
`bundle_id=...`); `LocalPolicy.resolve` maps one onto the owner's own
mapping or refuses `not_configured`:

- `repo_path` (`~/...` or absolute) is expanded against the node's home and
  `realpath`-resolved; it must **equal** a registered repo root's realpath,
  so a symlink planted in a mapped directory cannot redirect it elsewhere,
  and `..` never reaches here (the schema refuses it);
- `command_kind` must be mapped for that repo;
- a recipe must be enabled locally (and a repo recipe's `repo_path` must be a
  registered repo);
- `bundle_id` must be on the local list.

A mapping may not list an environment variable whose name looks like a
secret (D4): the document is refused at load.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from nodes.protocol.canonical import sha256_hex

COMMAND_KINDS = ("test", "lint", "typecheck", "build", "format_check")
SANDBOX_MODES = ("in_place", "worktree")
SECRET_NAME = re.compile(r"(?i)token|secret|key|password|credential|auth")
DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


class NotConfigured(Exception):
    """D5 check 22: the intent has no local mapping. `reason` is for local
    logs and tests only; the node answers the bare code `not_configured`."""

    code = "not_configured"

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class PolicyError(ValueError):
    """A local policy document is malformed or unsafe (refused at load)."""


@dataclass(frozen=True)
class CommandMapping:
    """One `command_kind` of one repo: the exact argv (no shell), its fixed
    `PATH`, extra environment names taken from the node's environment, the
    network domains it may reach (none by default), its runtime cap and the
    sandbox mode."""

    kind: str
    argv: tuple
    path: str = DEFAULT_PATH
    env_names: tuple = ()
    network_domains: tuple = ()
    max_runtime_seconds: int = 900
    mode: str = "in_place"

    def document(self) -> dict:
        return {
            "kind": self.kind,
            "argv": list(self.argv),
            "path": self.path,
            "env_names": list(self.env_names),
            "network_domains": list(self.network_domains),
            "max_runtime_seconds": self.max_runtime_seconds,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class RepoMapping:
    name: str
    root: str  # as written (`~/...` or absolute)
    real_root: str  # realpath at load
    commands: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class Resolved:
    """What check 22 resolved an intent to (never sent to the server)."""

    capability: str
    repo: RepoMapping | None = None
    command: CommandMapping | None = None
    recipe: str | None = None
    bundle_id: str | None = None


def _check_mapping(repo_name, body) -> CommandMapping:
    kind = body.get("kind")
    if kind not in COMMAND_KINDS:
        raise PolicyError(f"{repo_name}: unknown command kind")
    argv = body.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(part, str) and part and "\x00" not in part for part in argv)
    ):
        raise PolicyError(f"{repo_name}/{kind}: argv must be a non-empty list of strings")
    env_names = tuple(body.get("env_names", ()))
    for name in env_names:
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise PolicyError(f"{repo_name}/{kind}: bad environment name")
        if SECRET_NAME.search(name):
            raise PolicyError(f"{repo_name}/{kind}: a secret-shaped environment name is refused")
    domains = tuple(body.get("network_domains", ()))
    if not all(isinstance(d, str) and _DOMAIN.fullmatch(d) for d in domains):
        raise PolicyError(f"{repo_name}/{kind}: bad network domain")
    runtime = body.get("max_runtime_seconds", 900)
    if isinstance(runtime, bool) or not isinstance(runtime, int) or not 1 <= runtime <= 900:
        raise PolicyError(f"{repo_name}/{kind}: max_runtime_seconds outside 1..900")
    mode = body.get("mode", "in_place")
    if mode not in SANDBOX_MODES:
        raise PolicyError(f"{repo_name}/{kind}: unknown sandbox mode")
    path = body.get("path", DEFAULT_PATH)
    if not isinstance(path, str) or not path or "\x00" in path:
        raise PolicyError(f"{repo_name}/{kind}: bad PATH")
    extra = set(body) - {
        "kind",
        "argv",
        "path",
        "env_names",
        "network_domains",
        "max_runtime_seconds",
        "mode",
    }
    if extra:
        raise PolicyError(f"{repo_name}/{kind}: unexpected keys")
    return CommandMapping(
        kind=kind,
        argv=tuple(argv),
        path=path,
        env_names=env_names,
        network_domains=domains,
        max_runtime_seconds=runtime,
        mode=mode,
    )


class LocalPolicy:
    """The frozen node-local policy. Build it with `LocalPolicy.from_document`;
    it exposes lookups only."""

    __slots__ = ("_home", "_repos", "_recipes", "_bundle_ids", "_document")

    def __init__(self, *, home, repos, recipes_enabled, bundle_ids, document):
        object.__setattr__(self, "_home", str(home))
        object.__setattr__(self, "_repos", tuple(repos))
        object.__setattr__(self, "_recipes", frozenset(recipes_enabled))
        object.__setattr__(self, "_bundle_ids", frozenset(bundle_ids))
        object.__setattr__(self, "_document", json.loads(json.dumps(document)))

    def __setattr__(self, name, value):
        raise AttributeError("the local policy is read-only (ADR-013 decision 5)")

    @classmethod
    def from_document(cls, document, *, home):
        """Load a policy document (`fixtures/local_policy.json` shape) for a
        node whose home directory is `home`."""
        if not isinstance(document, dict) or set(document) - {
            "repos",
            "recipes_enabled",
            "bundle_ids",
        }:
            raise PolicyError("unexpected policy keys")
        repos = []
        seen = set()
        for body in document.get("repos", ()):
            name = body.get("name")
            if not isinstance(name, str) or not name or len(name) > 64:
                raise PolicyError("a repo needs a short local name")
            if set(body) - {"name", "root", "commands"}:
                raise PolicyError(f"{name}: unexpected keys")
            root = body.get("root")
            if not isinstance(root, str) or not root.startswith(("/", "~/")):
                raise PolicyError(f"{name}: root must be absolute or ~/")
            real_root = os.path.realpath(_expand(root, home))
            if real_root in seen:
                raise PolicyError(f"{name}: root mapped twice")
            seen.add(real_root)
            commands = {}
            for mapping in body.get("commands", ()):
                command = _check_mapping(name, mapping)
                if command.kind in commands:
                    raise PolicyError(f"{name}: command kind mapped twice")
                commands[command.kind] = command
            repos.append(RepoMapping(name, root, real_root, MappingProxyType(commands)))
        return cls(
            home=home,
            repos=repos,
            recipes_enabled=document.get("recipes_enabled", ()),
            bundle_ids=document.get("bundle_ids", ()),
            document=document,
        )

    @classmethod
    def load(cls, path, *, home):
        return cls.from_document(json.loads(Path(path).read_text(encoding="utf-8")), home=home)

    @property
    def home(self) -> str:
        return self._home

    @property
    def repos(self) -> tuple:
        return self._repos

    def fingerprint(self) -> str:
        """A hash of the whole policy (document and home); the conformance
        suite asserts it never changes during a session."""
        return sha256_hex({"home": self._home, "document": self._document})

    def repo_for_path(self, repo_path) -> RepoMapping:
        if not isinstance(repo_path, str):
            raise NotConfigured("repo_path is not a string")
        real = os.path.realpath(_expand(repo_path, self._home))
        for repo in self._repos:
            if repo.real_root == real:
                return repo
        raise NotConfigured("repo not mapped")

    def resolve(self, capability, args) -> Resolved:
        """D5 check 22 for already schema-valid `args`."""
        if capability == "repo.command.run":
            repo = self.repo_for_path(args["repo_path"])
            command = repo.commands.get(args["command_kind"])
            if command is None:
                raise NotConfigured("command kind not mapped")
            return Resolved(capability, repo=repo, command=command)
        if capability == "applescript.recipe.run":
            recipe = args["recipe"]
            if recipe not in self._recipes:
                raise NotConfigured("recipe not enabled")
            repo = None
            params = args.get("params") or {}
            if "repo_path" in params:
                repo = self.repo_for_path(params["repo_path"])
            return Resolved(capability, repo=repo, recipe=recipe)
        if capability in ("apps.open", "apps.focus", "ax.press", "ax.type_text"):
            if args["bundle_id"] not in self._bundle_ids:
                raise NotConfigured("bundle id not on the local list")
            return Resolved(capability, bundle_id=args["bundle_id"])
        raise NotConfigured("no local mapping for this capability")


def _expand(path, home):
    if path == "~":
        return str(home)
    if path.startswith("~/"):
        return os.path.join(str(home), path[2:])
    return path
