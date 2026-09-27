"""Connector writes: gated, snapshot-backed, at most once (ADR-010 decision
6, brief D6).

Every write tool of an active manifest is registered as a `gated=True`
`ToolSpec` named by `manifest.gate_name(policy)` (generic default
`mcp_<connector>_<tool>`), decided through the approval gate over the web
channel only (`approvals.services.approve_request`). The request's life:

1. **Request** (`prepare_write_payload`, tier 1, no network): the workspace
   must have an enabled binding whose pin check says the tool is `ok`; the
   caller's `{"arguments": {...}}` is validated by the tool's strict
   serializer and scope check. The stored payload is built here, wholesale
   -- normalized arguments, their sha256, binding id, manifest version,
   schema pin and every display field -- so nothing the client sent beyond
   `arguments` survives.
2. **Decision dialog** (`describe_write_request`): built from that payload
   only -- the tool's renderer rows, or a generic row per argument in full
   -- plus `arguments_json`, the exact canonical JSON that will be sent.
3. **Approval** (`create_write_execution`, inside the decision's
   transaction): re-checks manifest version, schema pin and binding; any
   change ends the execution `failed`/`connector_changed` right there (no
   call is ever made). Otherwise a `pending` `ConnectorWriteExecution`.
4. **Execution** (`connectors.tasks.run_connector_write`): claims the
   `pending` row (sets `started_at`), re-checks the binding, then sends
   exactly the snapshotted arguments once (pin re-checked in the same MCP
   session). Never retried: a failure before the send is `failed`, one
   after it (timeout, transport) is `unknown_outcome`, and a worker lost
   mid-call is moved to `unknown_outcome` by the recovery scan -- never
   re-sent. Every terminal state is audited and leaves a Jarvis-authored
   `work_log` row (unless the tool opts out, D13).
"""

import logging
import uuid
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from connectors import mcp_client
from connectors.manifest import ScopeError, get_manifest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.reads import arguments_sha256, canonical_json, compose_result_text, json_safe
from connectors.services import TOOL_OK, record_listing, tool_available
from memory.context import strip_invisible_formatting
from memory.models import Memory
from tools.registry import ToolSpec, ToolValidationError, register_tool, unregister_tool
from tools.work_log import record_tool_outcome

logger = logging.getLogger(__name__)

# The sanitized result kept on the execution row (low-trust server text).
RESULT_MAX_CHARS = 4000
# A `pending` execution unclaimed this long had its dispatch lost.
PENDING_RECOVERY_GRACE_SECONDS = 60

Status = ConnectorWriteExecution.Status
ErrorType = ConnectorWriteExecution.ErrorType


def _resolve(connector, tool):
    manifest = get_manifest(connector)
    if manifest is None or manifest.blocked:
        return None, None
    return manifest, manifest.get_tool(tool)


def _display_rows(policy, arguments):
    if policy.renderer is not None:
        return [
            {"label": str(row["label"]), "value": str(row["value"])}
            for row in policy.renderer(arguments)
        ]
    # Generic fallback: every argument, in full (D6).
    return [
        {
            "label": name,
            "value": value if isinstance(value, str) else canonical_json(value),
        }
        for name, value in sorted(arguments.items())
    ]


def prepare_write_payload(connector, tool, workspace, payload):
    manifest, policy = _resolve(connector, tool)
    if policy is None:
        raise ToolValidationError("This connector tool is not available.")
    binding = ConnectorBinding.objects.filter(
        workspace=workspace, connector=manifest.name, enabled=True
    ).first()
    if binding is None:
        raise ToolValidationError("This connector is not enabled for this workspace.")
    if not tool_available(binding, policy):
        raise ToolValidationError("This connector tool is disabled until its pin check passes.")
    arguments = (payload or {}).get("arguments")
    if not isinstance(arguments, dict):
        raise ToolValidationError("payload.arguments must be an object.")
    serializer = policy.argument_serializer(data=arguments)
    if not serializer.is_valid():
        # Field names only -- the rejected values are the caller's data.
        raise ToolValidationError(f"Invalid arguments: {', '.join(sorted(serializer.errors))}.")
    clean = json_safe(serializer.validated_data)
    if policy.scope_check is not None:
        try:
            policy.scope_check(workspace, clean)
        except ScopeError as exc:
            raise ToolValidationError("The arguments are outside this workspace.") from exc
    return {
        "connector": manifest.name,
        "tool": policy.name,
        "server_tool": policy.server_tool,
        "binding_id": str(binding.id),
        "manifest_version": manifest.version,
        "schema_sha256": policy.schema_sha256,
        "arguments": clean,
        "arguments_sha256": arguments_sha256(clean),
        "display": {
            "connector_name": manifest.display_name,
            "tool_description": policy.description,
            "data_class": manifest.data_class,
            "rows": _display_rows(policy, clean),
        },
    }


