from rest_framework import serializers


class BindingQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()


class RecentReadsQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    connector = serializers.RegexField(r"^[a-z][a-z0-9_]{0,63}$", required=False)


class BindingUpdateSerializer(serializers.Serializer):
    """`PUT /connectors/bindings/<connector>`: the only writable field.
    `workspace` names the binding's workspace (resolved through
    `for_actor()`, never trusted raw)."""

    workspace = serializers.UUIDField()
    enabled = serializers.BooleanField()


def serialize_binding(manifest, binding, workspace):
    """Manifest facts (code) + binding state (row). Pin statuses and server
    identity are Friday bookkeeping; no server description, URL or
    credential is ever part of this."""
    health = (binding.last_health if binding else {}) or {}
    return {
        "connector": manifest.name,
        "display_name": manifest.display_name,
        "kind": manifest.kind,
        "status": manifest.status,
        "data_class": manifest.data_class,
        "manifest_version": manifest.version,
        "bindable": not manifest.blocked and workspace.type in manifest.workspace_types,
        "workspace": str(workspace.id),
        "enabled": bool(binding and binding.enabled),
        "tools": [
            {
                "name": policy.name,
                "kind": policy.kind,
                "description": policy.description,
                "pin_status": (health.get("tools") or {}).get(policy.name, "unchecked"),
            }
            for policy in manifest.tools
        ],
        "health": {
            "checked_at": health.get("checked_at"),
            "error": health.get("error", ""),
            "server_name": health.get("server_name", ""),
            "server_version": health.get("server_version", ""),
            "protocol_version": health.get("protocol_version", ""),
        },
        "updated_at": binding.updated_at.isoformat() if binding else None,
    }
