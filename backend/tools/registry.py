"""Tool registry (MVP 5 branch 2, brief §2).

A plain Python dict, not a database model: with exactly one concrete tool
(the Obsidian vault export, branch 3) and no agent runtime to introspect a
dynamic registry, a DB-backed `ToolDefinition` model would be exactly the
kind of speculative generality CLAUDE.md's simplicity rule warns against.
Extract one once a second tool, or agent-driven tool selection, actually
needs it (MVP 7).

Two registries live here (Phase 5 branch 1, ADR-010):

- `ToolSpec` -- gated writes, decided through `approvals` (ADR-004).
- `ReadToolSpec` -- ungated, audited connector reads the chat router may
  pick (brief D4). `connectors` registers them; `assistant` discovers them
  only through `list_read_tools()`, so it never imports `connectors`
  (import-linter contract). A name belongs to exactly one registry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.channels import CHANNEL_CLI, CHANNEL_NODE_VOICE, CHANNEL_WEB
from core.unattended import deny_if_unattended


class ToolDecisionForbidden(Exception):
    """Raised by `ToolSpec.authorize_decision` to refuse an approval by this
    actor (Phase 10 D6, the approver check). A tools-local type like
    `ToolValidationError`: `approvals.services.approve_request` translates
    it into its own `ApprovalPermissionDenied` (403); the request stays
    `pending` and no execution is created."""


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

    `prepare_payload(workspace, payload) -> dict` (Phase 5 branch 4, brief
    D6): tier-1, no-network validation that also RETURNS the payload to
    store -- the request's snapshot. Called by `approvals.services.
    create_request` after `validate_request`; its return value replaces the
    caller-supplied payload wholesale, so a client can never smuggle a field
    the tool did not put there itself (a connector write snapshots its
    normalized arguments, binding, manifest version and schema pin here).
    Raise `ToolValidationError` to refuse. Optional.

    `records_work_log` (Phase 5 branch 1, brief D13): whether a terminal
    execution of this tool leaves a Friday-authored `work_log` memory
    (`tools.work_log.record_tool_outcome`, ADR-008 decision 4). Default
    True; a tool whose outcomes are audit-worthy but not memory-worthy
    (e.g. a Home Assistant light toggle, VISION R5) sets False. Audit rows
    are unaffected either way.

    Phase 10 branch 3 (brief D6) adds three optional hooks:

    - `approval_ttl_seconds`: a per-tool approval TTL. The decision-time
      expiry and the sweep use `min(approval_ttl_seconds,
      APPROVAL_REQUEST_TTL_HOURS)`, so a tool can only shorten the global
      TTL, never lengthen it. `None` (default) keeps the global TTL.
    - `precheck_request(workspace, actor, payload)`: runs in
      `approvals.services.create_request` OUTSIDE any database transaction,
      after the role, tool and `validate_request` checks, and before
      `prepare_payload`. Its return value reaches `prepare_payload` only as
      the keyword `precheck=` (a tool that declares this hook must accept
      it); a client can never supply it. For work that must not run inside
      a transaction or for an unauthorised caller (the node action's trust
      check, a model call). Raise `ToolValidationError` to refuse.
    - `authorize_decision(approval_request, actor)`: called by
      `approve_request` after its curator and channel checks and before the
      status changes. Raise `ToolDecisionForbidden` to refuse this approver
      (403, the request stays pending). `reject_request` never calls it:
      declining is the safe direction.
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
    records_work_log: bool = True
    prepare_payload: Callable[[Any, dict], dict] | None = None
    # Phase 8B branch 7: `describe_request` depends only on the request's
    # workspace and whether it is still pending (the vault export's counts
    # and dry pass), so one serialization computes it once per workspace
    # rather than once per row (no N+1 on the approvals list).
    describe_request_per_workspace: bool = False
    # Phase 10 branch 3 (brief D6): see the class docstring.
    approval_ttl_seconds: int | None = None
    precheck_request: Callable[[Any, Any, dict], Any] | None = None
    authorize_decision: Callable[[Any, Any], None] | None = None
    # Phase 12 D12 (branch 4): a gated tool whose draft card carries its
    # arguments (`{"arguments": {...}}`, the connector-write payload shape).
    # When set, the router (and a `tool.*` wake route) extracts them with
    # `router.connector_args` from the owner's own words, validates them
    # with this strict serializer and puts them on the draft; the owner
    # still sees, and decides, the exact arguments on /approvals. `None`
    # (default): the draft carries an empty payload, as before.
    draft_arguments: "DraftArguments | None" = None
    # A short Friday-authored label ("Ev yedeği") for the wake-route list;
    # empty = the tool name.
    label: str = ""


@dataclass(frozen=True)
class DraftArguments:
    """What the router needs to propose a gated tool's arguments (Phase 12
    D12): a Friday-authored description, the strict DRF serializer the
    request is validated by, and the tool's data class (a `personal` tool's
    argument call carries `personal_data=True`). Never server text."""

    description: str
    argument_serializer: Any
    data_class: str


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> None:
    if spec.name in _READ_REGISTRY:
        raise ValueError(f"{spec.name!r} is already registered as a read tool.")
    _REGISTRY[spec.name] = spec


def unregister_tool(name: str) -> None:
    """Test-only. Production registration happens once, at import time,
    and is permanent for the life of the process."""
    _REGISTRY.pop(name, None)


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def tools_with_approval_ttl() -> dict[str, int]:
    """`{tool name: approval_ttl_seconds}` for every registered tool with a
    per-tool TTL (Phase 10 D6; the approvals sweep)."""
    return {
        name: spec.approval_ttl_seconds
        for name, spec in _REGISTRY.items()
        if spec.approval_ttl_seconds is not None
    }


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


# --- Read tools (Phase 5 branch 1, ADR-010, brief D4) ---

DATA_CLASS_FIRM = "firm"
DATA_CLASS_PERSONAL = "personal"
DATA_CLASSES = frozenset({DATA_CLASS_FIRM, DATA_CLASS_PERSONAL})

# Channels a read may ever run over (brief Q8): `firm` reads web + CLI,
# `personal` reads web only; Telegram and the OpenAI-compatible facade never.
# Checked at registration, so a manifest cannot widen it -- only a change
# here (and to ADR-010) can. Phase 11 D4 (ADR-010 amendment 2026-11, the
# accepted risk of ADR-015 decision 7): both classes also over the node
# voice channel ("Hey Friday, bugün ne var?"); a manifest or `ReadToolSpec`
# that omits `node_voice` still refuses it. `cli` (personal), `hook`,
# `openai`, `telegram` and `node` stay refused.
READ_CHANNELS = {
    DATA_CLASS_FIRM: frozenset({CHANNEL_WEB, CHANNEL_CLI, CHANNEL_NODE_VOICE}),
    DATA_CLASS_PERSONAL: frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE}),
}


class ReadToolError(Exception):
    """Raised by `ReadToolSpec.execute` when a read is refused or fails.
    `code` is a closed-vocabulary token (e.g. `invalid_arguments`,
    `out_of_scope`, `not_available`, `timeout`, `tool_error`) -- never
    server or exception text. A tools-local type, like `ToolValidationError`,
    so `assistant` can handle it without importing `connectors`."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReadResult:
    """What a read hands back to the caller: text and, optionally, the
    server's `structuredContent`. Everything else an MCP result may carry
    (images, resources, links) is dropped before this object exists. Still
    low-trust data: the caller strips and fences it (brief D4) before it
    gets anywhere near a prompt.

    The connectors framework serializes `structuredContent` into `text`
    itself, so one size bound covers both: `text` is already bounded to
    `CONNECTOR_RESULT_MAX_CHARS`, `size` is its length before bounding and
    `truncated` says whether anything was cut (Phase 5 branch 4).

    `data_class` (Phase 5 branch 5) is set when this one call's data is of
    a stricter class than the spec's own -- a GitHub read of a `sensitive`
    `HarnessRepo` is `personal` although the connector is `firm` (brief
    D7/D5). Empty means "the spec's `data_class`"; a caller treats the
    stricter of the two as the call's class."""

    text: str
    structured: Any = None
    size: int = 0
    truncated: bool = False
    data_class: str = ""


