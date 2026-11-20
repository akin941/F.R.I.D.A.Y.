"""Connector manifests: code, not data (ADR-010 decision 3, brief D3).

A connector exists only as a `ConnectorManifest` committed under
`connectors/manifests/<name>.py`, with a governance twin `docs/mcp/<name>.md`
and a row in `docs/mcp/allowlist.md` (a test fails if any of the three is
missing). Widening a connector -- another tool, another channel, a changed
schema pin -- is a reviewed commit, never a database write (ADR-004: no
self-granted permissions). The database only holds per-workspace
`ConnectorBinding`s (enabled or not) and their last health/pin report.

A manifest lists every tool Jarvis may ever call on that server. Anything
the server offers beyond the list is unreachable: it is never registered,
never offered to the router, never callable. Each listed tool carries:

- a Jarvis-authored name and description -- the only text about the tool
  that ever reaches an LLM prompt (server descriptions never do);
- a strict DRF argument serializer (no `jsonschema`, brief D2) -- the router's
  extracted arguments and a write's payload are validated by it;
- an optional scope check `(workspace, args) -> None` raising `ScopeError`
  (e.g. GitHub: the repo must be one of the workspace's `HarnessRepo`s);
- the sha256 pin of the server's `inputSchema` (`schema_hash`), compared on
  enable, on the pin beat and before every call: a changed schema disables
  the tool until a commit updates the pin (fail closed, tool poisoning);
- for writes: the gate name, `user_invocable`, a renderer for the decision
  dialog and `records_work_log` (brief D6, D13).
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rest_framework import serializers

from tools.registry import DATA_CLASSES, READ_CHANNELS

KIND_MCP = "mcp"
KIND_NATIVE = "native"
STATUS_ACTIVE = "active"
# A blocked manifest (D14: the external-installation placeholder) documents a connector that
# can never bind: no binding row can be created for it and none of its tools
# is ever registered.
STATUS_BLOCKED = "blocked"
TOOL_READ = "read"
TOOL_WRITE = "write"
# Workspace types a manifest may bind to (`workspaces.Workspace.Type` values;
# literals here so this module stays import-light).
WORKSPACE_PERSONAL = "personal"
WORKSPACE_COMPANY = "company"
_WORKSPACE_TYPES = frozenset({WORKSPACE_PERSONAL, WORKSPACE_COMPANY})

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_GATE_RE = re.compile(r"^[a-z][a-z0-9_]{1,99}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# A sidecar image is pinned by digest, never by a movable tag alone (D1).
_DIGEST_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class ScopeError(Exception):
    """Raised by a `ToolPolicy.scope_check`: the arguments name something
    outside the calling workspace (e.g. another firm's repository). The
    call is refused before any network traffic."""


def schema_hash(input_schema):
    """The pin of a server tool's `inputSchema`: sha256 over its canonical
    JSON (sorted keys, no whitespace). Stable across key order, sensitive to
    every value -- a renamed argument, a new enum member, a changed type."""
    canonical = json.dumps(input_schema or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    server_tool: str
    kind: str
    description: str
    argument_serializer: Any
    schema_sha256: str
    scope_check: Callable[[Any, dict], None] | None = None
    user_invocable: bool = True
    renderer: Callable[[dict], list] | None = None
    records_work_log: bool = True
    gate_name: str = ""
    # Phase 5 branch 5 (GitHub): optional per-call hooks, all Jarvis code.
    # `classify(workspace, args) -> "firm" | "personal"`: a read whose
    # arguments name personal-class data (a `sensitive` repo) is treated as
    # `personal` for that call even on a `firm` connector (brief D7/D5).
    classify: Callable[[Any, dict], str] | None = None
    # A write's extra snapshot beyond the server arguments (e.g. the
    # expected head SHA of a PR), validated by this strict serializer at
    # request time and stored as `payload["context"]`; never sent.
    context_serializer: Any = None
    # `precondition(manifest, binding, payload) -> error_type | None`, run
    # by the write task after the claim and before the one send; a returned
    # code (a `ConnectorWriteExecution.ErrorType` value) ends the execution
    # `failed` with nothing sent (e.g. GitHub's `head_moved`).
    precondition: Callable[[Any, Any, dict], str | None] | None = None


@dataclass(frozen=True)
class ConnectorManifest:
    name: str
    version: int
    display_name: str
    kind: str
    status: str
    data_class: str
    workspace_types: frozenset
    read_channels: frozenset
    # Jarvis-side env var holding the server's internal URL (MCP kind). The
    # credential lives only in the sidecar's own environment (D1); a native
    # bridge names its credential env vars in `credential_envs` -- names only,
    # the values never leave the environment.
    url_env: str = ""
    credential_envs: tuple = ()
    sidecar_image: str = ""
    tools: tuple = ()

    @property
    def reads(self):
        return tuple(t for t in self.tools if t.kind == TOOL_READ)

    @property
    def writes(self):
        return tuple(t for t in self.tools if t.kind == TOOL_WRITE)

    @property
    def blocked(self):
        return self.status == STATUS_BLOCKED

    def get_tool(self, name):
        return next((t for t in self.tools if t.name == name), None)

    def gate_name(self, policy):
        """A write's approval-gate tool name: explicit, else the generic
        `mcp_<connector>_<tool>` (brief D6)."""
        return policy.gate_name or f"mcp_{self.name}_{policy.name}"


def validate_manifest(manifest):
    """Boot-time check of a manifest; raises `ValueError` on the first
    violation (a programming error in a reviewed commit, never a runtime
    refusal)."""
    m = manifest
    if not _NAME_RE.match(m.name):
        raise ValueError(f"manifest name {m.name!r} must match {_NAME_RE.pattern}.")
    if not isinstance(m.version, int) or isinstance(m.version, bool) or m.version < 1:
        raise ValueError(f"{m.name}: version must be a positive integer.")
    if not m.display_name.strip():
        raise ValueError(f"{m.name}: display_name is required.")
    if m.kind not in (KIND_MCP, KIND_NATIVE):
        raise ValueError(f"{m.name}: unknown kind {m.kind!r}.")
    if m.status not in (STATUS_ACTIVE, STATUS_BLOCKED):
        raise ValueError(f"{m.name}: unknown status {m.status!r}.")
    if m.data_class not in DATA_CLASSES:
        raise ValueError(f"{m.name}: unknown data class {m.data_class!r}.")
    types = frozenset(m.workspace_types)
    if not types or not types <= _WORKSPACE_TYPES:
        raise ValueError(
            f"{m.name}: workspace_types must be a non-empty subset of {_WORKSPACE_TYPES}."
        )
    # D3: `personal` manifests bind only to the personal workspace.
    if m.data_class == "personal" and types != {WORKSPACE_PERSONAL}:
        raise ValueError(f"{m.name}: a personal-class connector binds only to personal workspaces.")
    channels = frozenset(m.read_channels)
    widened = channels - READ_CHANNELS[m.data_class]
    if widened:
        raise ValueError(
            f"{m.name}: read channels {sorted(widened)} exceed the {m.data_class} set."
        )
    for env_name in (m.url_env, *m.credential_envs):
        if env_name and not _ENV_RE.match(env_name):
            raise ValueError(f"{m.name}: {env_name!r} is not an env var name.")
    if m.status == STATUS_ACTIVE and m.kind == KIND_MCP:
        if not m.url_env:
            raise ValueError(f"{m.name}: an MCP connector names its URL env var.")
        if m.sidecar_image and not _DIGEST_RE.match(m.sidecar_image):
            raise ValueError(f"{m.name}: the sidecar image must be pinned by digest.")
    if m.reads and not channels:
        raise ValueError(f"{m.name}: read tools need at least one read channel.")
    seen_names, seen_server = set(), set()
    for policy in m.tools:
        _validate_tool(m, policy)
        if policy.name in seen_names or policy.server_tool in seen_server:
            raise ValueError(f"{m.name}: tool {policy.name!r} is listed twice.")
        seen_names.add(policy.name)
        seen_server.add(policy.server_tool)


def _validate_tool(manifest, policy):
    where = f"{manifest.name}.{policy.name}"
    if not _NAME_RE.match(policy.name):
        raise ValueError(f"{where}: tool name must match {_NAME_RE.pattern}.")
    if not policy.server_tool or not policy.server_tool.strip():
        raise ValueError(f"{where}: server_tool is required.")
    if policy.kind not in (TOOL_READ, TOOL_WRITE):
        raise ValueError(f"{where}: unknown tool kind {policy.kind!r}.")
    if not policy.description.strip():
        raise ValueError(f"{where}: a Jarvis-authored description is required.")
    if not (
        isinstance(policy.argument_serializer, type)
        and issubclass(policy.argument_serializer, serializers.Serializer)
    ):
        raise ValueError(f"{where}: argument_serializer must be a DRF Serializer class.")
    if not _SHA256_RE.match(policy.schema_sha256 or ""):
        raise ValueError(f"{where}: schema_sha256 must be a 64-hex sha256 pin.")
    if policy.kind == TOOL_WRITE and not _GATE_RE.match(manifest.gate_name(policy)):
        raise ValueError(f"{where}: invalid gate name {manifest.gate_name(policy)!r}.")
    if policy.context_serializer is not None and not (
        isinstance(policy.context_serializer, type)
        and issubclass(policy.context_serializer, serializers.Serializer)
    ):
        raise ValueError(f"{where}: context_serializer must be a DRF Serializer class.")
    if policy.kind == TOOL_READ and (policy.context_serializer or policy.precondition):
        raise ValueError(f"{where}: context and preconditions apply to writes only.")


_MANIFESTS: dict[str, ConnectorManifest] = {}


def register_manifest(manifest):
    validate_manifest(manifest)
    if manifest.name in _MANIFESTS:
        raise ValueError(f"connector {manifest.name!r} is already registered.")
    _MANIFESTS[manifest.name] = manifest


def unregister_manifest(name):
    """Test-only; production manifests register once at startup."""
    _MANIFESTS.pop(name, None)


def get_manifest(name):
    return _MANIFESTS.get(name)


def list_manifests():
    return [_MANIFESTS[name] for name in sorted(_MANIFESTS)]
