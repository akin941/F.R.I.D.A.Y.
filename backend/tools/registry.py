"""Tool registry (MVP 5 branch 2, brief §2).

A plain Python dict, not a database model: with exactly one concrete tool
(the Obsidian vault export, branch 3) and no agent runtime to introspect a
dynamic registry, a DB-backed `ToolDefinition` model would be exactly the
kind of speculative generality CLAUDE.md's simplicity rule warns against.
Extract one once a second tool, or agent-driven tool selection, actually
needs it (MVP 7).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ToolValidationError(Exception):
    """Raised by `ToolSpec.validate_request` to reject a request before its
    `ApprovalRequest` row is even created. `tools` must not import
    `approvals` (one-way dependency, pyproject import-linter contract), so
    this is a tools-local exception type -- `approvals.services.
    create_request` (which already imports `tools`) catches it and
    re-raises its own `InvalidTransition`, translating at the boundary
    rather than the other direction."""


@dataclass(frozen=True)
class ToolSpec:
    """`create_execution` and `dispatch` are called, in this order, by
    `approvals.services.approve_request`:

    1. `create_execution(approval_request)` runs and its write COMMITS
       inside the same transaction as the approval decision itself --
       before the broker is ever contacted -- so a durable execution row
       exists even if the publish that follows never lands. Mirrors
       `memory.jobs.create_extraction_job`'s `transaction.on_commit`
       ordering, one level more granular (creation and dispatch are the
       two moments kept in order here, not creation-then-dispatch as a
       single later addition).
    2. `dispatch(execution_id)` runs via `transaction.on_commit`, after
       that transaction has committed. A real tool's `dispatch` MUST
       swallow broker-publish failures itself, never propagate them --
       see `memory.jobs.dispatch_extraction_job` for the canonical
       non-propagating shape this mirrors.

    `validate_request(workspace, payload)` (branch 3, brief §3) is called by
    `approvals.services.create_request` BEFORE the `ApprovalRequest` row is
    created. It must do only zero-filesystem-access, purely-configuration
    checks (e.g. the vault export tool's tier-1 `OBSIDIAN_VAULT_PATH`
    string validation) -- anything that needs real filesystem access (tier
    2) can only run in the worker container, at execution time, and belongs
    in `create_execution`/the dispatched task instead. Raise
    `ToolValidationError` to reject the request with a 400; return `None`
    to allow it. Optional -- tools with nothing to validate up front simply
    omit it.

    `describe_execution(approval_request)` (branch 4, brief §4) is called by
    `approvals.serializers.ApprovalRequestSerializer` on every read -- the
    UI's run-status indicator needs to know whether an approved request's
    tool has actually finished, not just that it was approved (dispatch is
    async). Must return a small JSON-safe dict (or `None` if nothing has
    run yet), mirroring `describe_payload()`'s not-yet-implemented sibling
    contract: never the tool's raw internal row, no file contents, no
    unsanitized error text -- the same discipline `VaultExportRun`'s own
    sanitized `error_type`/`error_message` fields already enforce. Optional
    -- a tool with nothing to report simply omits it.

    `user_invocable` (Phase 3 branch 6, plan-review round 9 item 3): `False`
    for tools only a system component may request -- the harness gates
    (`harness_implement`/`harness_push`) are created by the harness itself.
    Such tools are left out of `list_tool_names()` (so the chat router and
    the agent planner never propose them) AND
    `approvals.services.create_request` refuses them unless its owning
    service passes `system_created=True` (ADR-010; the HTTP view never
    does), so a direct `POST /approvals` can't mint one either, whatever
    origin it claims.

    `proposable` (Phase 4 branch 2, ADR-009): `False` for a tool only a
    person may request directly (the trash view's "Kalıcı olarak sil" ->
    `purge_trash`). Such tools are left out of `list_tool_names()` (the chat
    router and agent planner never propose them) and
    `approvals.services.create_request` refuses them for any non-human
    origin.

    `describe_request(approval_request)` (Phase 3 branch 6) returns the
    safe, whitelisted `display_summary` the approvals UI shows the decider
    -- built only from the request's own snapshotted payload and immutable
    snapshots, never the raw `payload` itself (which the serializer still
    never exposes). Optional.
    """

    name: str
    gated: bool
    create_execution: Callable[[Any], Any]
    dispatch: Callable[[Any], None]
    validate_request: Callable[[Any, dict], None] | None = None
    describe_execution: Callable[[Any], dict | None] | None = None
    user_invocable: bool = True
    describe_request: Callable[[Any], dict | None] | None = None
    proposable: bool = True


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> None:
    _REGISTRY[spec.name] = spec


def unregister_tool(name: str) -> None:
    """Test-only. Production registration happens once, at import time,
    and is permanent for the life of the process."""
    _REGISTRY.pop(name, None)


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def list_tool_names() -> list[str]:
    """Registered, user-invocable tool names only (MVP 6 §4; the
    `user_invocable` filter since Phase 3 branch 6) -- no metadata. `assistant`
    (the router) uses this to build its own natural-language tool
    vocabulary for classification prompts and to verify a classifier's
    `tool_name` output actually names something registered, without this
    registry growing a searchable-description field it doesn't otherwise
    need for a single-tool MVP (see this module's own docstring on why
    that would be premature). Sorted for a deterministic prompt/test
    ordering, not registration order."""
    return sorted(
        name for name, spec in _REGISTRY.items() if spec.user_invocable and spec.proposable
    )