@dataclass(frozen=True)
class ReadToolSpec:
    """An ungated connector read the chat router may pick (brief D4).

    - `name`/`description`: Friday-authored, never the server's own text
      (server descriptions never reach an LLM prompt, brief D3).
    - `argument_serializer`: a strict DRF serializer class validating the
      router-extracted arguments; invalid -> a plain refusal, no read.
    - `data_class`: `firm` or `personal`; a `personal` read marks the
      conversation private and routes to personal-data providers only
      (brief D5).
    - `channels`: the request channels the read may run over, a subset of
      `READ_CHANNELS[data_class]`.
    - `execute(*, workspace, actor, args, channel) -> ReadResult`: validates
      the arguments (serializer, scope check), performs the read and audits
      it (`connector.read`, every outcome); raises `ReadToolError` on any
      refusal or failure.
    - `is_available(workspace) -> bool`: whether the workspace has an
      enabled binding for the read's connector. A callback rather than a
      query here because `tools` must never import `connectors`
      (ADR-010). Required: no read is available by default.
    - `source`: a short, code-defined label of the system read (e.g.
      `github`), shown in the fence (`<connector_result source=...>`) and
      the chat's source badge. Never server-supplied text.
    """

    name: str
    description: str
    argument_serializer: Any
    data_class: str
    channels: frozenset
    execute: Callable[..., ReadResult]
    is_available: Callable[[Any], bool]
    source: str = ""
    # Phase 12 D11 (branch 4): an `async` read is started in the turn and
    # finishes later in a worker; its result reaches the conversation as one
    # follow-up message (`assistant.followups`). `start_async(*, workspace,
    # actor, args, channel) -> AsyncReadHandle` runs the same refusals as
    # `execute` and never waits for the result; `fetch_async(handle) ->
    # AsyncReadOutcome` reads where it stands. Both required for `async`.
    mode: str = "sync"
    start_async: Callable[..., "AsyncReadHandle"] | None = None
    fetch_async: Callable[["AsyncReadHandle"], "AsyncReadOutcome"] | None = None
    # A short Friday-authored label ("Sunucu durumu") for templates and
    # notices; empty = the tool name.
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.name