def describe_write_request(approval_request):
    """The decision dialog, from the snapshot only. `arguments_json` is the
    canonical JSON of exactly what `run_connector_write` sends."""
    payload = approval_request.payload or {}
    display = payload.get("display") or {}
    arguments = payload.get("arguments") or {}
    return {
        "kind": "connector_write",
        "connector": payload.get("connector", ""),
        "connector_name": display.get("connector_name", ""),
        "tool": payload.get("tool", ""),
        "tool_description": display.get("tool_description", ""),
        "data_class": display.get("data_class", ""),
        "manifest_version": payload.get("manifest_version"),
        "rows": display.get("rows", []),
        "arguments_json": canonical_json(arguments),
        "arguments_sha256": payload.get("arguments_sha256", ""),
    }


def _snapshot_changed(approval_request, connector, tool, binding):
    """Why the snapshot no longer matches the world, or `None`."""
    payload = approval_request.payload or {}
    manifest, policy = _resolve(connector, tool)
    if policy is None or payload.get("connector") != connector or payload.get("tool") != tool:
        return "manifest"
    if payload.get("manifest_version") != manifest.version:
        return "manifest_version"
    if (
        payload.get("schema_sha256") != policy.schema_sha256
        or payload.get("server_tool") != policy.server_tool
    ):
        return "schema_pin"
    if payload.get("arguments_sha256") != arguments_sha256(payload.get("arguments") or {}):
        return "arguments"
    if binding is None or str(binding.id) != str(payload.get("binding_id")):
        return "binding"
    if not binding.enabled or binding.tool_status(policy.name) != TOOL_OK:
        return "binding"
    return None


def create_write_execution(connector, tool, approval_request):
    """`ToolSpec.create_execution`, inside the approval's transaction. Never
    raises for a changed connector: the execution is created already
    terminal (`failed`/`connector_changed`, audited, work_log) so the
    decision stands and nothing is ever sent."""
    payload = approval_request.payload or {}
    binding = (
        ConnectorBinding.objects.select_for_update()
        .filter(pk=_uuid_or_none(payload.get("binding_id")), workspace=approval_request.workspace)
        .first()
    )
    changed = _snapshot_changed(approval_request, connector, tool, binding)
    execution = ConnectorWriteExecution.objects.create(
        workspace=approval_request.workspace,
        approval_request=approval_request,
        binding=binding,
        connector=connector,
        tool=tool,
    )
    if changed is not None:
        _finalize(
            execution,
            status=Status.FAILED,
            error_type=ErrorType.CONNECTOR_CHANGED,
            detail=changed,
        )
    return str(execution.id)


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def dispatch_write_execution(execution_id):
    """`ToolSpec.dispatch`; a broker failure never propagates (the recovery
    scan re-publishes a `pending` row -- never a `running` one)."""
    from connectors.tasks import run_connector_write

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_connector_write.delay(execution_id)
        return
    try:
        run_connector_write.delay(execution_id)
    except Exception:
        logger.exception("Failed to publish connector write %s to the broker", execution_id)


def describe_write_execution(approval_request):
    execution = ConnectorWriteExecution.objects.filter(approval_request=approval_request).first()
    if execution is None:
        return None
    return {
        "status": execution.status,
        "error_type": execution.error_type,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
    }


# --- execution ---


def claim_write_execution(execution_id):
    """`pending` -> `running` only. A `running` row is never taken over,
    even with an expired lease: its send may already have happened."""
    now = timezone.now()
    with transaction.atomic():
        execution = (
            ConnectorWriteExecution.objects.select_for_update(skip_locked=True)
            .filter(pk=execution_id, status=Status.PENDING)
            .first()
        )
        if execution is None:
            return None
        token = uuid.uuid4()
        execution.status = Status.RUNNING
        execution.claim_token = token
        execution.started_at = now
        execution.lease_expires_at = now + timedelta(seconds=settings.CONNECTOR_WRITE_LEASE_SECONDS)
        execution.save(
            update_fields=["status", "claim_token", "started_at", "lease_expires_at", "updated_at"]
        )
    return token


def execute_claimed_write(execution_id, claim_token):
    execution = ConnectorWriteExecution.objects.select_related(
        "approval_request", "binding", "workspace"
    ).get(pk=execution_id)
    approval_request = execution.approval_request
    changed = _snapshot_changed(
        approval_request, execution.connector, execution.tool, execution.binding
    )
    if changed is not None:
        _finalize_fenced(
            execution_id,
            claim_token,
            status=Status.FAILED,
            error_type=ErrorType.CONNECTOR_CHANGED,
            detail=changed,
        )
        return
    payload = approval_request.payload
    manifest, policy = _resolve(execution.connector, execution.tool)
    try:
        result = mcp_client.call_tool(
            manifest,
            server_tool=payload["server_tool"],
            arguments=payload["arguments"],
            schema_sha256=payload["schema_sha256"],
            timeout=settings.CONNECTOR_WRITE_TIMEOUT_SECONDS,
        )
    except mcp_client.ConnectorError as exc:
        if exc.listing is not None:
            record_listing(execution.binding, manifest, exc.listing, only_tool=policy.name)
        error_type = {
            "pin_mismatch": ErrorType.PIN_MISMATCH,
            "tool_missing": ErrorType.PIN_MISMATCH,
            "not_configured": ErrorType.NOT_CONFIGURED,
            "timeout": ErrorType.TIMEOUT,
            "transport_error": ErrorType.TRANSPORT_ERROR,
            "protocol_error": ErrorType.PROTOCOL_ERROR,
        }.get(exc.code, ErrorType.UNKNOWN)
        _finalize_fenced(
            execution_id,
            claim_token,
            status=Status.UNKNOWN_OUTCOME if exc.sent else Status.FAILED,
            error_type=error_type,
        )
        return
    text, _size, truncated = compose_result_text(result.texts, result.structured)
    stored = {
        "text": strip_invisible_formatting(text)[:RESULT_MAX_CHARS],
        "truncated": truncated or len(text) > RESULT_MAX_CHARS,
        "dropped_blocks": result.dropped_blocks,
    }
    _finalize_fenced(
        execution_id,
        claim_token,
        status=Status.FAILED if result.is_error else Status.SUCCEEDED,
        error_type=ErrorType.TOOL_ERROR if result.is_error else "",
        result=stored,
    )


