"""Async connector reads (Phase 12 D11, ADR-010 amendment 2026-11).

A read declared `mode="async"` (an n8n workflow that takes seconds to
minutes) is not run in the chat request thread:

1. **Start** (`start_async_read`, the `ReadToolSpec.start_async` of the
   tool): denied first inside an unattended run (before any row or
   dispatch); then exactly the refusals of a sync read (channel, binding,
   pin, serializer, scope check, per-call class) -- a refused start writes
   its `connector.read` audit row (`mode=async`) and no row. Otherwise one
   `pending` `ConnectorAsyncRead` row, and `run_async_read` is published
   after commit. Returns an `AsyncReadHandle` (the row id).
2. **Run** (`connectors.tasks.run_async_read`): claims the `pending` row
   (`running`, `started_at`), re-checks the binding and the pin, makes the
   **one** call (pin re-checked in the same MCP session, the tool's own
   timeout), stores the bounded, stripped text and ends `ok` / `failed` /
   `timed_out`. Never retried: a second delivery finds the row claimed.
   One `connector.read` audit row (`mode=async`), never arguments or
   content. Then `tools.registry.notify_async_read(handle)`.
3. **Recovery** (`recover_async_reads`, every minute): a `pending` row whose
   dispatch was lost is re-published (it was never sent) until its
   deadline, then `failed`/`dispatch_lost`; a `running` row past its
   deadline had its worker lost -> `failed`/`worker_lost`, never re-sent.
   Both notify. Result text older than a day is cleared.
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from connectors import bridges, mcp_client
from connectors.manifest import get_manifest
from connectors.models import ConnectorAsyncRead, ConnectorBinding
from connectors.reads import (
    arguments_sha256,
    checked_arguments,
    compose_result_text,
    read_timeout,
)
from connectors.services import enabled_binding, record_listing, tool_available
from core.unattended import deny_if_unattended
from memory.context import strip_invisible_formatting
from tools.registry import (
    ASYNC_FAILED,
    ASYNC_OK,
    ASYNC_PENDING,
    ASYNC_RUNNING,
    ASYNC_TIMED_OUT,
    AsyncReadHandle,
    AsyncReadOutcome,
    ReadResult,
    ReadToolError,
    notify_async_read,
)

logger = logging.getLogger(__name__)

Status = ConnectorAsyncRead.Status

# A `pending` row unclaimed this long had its dispatch lost (re-published).
PENDING_RECOVERY_GRACE_SECONDS = 60
# Beyond the call timeout: how long a `running` row may stay before its
# worker counts as lost.
RUNNING_GRACE_SECONDS = 60
# Terminal rows keep their result text this long (the follow-up consumes
# it within seconds); afterwards only the bookkeeping stays.
RESULT_RETENTION = timedelta(days=1)

_STATUS_MAP = {
    Status.PENDING: ASYNC_PENDING,
    Status.RUNNING: ASYNC_RUNNING,
    Status.OK: ASYNC_OK,
    Status.FAILED: ASYNC_FAILED,
    Status.TIMED_OUT: ASYNC_TIMED_OUT,
}


def _handle(row):
    return AsyncReadHandle(id=str(row.id), tool=row.tool)


def _audit(*, actor, workspace, binding, metadata):
    record_audit(
        action="connector.read",
        actor=actor,
        actor_type=AuditLog.ActorType.HUMAN if actor is not None else AuditLog.ActorType.SYSTEM,
        workspace=workspace,
        resource=binding,
        metadata={**metadata, "mode": "async"},
    )


def start_async_read(manifest, policy, *, workspace, actor, args, channel):
    """`ReadToolSpec.start_async` of an async connector read (see module
    docstring). Raises `ReadToolError` on any refusal."""
    deny_if_unattended("connector_read")
    binding = enabled_binding(workspace, manifest.name)
    audit = {
        "connector": manifest.name,
        "tool": policy.name,
        "binding": str(binding.id) if binding else "",
        "channel": channel,
        "args_sha256": arguments_sha256(args if isinstance(args, dict) else {}),
        "size": 0,
        "truncated": False,
        "outcome": "started",
    }
    try:
        clean, data_class = checked_arguments(
            manifest,
            policy,
            workspace=workspace,
            binding=binding,
            args=args,
            channel=channel,
            audit=audit,
        )
    except ReadToolError as exc:
        audit["outcome"] = exc.code
        _audit(actor=actor, workspace=workspace, binding=binding, metadata=audit)
        raise
    with transaction.atomic():
        row = ConnectorAsyncRead.objects.create(
            workspace=workspace,
            binding=binding,
            connector=manifest.name,
            tool=policy.name,
            channel=channel,
            requested_by=actor if getattr(actor, "pk", None) else None,
            manifest_version=manifest.version,
            schema_sha256=policy.schema_sha256,
            arguments=clean,
            arguments_sha256=arguments_sha256(clean),
            data_class=data_class,
        )
        read_id = str(row.id)
        transaction.on_commit(lambda: dispatch_async_read(read_id))
    return _handle(row)


def dispatch_async_read(read_id):
    """Publish `run_async_read`; a broker failure never propagates (the
    recovery scan re-publishes a `pending` row)."""
    from connectors.tasks import run_async_read

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_async_read.delay(read_id)
        return
    try:
        run_async_read.delay(read_id)
    except Exception:
        logger.exception("Failed to publish async read %s to the broker", read_id)


def fetch_async_read(handle):
    """`ReadToolSpec.fetch_async`: where the read stands, with its bounded
    text once `ok`. A missing row reads as `failed`/`not_found`."""
    row = ConnectorAsyncRead.objects.filter(pk=_uuid_or_none(handle.id), tool=handle.tool).first()
    if row is None:
        return AsyncReadOutcome(status=ASYNC_FAILED, error_code="not_found")
    status = _STATUS_MAP.get(row.status, ASYNC_FAILED)
    result = None
    if status == ASYNC_OK:
        result = ReadResult(
            text=row.result_text,
            size=row.result_size,
            truncated=row.truncated,
            data_class=row.data_class,
        )
    return AsyncReadOutcome(
        status=status, result=result, error_code=row.error_code, data_class=row.data_class
    )


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def deadline_seconds(row):
    manifest = get_manifest(row.connector)
    policy = manifest.get_tool(row.tool) if manifest else None
    timeout = read_timeout(policy) if policy else settings.CONNECTOR_READ_TIMEOUT_SECONDS
    return timeout + RUNNING_GRACE_SECONDS


# --- execution ---


def claim_async_read(read_id):
    """`pending` -> `running` only (sets `started_at`). Returns the row or
    `None` when it was not `pending` (already claimed, finished, gone)."""
    with transaction.atomic():
        row = (
            ConnectorAsyncRead.objects.select_for_update(skip_locked=True)
            .filter(pk=read_id, status=Status.PENDING)
            .first()
        )
        if row is None:
            return None
        row.status = Status.RUNNING
        row.started_at = timezone.now()
        row.save(update_fields=["status", "started_at", "updated_at"])
    return row


def execute_claimed_read(row):
    """The one call of a claimed row. Never raises for a connector failure:
    the row ends terminal either way."""
    started = timezone.now()
    manifest = get_manifest(row.connector)
    policy = manifest.get_tool(row.tool) if manifest and not manifest.blocked else None
    binding = (
        ConnectorBinding.objects.filter(pk=row.binding_id, workspace_id=row.workspace_id).first()
        if row.binding_id
        else None
    )
    if (
        policy is None
        or manifest.version != row.manifest_version
        or policy.schema_sha256 != row.schema_sha256
    ):
        return _finish(row, status=Status.FAILED, error_code="connector_changed", started=started)
    if not tool_available(binding, policy):
        return _finish(row, status=Status.FAILED, error_code="not_available", started=started)
    try:
        result = bridges.call_tool(
            manifest,
            server_tool=policy.server_tool,
            arguments=row.arguments,
            schema_sha256=policy.schema_sha256,
            timeout=read_timeout(policy),
        )
    except mcp_client.ConnectorError as exc:
        if exc.listing is not None and binding is not None:
            record_listing(binding, manifest, exc.listing, only_tool=policy.name)
        status = Status.TIMED_OUT if exc.code == "timeout" else Status.FAILED
        return _finish(row, status=status, error_code=exc.code, started=started)
    if result.is_error:
        return _finish(row, status=Status.FAILED, error_code="tool_error", started=started)
    text, size, truncated = compose_result_text(result.texts, result.structured)
    return _finish(
        row,
        status=Status.OK,
        started=started,
        text=strip_invisible_formatting(text.replace("\r", "")),
        size=size,
        truncated=truncated,
    )


def _finish(row, *, status, started, error_code="", text="", size=0, truncated=False):
    """Terminal write for a `running` row (fenced on `running`), its audit
    row and the listeners' notification after commit. Returns whether this
    call ended the row."""
    with transaction.atomic():
        locked = (
            ConnectorAsyncRead.objects.select_for_update()
            .filter(pk=row.pk, status=Status.RUNNING)
            .first()
        )
        if locked is None:
            return False
        _terminal_write(
            locked, status=status, error_code=error_code, text=text, size=size, truncated=truncated
        )
        _audit(
            actor=locked.requested_by,
            workspace=locked.workspace,
            binding=locked.binding,
            metadata={
                "connector": locked.connector,
                "tool": locked.tool,
                "binding": str(locked.binding_id or ""),
                "channel": locked.channel,
                "args_sha256": locked.arguments_sha256,
                "size": size,
                "truncated": truncated,
                "outcome": "ok" if status == Status.OK else error_code or str(status),
                "duration_ms": int((timezone.now() - started).total_seconds() * 1000),
                **({"data_class": locked.data_class} if locked.data_class else {}),
            },
        )
        handle = _handle(locked)
        transaction.on_commit(lambda: notify_async_read(handle))
    return True


def _terminal_write(row, *, status, error_code="", text="", size=0, truncated=False):
    row.status = status
    row.error_code = error_code
    row.result_text = text
    row.result_size = size
    row.truncated = truncated
    row.finished_at = timezone.now()
    # Only the sha256 of what was sent stays once the read has ended.
    row.arguments = {}
    row.save(
        update_fields=[
            "status",
            "error_code",
            "result_text",
            "result_size",
            "truncated",
            "finished_at",
            "arguments",
            "updated_at",
        ]
    )


def fail_unexpected(row):
    """An unexpected exception after the claim: the read ends `failed`
    (`unknown`) -- a read is never re-sent either way."""
    _finish(row, status=Status.FAILED, error_code="unknown", started=row.started_at)


def recover_async_reads(now=None):
    """The recovery scan (see module docstring). Returns counts."""
    now = now or timezone.now()
    redispatched = dispatch_lost = worker_lost = 0
    pending = ConnectorAsyncRead.objects.filter(
        status=Status.PENDING,
        created_at__lt=now - timedelta(seconds=PENDING_RECOVERY_GRACE_SECONDS),
    ).order_by("created_at")
    for row in pending:
        if row.created_at + timedelta(seconds=deadline_seconds(row)) < now:
            if _expire(row.pk, Status.PENDING, error_code="dispatch_lost"):
                dispatch_lost += 1
            continue
        dispatch_async_read(str(row.pk))
        redispatched += 1
    for row in ConnectorAsyncRead.objects.filter(status=Status.RUNNING).order_by("started_at"):
        started = row.started_at or row.created_at
        if started + timedelta(seconds=deadline_seconds(row)) < now:
            if _expire(row.pk, Status.RUNNING, error_code="worker_lost"):
                worker_lost += 1
    cleared = (
        ConnectorAsyncRead.objects.filter(
            status__in=list(ConnectorAsyncRead.TERMINAL),
            finished_at__lt=now - RESULT_RETENTION,
        )
        .exclude(result_text="")
        .update(result_text="")
    )
    return {
        "redispatched": redispatched,
        "dispatch_lost": dispatch_lost,
        "worker_lost": worker_lost,
        "cleared": cleared,
    }


def _expire(read_id, from_status, *, error_code):
    with transaction.atomic():
        row = (
            ConnectorAsyncRead.objects.select_for_update(skip_locked=True)
            .filter(pk=read_id, status=from_status)
            .first()
        )
        if row is None:
            return False
        _terminal_write(row, status=Status.FAILED, error_code=error_code)
        _audit(
            actor=None,
            workspace=row.workspace,
            binding=row.binding,
            metadata={
                "connector": row.connector,
                "tool": row.tool,
                "binding": str(row.binding_id or ""),
                "channel": row.channel,
                "args_sha256": row.arguments_sha256,
                "size": 0,
                "truncated": False,
                "outcome": error_code,
            },
        )
        handle = _handle(row)
        transaction.on_commit(lambda: notify_async_read(handle))
    return True