READ_MODE_SYNC = "sync"
READ_MODE_ASYNC = "async"
READ_MODES = frozenset({READ_MODE_SYNC, READ_MODE_ASYNC})

# `AsyncReadOutcome.status` (Phase 12 D11): the closed lifecycle of one
# async read. The last three are terminal.
ASYNC_PENDING = "pending"
ASYNC_RUNNING = "running"
ASYNC_OK = "ok"
ASYNC_FAILED = "failed"
ASYNC_TIMED_OUT = "timed_out"
ASYNC_TERMINAL = frozenset({ASYNC_OK, ASYNC_FAILED, ASYNC_TIMED_OUT})


@dataclass(frozen=True)
class AsyncReadHandle:
    """An opaque reference to one started async read: the id of the row
    its owner keeps (`connectors.ConnectorAsyncRead`) and the read tool's
    name. Holds no arguments and no result."""

    id: str
    tool: str


@dataclass(frozen=True)
class AsyncReadOutcome:
    """Where one async read stands. `result` only when `status == "ok"`
    (bounded, still low-trust text the caller fences); `error_code` a
    closed-vocabulary token otherwise. `data_class` as on `ReadResult`."""

    status: str
    result: ReadResult | None = None
    error_code: str = ""
    data_class: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in ASYNC_TERMINAL


_READ_REGISTRY: dict[str, ReadToolSpec] = {}


def register_read_tool(spec: ReadToolSpec) -> None:
    """Validates the spec's data class and channels and registers it.
    Raises `ValueError` -- a boot-time programming error, never a runtime
    refusal -- for an unknown data class, an empty channel set, a channel
    outside `READ_CHANNELS[data_class]`, or a name already used by a
    gated tool."""
    if spec.data_class not in DATA_CLASSES:
        raise ValueError(f"{spec.name!r}: unknown data class {spec.data_class!r}.")
    channels = frozenset(spec.channels)
    if not channels:
        raise ValueError(f"{spec.name!r}: a read tool needs at least one channel.")
    widened = channels - READ_CHANNELS[spec.data_class]
    if widened:
        raise ValueError(
            f"{spec.name!r}: channels {sorted(widened)} are not allowed for "
            f"{spec.data_class!r} reads."
        )
    if spec.name in _REGISTRY:
        raise ValueError(f"{spec.name!r} is already registered as a gated tool.")
    if spec.mode not in READ_MODES:
        raise ValueError(f"{spec.name!r}: unknown read mode {spec.mode!r}.")
    if spec.mode == READ_MODE_ASYNC and not (
        callable(spec.start_async) and callable(spec.fetch_async)
    ):
        raise ValueError(f"{spec.name!r}: an async read needs start_async and fetch_async.")
    _READ_REGISTRY[spec.name] = spec