def _finalize_fenced(execution_id, claim_token, **outcome):
    with transaction.atomic():
        execution = (
            ConnectorWriteExecution.objects.select_for_update()
            .filter(pk=execution_id, status=Status.RUNNING, claim_token=claim_token)
            .first()
        )
        if execution is None:
            return False
        _finalize(execution, **outcome)
    return True


def _finalize(execution, *, status, error_type="", result=None, detail=""):
    """Terminal write for an execution already locked (or just created) in
    the caller's transaction: status, audit, work_log."""
    execution.status = status
    execution.error_type = error_type
    execution.claim_token = None
    execution.lease_expires_at = None
    execution.finished_at = timezone.now()
    if result is not None:
        execution.result = result
    execution.save(
        update_fields=[
            "status",
            "error_type",
            "claim_token",
            "lease_expires_at",
            "finished_at",
            "result",
            "updated_at",
        ]
    )
    payload = execution.approval_request.payload or {}
    metadata = {
        "connector": execution.connector,
        "tool": execution.tool,
        "binding": str(execution.binding_id or ""),
        "approval_request": str(execution.approval_request_id),
        "arguments_sha256": payload.get("arguments_sha256", ""),
        "error_type": error_type,
    }
    if detail:
        metadata["changed"] = detail
    record_audit(
        action=f"connector.write.{status}",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=execution.workspace,
        resource=execution,
        metadata=metadata,
    )
    manifest = get_manifest(execution.connector)
    personal = manifest is not None and manifest.data_class == "personal"
    summary = f"Connector write {execution.connector}.{execution.tool} (execution {execution.id})"
    if error_type:
        summary += f": {error_type}"
    record_tool_outcome(
        execution.approval_request,
        outcome=str(status),
        summary=summary + ".",
        sensitivity=Memory.Sensitivity.PRIVATE if personal else Memory.Sensitivity.NORMAL,
    )


def mark_lost_executions(now=None):
    """Recovery half of the scan: a `running` row whose lease expired had
    its worker lost somewhere around the send -> `unknown_outcome`, never a
    retry. Returns how many were moved."""
    now = now or timezone.now()
    moved = 0
    stale_ids = list(
        ConnectorWriteExecution.objects.filter(
            status=Status.RUNNING, lease_expires_at__lt=now
        ).values_list("id", flat=True)
    )
    for execution_id in stale_ids:
        with transaction.atomic():
            execution = (
                ConnectorWriteExecution.objects.select_for_update(skip_locked=True)
                .filter(pk=execution_id, status=Status.RUNNING, lease_expires_at__lt=now)
                .first()
            )
            if execution is None:
                continue
            _finalize(execution, status=Status.UNKNOWN_OUTCOME, error_type=ErrorType.WORKER_LOST)
            moved += 1
    return moved


def fail_unexpected(execution_id, claim_token):
    """An unexpected exception after the claim: whether the send happened is
    unknown, so the row ends `unknown_outcome` (never re-sent)."""
    _finalize_fenced(
        execution_id,
        claim_token,
        status=Status.UNKNOWN_OUTCOME,
        error_type=ErrorType.UNKNOWN,
    )


# --- registration ---


def build_write_tool(manifest, policy):
    return ToolSpec(
        name=manifest.gate_name(policy),
        gated=True,
        create_execution=partial(create_write_execution, manifest.name, policy.name),
        dispatch=dispatch_write_execution,
        prepare_payload=partial(prepare_write_payload, manifest.name, policy.name),
        describe_request=describe_write_request,
        describe_execution=describe_write_execution,
        user_invocable=policy.user_invocable,
        records_work_log=policy.records_work_log,
    )


def register_writes(manifest):
    if manifest.blocked:
        return
    for policy in manifest.writes:
        register_tool(build_write_tool(manifest, policy))


def unregister_writes(manifest):
    """Test-only."""
    for policy in manifest.writes:
        unregister_tool(manifest.gate_name(policy))
