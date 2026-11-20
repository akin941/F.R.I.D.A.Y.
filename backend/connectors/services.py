"""Bindings, pins and health (ADR-010 decision 3, brief D3).

Role policy: listing and toggling bindings is curator-only (owner/admin of
the workspace), human-only and web-only -- the view adds the installation
owner check on top (`core.permissions.IsOwner`). Every toggle is audited.

Pins against tool poisoning: `check_binding_pins` compares the server's
`tools/list` with the manifest -- on enable (dispatched after commit), on the
pin beat (`connectors.tasks.check_connector_pins`, every 15 minutes) and,
in the same MCP session, before every read and write
(`connectors.mcp_client.call_tool`). Unlisted server tools are ignored; a
listed tool whose `inputSchema` hash differs from the manifest pin, or that
the server stopped offering, is disabled (`mismatch` / `missing`,
`connector.pin_mismatch` audit) until a commit updates the manifest: the
disable is sticky for the manifest version it happened under -- a server
that flips back to the pinned schema, or the owner toggling the binding off
and on, does not re-enable it; only a new manifest version (a reviewed
commit) clears it. A failed check (server down, timeout) never turns a tool
back on.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from connectors import bridges, mcp_client
from connectors.manifest import KIND_MCP, KIND_NATIVE, get_manifest, list_manifests
from connectors.models import ConnectorBinding
from core.channels import CHANNEL_WEB
from workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)

_CURATOR_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}

TOOL_OK = "ok"
TOOL_MISMATCH = "mismatch"
TOOL_MISSING = "missing"
TOOL_UNCHECKED = "unchecked"
_DISABLED_STATUSES = {TOOL_MISMATCH, TOOL_MISSING}


class ConnectorServiceError(Exception):
    code = "connector.invalid_transition"


class ConnectorPermissionDenied(ConnectorServiceError):
    """Role, channel or principal does not allow the action -> 403."""

    code = "connector.permission_denied"


class ConnectorInvalidTransition(ConnectorServiceError):
    """Unknown or blocked connector, or a workspace type the manifest does
    not bind to -> 400."""

    code = "connector.invalid_transition"


def get_role(actor, workspace):
    membership = (
        WorkspaceMember.objects.for_actor(actor, workspace=workspace).filter(user=actor).first()
    )
    return membership.role if membership else None


def _require_curator(actor, workspace, action):
    if getattr(actor, "is_agent", False):
        raise ConnectorPermissionDenied("Agent accounts cannot manage connectors.")
    if get_role(actor, workspace) not in _CURATOR_ROLES:
        raise ConnectorPermissionDenied(f"Role does not allow {action}.")


def bindable(manifest, workspace):
    """Whether `manifest` may ever bind to `workspace`: active, and the
    workspace's type is one the manifest names (personal-class connectors
    name only `personal`, D3)."""
    return not manifest.blocked and workspace.type in manifest.workspace_types


def list_bindings(*, workspace, actor):
    """Every manifest relevant to this workspace's type (blocked ones
    included, so the owner sees why they cannot be switched on), with its
    binding row or `None`. Curator-only."""
    _require_curator(actor, workspace, "viewing connectors")
    rows = {b.connector: b for b in ConnectorBinding.objects.filter(workspace=workspace)}
    return [
        (manifest, rows.get(manifest.name))
        for manifest in list_manifests()
        if workspace.type in manifest.workspace_types
    ]


def set_binding_enabled(*, workspace, actor, connector, enabled, channel):
    """Switch a connector on or off for one workspace (audited, idempotent:
    no change -> no audit row). Web channel and a human curator only;
    `channel` is required so no internal caller can toggle "as web" by
    accident. Enabling never makes a tool callable by itself: every tool
    starts `unchecked` until the pin check dispatched after commit passes."""
    if channel != CHANNEL_WEB:
        raise ConnectorPermissionDenied("Connectors can only be managed from the web app.")
    _require_curator(actor, workspace, "managing connectors")
    manifest = get_manifest(connector)
    if manifest is None:
        raise ConnectorInvalidTransition(f"Unknown connector {connector!r}.")
    if manifest.blocked:
        raise ConnectorInvalidTransition(f"{connector!r} is blocked and cannot be bound.")
    if workspace.type not in manifest.workspace_types:
        raise ConnectorInvalidTransition(f"{connector!r} cannot be bound to this workspace type.")
    with transaction.atomic():
        binding, _ = ConnectorBinding.objects.select_for_update().get_or_create(
            workspace=workspace, connector=connector
        )
        if binding.enabled == enabled:
            return binding
        binding.enabled = enabled
        update_fields = ["enabled", "updated_at"]
        if enabled:
            # Nothing is callable until a fresh pin check says so; a tool
            # disabled under this manifest version stays disabled.
            sticky = _sticky_disabled(binding.last_health, manifest)
            binding.last_health = {
                "manifest_version": manifest.version,
                "tools": {
                    policy.name: sticky.get(policy.name, TOOL_UNCHECKED)
                    for policy in manifest.tools
                },
            }
            update_fields.append("last_health")
        binding.save(update_fields=update_fields)
        record_audit(
            action="connector.binding.enabled" if enabled else "connector.binding.disabled",
            actor=actor,
            workspace=workspace,
            resource=binding,
            metadata={
                "connector": connector,
                "manifest_version": manifest.version,
                "channel": channel,
            },
        )
        if enabled:
            binding_id = str(binding.id)
            transaction.on_commit(lambda: dispatch_pin_check(binding_id))
    return binding


def dispatch_pin_check(binding_id):
    """Publish a pin check; never propagates a broker failure (the beat
    retries within 15 minutes, and tools stay `unchecked` until then)."""
    from connectors.tasks import check_binding_pins_task

    if settings.CELERY_TASK_ALWAYS_EAGER:
        check_binding_pins_task.delay(binding_id)
        return
    try:
        check_binding_pins_task.delay(binding_id)
    except Exception:
        logger.exception("Failed to publish the pin check for binding %s", binding_id)


def enabled_binding(workspace, connector):
    return ConnectorBinding.objects.filter(
        workspace=workspace, connector=connector, enabled=True
    ).first()


def tool_available(binding, policy):
    return bool(binding and binding.enabled and binding.tool_status(policy.name) == TOOL_OK)


def _sticky_disabled(health, manifest):
    """`{tool: status}` of the tools disabled under the current manifest
    version -- they stay disabled until the manifest version changes."""
    health = health or {}
    if health.get("manifest_version") != manifest.version:
        return {}
    return {
        name: status
        for name, status in (health.get("tools") or {}).items()
        if status in _DISABLED_STATUSES
    }


def _statuses(manifest, listing):
    statuses = {}
    for policy in manifest.tools:
        actual = listing.tool_hashes.get(policy.server_tool)
        if actual is None:
            statuses[policy.name] = TOOL_MISSING
        elif actual != policy.schema_sha256:
            statuses[policy.name] = TOOL_MISMATCH
        else:
            statuses[policy.name] = TOOL_OK
    return statuses


def check_binding_pins(binding_id):
    """Run one pin/health check for an enabled binding and record it.
    Returns the binding (or `None` if it no longer exists / is disabled)."""
    binding = ConnectorBinding.objects.filter(pk=binding_id).first()
    if binding is None or not binding.enabled:
        return None
    manifest = get_manifest(binding.connector)
    if manifest is None or manifest.blocked:
        return binding
    now = timezone.now().isoformat()
    if manifest.kind == KIND_NATIVE:
        bridge = bridges.get_bridge(manifest)
        if bridge is None:
            return binding
        health = bridge.check_health(manifest, timeout=settings.CONNECTOR_HEALTH_TIMEOUT_SECONDS)
        return record_native_health(binding, manifest, health, checked_at=now)
    if manifest.kind != KIND_MCP:
        return binding
    try:
        listing = mcp_client.list_tools(manifest, timeout=settings.CONNECTOR_HEALTH_TIMEOUT_SECONDS)
    except mcp_client.ConnectorError as exc:
        with transaction.atomic():
            locked = ConnectorBinding.objects.select_for_update().get(pk=binding.pk)
            health = dict(locked.last_health or {})
            health.update({"checked_at": now, "error": exc.code})
            locked.last_health = health
            locked.save(update_fields=["last_health", "updated_at"])
        return locked
    return record_listing(binding, manifest, listing, checked_at=now)


def record_listing(binding, manifest, listing, *, checked_at=None, only_tool=None):
    """Store a `tools/list` result's pin statuses on the binding and audit
    every tool that newly left `ok`/`unchecked` for `mismatch`/`missing`.
    `only_tool` limits the update to one tool (a failure detected by a
    single call must not re-enable anything else)."""
    statuses = _statuses(manifest, listing)
    if only_tool is not None:
        statuses = {only_tool: statuses.get(only_tool, TOOL_MISSING)}
    with transaction.atomic():
        locked = ConnectorBinding.objects.select_for_update().get(pk=binding.pk)
        health = dict(locked.last_health or {})
        sticky = _sticky_disabled(health, manifest)
        # Statuses recorded under an older manifest version are stale: the
        # new version's pins are checked from scratch.
        same_version = health.get("manifest_version") == manifest.version
        previous = dict(health.get("tools", {})) if same_version else {}
        for name, status in sticky.items():
            if statuses.get(name) == TOOL_OK:
                statuses[name] = status
        tools = {**previous, **statuses}
        health["manifest_version"] = manifest.version
        if only_tool is None:
            health.update(
                {
                    "checked_at": checked_at or timezone.now().isoformat(),
                    "error": "",
                    "server_name": listing.server_name,
                    "server_version": listing.server_version,
                    "protocol_version": listing.protocol_version,
                }
            )
        health["tools"] = tools
        locked.last_health = health
        locked.save(update_fields=["last_health", "updated_at"])
        for name, status in statuses.items():
            if status in _DISABLED_STATUSES and previous.get(name) != status:
                policy = manifest.get_tool(name)
                logger.warning("connector %s tool %s pin %s", manifest.name, name, status)
                record_audit(
                    action="connector.pin_mismatch",
                    actor=None,
                    actor_type=AuditLog.ActorType.SYSTEM,
                    workspace=locked.workspace,
                    resource=locked,
                    metadata={
                        "connector": manifest.name,
                        "tool": name,
                        "status": status,
                        "expected_sha256": policy.schema_sha256,
                        "actual_sha256": listing.tool_hashes.get(policy.server_tool) or "",
                        "manifest_version": manifest.version,
                    },
                )
    return locked


# Native bridges (brief D8/D9): no server schema to pin -- the bridge's own
# health check decides each tool's availability (e.g. Google's granted OAuth
# scopes). These statuses are NOT sticky: they follow the credential, so a
# re-consent that grants `gmail.send` makes `mail_send` available at the
# next check without a manifest change.
TOOL_SCOPE_MISSING = "scope_missing"
TOOL_SCOPE_EXTRA = "scope_extra"
TOOL_RECONNECT_REQUIRED = "reconnect_required"
# A health error after which nothing stays callable (the credential itself
# is gone); any other error (timeout, transport) keeps the last statuses.
_CREDENTIAL_ERRORS = {TOOL_RECONNECT_REQUIRED, "not_configured"}


def record_native_health(binding, manifest, health, *, checked_at=None):
    """Store a native bridge's health report on the binding; audit every
    tool that newly lost availability to a scope wider than the manifest
    allows (`connector.scope_mismatch`) and a newly rejected credential
    (`connector.reconnect_required`). Never records a credential."""
    with transaction.atomic():
        locked = ConnectorBinding.objects.select_for_update().get(pk=binding.pk)
        last = dict(locked.last_health or {})
        same_version = last.get("manifest_version") == manifest.version
        previous = dict(last.get("tools", {})) if same_version else {}
        if health.error in _CREDENTIAL_ERRORS:
            tools = {policy.name: health.error for policy in manifest.tools}
        elif health.error:
            tools = {
                policy.name: previous.get(policy.name, TOOL_UNCHECKED) for policy in manifest.tools
            }
        else:
            tools = {
                policy.name: health.tools.get(policy.name, TOOL_SCOPE_MISSING)
                for policy in manifest.tools
            }
        locked.last_health = {
            "manifest_version": manifest.version,
            "checked_at": checked_at or timezone.now().isoformat(),
            "error": health.error,
            "server_name": health.server_name,
            "server_version": health.server_version,
            "protocol_version": "",
            "details": dict(health.details),
            "tools": tools,
        }
        locked.save(update_fields=["last_health", "updated_at"])
        newly_extra = sorted(
            name
            for name, status in tools.items()
            if status == TOOL_SCOPE_EXTRA and previous.get(name) != TOOL_SCOPE_EXTRA
        )
        if newly_extra:
            logger.warning("connector %s disabled: grant wider than the manifest", manifest.name)
            record_audit(
                action="connector.scope_mismatch",
                actor=None,
                actor_type=AuditLog.ActorType.SYSTEM,
                workspace=locked.workspace,
                resource=locked,
                metadata={
                    "connector": manifest.name,
                    "tools": newly_extra,
                    "extra_scopes": list(health.details.get("extra_scopes", [])),
                    "manifest_version": manifest.version,
                },
            )
        # A bridge whose own request contract no longer matches the
        # manifest pin (e.g. Home Assistant's allowlist widened without a
        # manifest update) -- the same audit as MCP schema drift.
        hashes = health.details.get("tool_hashes") or {}
        for name, status in sorted(tools.items()):
            if status in _DISABLED_STATUSES and previous.get(name) != status:
                policy = manifest.get_tool(name)
                logger.warning("connector %s tool %s pin %s", manifest.name, name, status)
                record_audit(
                    action="connector.pin_mismatch",
                    actor=None,
                    actor_type=AuditLog.ActorType.SYSTEM,
                    workspace=locked.workspace,
                    resource=locked,
                    metadata={
                        "connector": manifest.name,
                        "tool": name,
                        "status": status,
                        "expected_sha256": policy.schema_sha256,
                        "actual_sha256": hashes.get(policy.server_tool) or "",
                        "manifest_version": manifest.version,
                    },
                )
        if health.error in _CREDENTIAL_ERRORS and last.get("error") != health.error:
            record_audit(
                action="connector.reconnect_required",
                actor=None,
                actor_type=AuditLog.ActorType.SYSTEM,
                workspace=locked.workspace,
                resource=locked,
                metadata={"connector": manifest.name, "error": health.error},
            )
    return locked