def unregister_read_tool(name: str) -> None:
    """Test-only, like `unregister_tool`."""
    _READ_REGISTRY.pop(name, None)


def get_read_tool(name: str) -> ReadToolSpec | None:
    return _READ_REGISTRY.get(name)


def list_read_tool_names() -> list[str]:
    """Every registered read tool's name, sorted -- names only, no
    availability check (Phase 8A D8: the `self` block lists what Friday can
    read in general, never which workspace has a binding)."""
    return sorted(_READ_REGISTRY)


def list_read_tools(*, workspace, channel) -> list[ReadToolSpec]:
    """The read tools the router may offer for one turn: those allowed over
    `channel` whose connector has an enabled binding in `workspace`. Both
    arguments are required -- there is no "any channel" or "any workspace"
    listing. Sorted by name for a deterministic prompt/test ordering."""
    return [
        spec
        for name, spec in sorted(_READ_REGISTRY.items())
        if channel in spec.channels and spec.is_available(workspace)
    ]


def run_read_tool(spec: ReadToolSpec, *, workspace, actor, args, channel) -> ReadResult:
    """Execute one read tool (Phase 12 D2). The only sanctioned call site of
    `ReadToolSpec.execute`: inside an unattended run the read is denied
    before the spec is touched (no network call, no audit row of its
    own); outside one, the call is exactly `spec.execute(...)`."""
    deny_if_unattended("read_tool")
    return spec.execute(workspace=workspace, actor=actor, args=args, channel=channel)


# --- Async reads (Phase 12 D11, branch 4) ---
#
# The owner of the read (`connectors`) runs it in a worker and, once it is
# terminal, calls `notify_async_read(handle)`; whoever waits for it
# (`assistant`, the follow-up) registered a listener at startup. `tools`
# imports neither side -- the same pattern as the read registry itself.

_ASYNC_LISTENERS: list[Callable[[AsyncReadHandle], None]] = []


def start_async_read(spec: ReadToolSpec, *, workspace, actor, args, channel) -> AsyncReadHandle:
    """Start one async read (Phase 12 D11). Like `run_read_tool`, the only
    sanctioned call site of `ReadToolSpec.start_async`: inside an unattended
    run it is denied before the spec is touched (no row, no dispatch)."""
    deny_if_unattended("read_tool")
    if spec.mode != READ_MODE_ASYNC or spec.start_async is None:
        raise ReadToolError("not_available")
    return spec.start_async(workspace=workspace, actor=actor, args=args, channel=channel)


def fetch_async_read(handle: AsyncReadHandle) -> AsyncReadOutcome:
    """Where the read behind `handle` stands. A tool that is no longer
    registered (or not async) reads as `failed`/`not_available`."""
    spec = _READ_REGISTRY.get(handle.tool)
    if spec is None or spec.fetch_async is None:
        return AsyncReadOutcome(status=ASYNC_FAILED, error_code="not_available")
    return spec.fetch_async(handle)


def register_async_read_listener(listener: Callable[[AsyncReadHandle], None]) -> None:
    """Called once per process at startup by the app that waits for async
    reads. Idempotent for the same callable."""
    if listener not in _ASYNC_LISTENERS:
        _ASYNC_LISTENERS.append(listener)


def unregister_async_read_listener(listener: Callable[[AsyncReadHandle], None]) -> None:
    """Test-only."""
    if listener in _ASYNC_LISTENERS:
        _ASYNC_LISTENERS.remove(listener)


def notify_async_read(handle: AsyncReadHandle) -> None:
    """An async read reached a terminal state: tell every listener. A
    listener failure is logged by class name and never propagates (the
    read's own row is already final; the follow-up's recovery picks it up)."""
    import logging

    for listener in list(_ASYNC_LISTENERS):
        try:
            listener(handle)
        except Exception as exc:  # noqa: BLE001 -- one listener never blocks another
            logging.getLogger(__name__).warning(
                "async read listener failed for %s: %s", handle.tool, type(exc).__name__
            )
