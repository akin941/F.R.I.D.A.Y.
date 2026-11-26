from rest_framework import serializers

# Transport bounds only; the service applies the real label rules
# (`workspaces.rooms.clean_label`) and the code maximum.
_LABEL_TRANSPORT_MAX = 200


class HomeEntityQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()


class HomeEntityWriteSerializer(serializers.Serializer):
    """`POST` (workspace, entity_id, label, services required) and `PATCH`
    (partial; `workspace` and `entity_id` not accepted) body of an
    allowlisted Home Assistant entity (Phase 13 D11). Unknown keys are
    refused."""

    workspace = serializers.UUIDField()
    entity_id = serializers.CharField(max_length=141, trim_whitespace=False)
    label = serializers.CharField(
        max_length=_LABEL_TRANSPORT_MAX, trim_whitespace=False, allow_blank=True
    )
    room_id = serializers.UUIDField(allow_null=True, required=False)
    aliases = serializers.ListField(
        child=serializers.CharField(
            max_length=_LABEL_TRANSPORT_MAX, trim_whitespace=False, allow_blank=True
        ),
        max_length=20,
        required=False,
    )
    services = serializers.ListField(
        child=serializers.CharField(max_length=40), max_length=40, allow_empty=True
    )
    enabled = serializers.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.partial:
            # The entity id never changes and the row names its workspace.
            self.fields.pop("workspace")
            self.fields.pop("entity_id")

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({key: ["Unknown field."] for key in unknown})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if self.partial and not attrs:
            raise serializers.ValidationError("Provide at least one field to change.")
        return attrs


class HomeEntitySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    entity_id = serializers.CharField(read_only=True)
    domain = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    key = serializers.CharField(read_only=True)
    aliases = serializers.ListField(child=serializers.CharField(), read_only=True)
    services = serializers.ListField(child=serializers.CharField(), read_only=True)
    enabled = serializers.BooleanField(read_only=True)
    room = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_room(self, entity):
        if entity.room_id is None:
            return None
        return {"id": str(entity.room_id), "name": entity.room.name, "key": entity.room.key}


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
