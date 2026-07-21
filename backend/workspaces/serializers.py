from rest_framework import serializers

from workspaces.models import Workspace

_MAX_LIST_ITEMS = 50
_MAX_NOTES_CHARS = 10_000
_MAX_NAME_CHARS = 255
_MAX_URL_CHARS = 2_000


class WorkspaceSerializer(serializers.ModelSerializer):
    role = serializers.CharField(read_only=True)

    class Meta:
        model = Workspace
        fields = ["id", "name", "type", "enabled_modules", "role", "created_at"]
        read_only_fields = fields


class WorkspaceDetailSerializer(serializers.ModelSerializer):
    """Retrieve-only view of a workspace, including the firm `profile`
    dossier. Every field is read-only here -- `profile` becomes writable
    only through `WorkspaceProfileUpdateSerializer` on the PATCH endpoint,
    never through this serializer (mirrors the `TaskSerializer` /
    `TaskEditSerializer` split in `tasks.serializers`)."""

    role = serializers.CharField(read_only=True)

    class Meta:
        model = Workspace
        fields = ["id", "name", "type", "enabled_modules", "profile", "role", "created_at"]
        read_only_fields = fields


class _StrictCharField(serializers.CharField):
    """`serializers.CharField` silently coerces ints/floats to strings
    (`str(1)` -> `"1"`). The firm-profile schema is a validated shape, not a
    free-form blob (brief §2) -- a non-string value in a string slot must be
    a clear 400, not a silent coercion."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class _StrictObjectSerializer(serializers.Serializer):
    """Base for the profile's list-entry shapes: rejects a non-object entry
    and any key outside the declared field set (no blob-store escape
    hatch)."""

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Must be an object.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unknown field." for key in sorted(unknown)})
        return super().to_internal_value(data)


class _RepoEntrySerializer(_StrictObjectSerializer):
    name = _StrictCharField(max_length=_MAX_NAME_CHARS)
    url = _StrictCharField(max_length=_MAX_URL_CHARS)
    default_branch = _StrictCharField(max_length=_MAX_NAME_CHARS, required=False, allow_blank=True)


class _ServiceEntrySerializer(_StrictObjectSerializer):
    name = _StrictCharField(max_length=_MAX_NAME_CHARS)
    kind = _StrictCharField(max_length=_MAX_NAME_CHARS, required=False, allow_blank=True)
    url = _StrictCharField(max_length=_MAX_URL_CHARS, required=False, allow_blank=True)


class _DeployTargetEntrySerializer(_StrictObjectSerializer):
    # Metadata only. Deploy credentials/secrets must NEVER be stored here --
    # secrets stay in env per CLAUDE.md ("Secrets only via env").
    name = _StrictCharField(max_length=_MAX_NAME_CHARS)
    host = _StrictCharField(max_length=_MAX_NAME_CHARS, required=False, allow_blank=True)
    notes = _StrictCharField(max_length=_MAX_NOTES_CHARS, required=False, allow_blank=True)


class WorkspaceProfileSerializer(_StrictObjectSerializer):
    """Schema for `Workspace.profile` (brief §2): a validated dossier shape,
    not free-form JSON. Unknown top-level keys, wrong types, and oversized
    lists/strings are all rejected with clear serializer errors."""

    repos = _RepoEntrySerializer(many=True, required=False)
    services = _ServiceEntrySerializer(many=True, required=False)
    deploy_targets = _DeployTargetEntrySerializer(many=True, required=False)
    notes = _StrictCharField(max_length=_MAX_NOTES_CHARS, required=False, allow_blank=True)

    def validate_repos(self, value):
        return _bounded_list(value, "repos")

    def validate_services(self, value):
        return _bounded_list(value, "services")

    def validate_deploy_targets(self, value):
        return _bounded_list(value, "deploy_targets")


def _bounded_list(value, field_name):
    if len(value) > _MAX_LIST_ITEMS:
        raise serializers.ValidationError(f"{field_name} cannot exceed {_MAX_LIST_ITEMS} items.")
    return value


class WorkspaceProfileUpdateSerializer(serializers.Serializer):
    """PATCH body for the profile-only update endpoint. `profile` is the
    sole writable field on `Workspace` -- any other top-level key in the
    request body is silently ignored (this serializer simply has no field
    for it), mirroring `tasks.serializers.TaskEditSerializer`. `profile`
    fully replaces the stored dossier -- it is not deep-merged with the
    existing value."""

    profile = WorkspaceProfileSerializer()
