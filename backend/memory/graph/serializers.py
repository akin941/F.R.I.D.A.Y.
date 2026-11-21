"""Request validation and response shapes of the owner graph API."""

from rest_framework import serializers

from memory.models import LEARNED_ENTITY_KINDS, LEARNED_PREDICATES, MemoryEntity, MemoryFact

_LEARNED_KINDS = sorted(LEARNED_ENTITY_KINDS)
_LEARNED_PREDICATES = sorted(LEARNED_PREDICATES)


class WorkspaceQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()


class EntityListQuerySerializer(WorkspaceQuerySerializer):
    kind = serializers.ChoiceField(choices=MemoryEntity.Kind.choices, required=False)
    q = serializers.CharField(required=False, max_length=MemoryEntity.MAX_NAME_CHARS)


class NeighbourhoodQuerySerializer(serializers.Serializer):
    hops = serializers.IntegerField(required=False, min_value=1, max_value=2, default=1)
    as_of = serializers.DateTimeField(required=False)


class AsOfQuerySerializer(serializers.Serializer):
    as_of = serializers.DateTimeField(required=False)


class FactListQuerySerializer(WorkspaceQuerySerializer):
    status = serializers.ChoiceField(choices=MemoryFact.Status.choices, required=False)
    as_of = serializers.DateTimeField(required=False)
    current = serializers.BooleanField(required=False, default=False)


class _ObjectFields(serializers.Serializer):
    object_kind = serializers.ChoiceField(choices=_LEARNED_KINDS, required=False)
    object_name = serializers.CharField(
        required=False, max_length=MemoryEntity.MAX_NAME_CHARS, trim_whitespace=True
    )
    object_text = serializers.CharField(
        required=False, max_length=MemoryFact.MAX_OBJECT_TEXT_CHARS, trim_whitespace=True
    )
    statement = serializers.CharField(
        required=False, max_length=MemoryFact.MAX_STATEMENT_CHARS, trim_whitespace=True
    )

    def validate(self, attrs):
        has_entity = bool(attrs.get("object_kind") or attrs.get("object_name"))
        has_text = bool(attrs.get("object_text"))
        if has_entity and has_text:
            raise serializers.ValidationError("Name an object entity or an object text, not both.")
        if has_entity and not (attrs.get("object_kind") and attrs.get("object_name")):
            raise serializers.ValidationError("An object entity needs a kind and a name.")
        return attrs


class FactCreateSerializer(_ObjectFields):
    workspace = serializers.UUIDField()
    subject_kind = serializers.ChoiceField(choices=_LEARNED_KINDS)
    subject_name = serializers.CharField(max_length=MemoryEntity.MAX_NAME_CHARS)
    predicate = serializers.ChoiceField(choices=_LEARNED_PREDICATES)
    valid_from = serializers.DateTimeField(required=False)
    sensitivity = serializers.ChoiceField(
        choices=MemoryFact.Sensitivity.choices, required=False, default="normal"
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not (attrs.get("object_text") or attrs.get("object_kind")):
            raise serializers.ValidationError("A fact names exactly one object.")
        return attrs


class FactEditSerializer(_ObjectFields):
    valid_from = serializers.DateTimeField(required=False)
    sensitivity = serializers.ChoiceField(choices=MemoryFact.Sensitivity.choices, required=False)


class FactSupersedeSerializer(_ObjectFields):
    valid_from = serializers.DateTimeField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not (attrs.get("object_text") or attrs.get("object_kind")):
            raise serializers.ValidationError("The new version names exactly one object.")
        return attrs


def _entity_ref(entity):
    if entity is None:
        return None
    return {"id": str(entity.id), "kind": entity.kind, "name": entity.name}


def serialize_entity(entity, *, is_private=None):
    if is_private is None:
        is_private = getattr(entity, "is_private", None)
    return {
        "id": str(entity.id),
        "workspace": str(entity.workspace_id),
        "kind": entity.kind,
        "name": entity.name,
        "aliases": list(entity.aliases or []),
        "origin": entity.origin,
        "is_private": is_private,
        "created_at": entity.created_at.isoformat(),
        "updated_at": entity.updated_at.isoformat(),
    }


def _iso(value):
    return value.isoformat() if value else None


def serialize_fact(fact):
    data = {
        "id": str(fact.id),
        "workspace": str(fact.workspace_id),
        "subject": _entity_ref(fact.subject),
        "predicate": fact.predicate,
        "object_entity": _entity_ref(fact.object_entity),
        "object_text": fact.object_text,
        "statement": fact.statement,
        "valid_from": _iso(fact.valid_from),
        "valid_to": _iso(fact.valid_to),
        "superseded_by": str(fact.superseded_by_id) if fact.superseded_by_id else None,
        "supersedes": str(fact.supersedes_id) if fact.supersedes_id else None,
        "confidence": fact.confidence,
        "status": fact.status,
        "origin": fact.origin,
        "sensitivity": fact.sensitivity,
        "provenance_quote": fact.provenance_quote,
        "source_episode_id": str(fact.source_episode_id) if fact.source_episode_id else None,
        "ingestion_source": str(fact.ingestion_source_id) if fact.ingestion_source_id else None,
        "in_trash": fact.deleted_at is not None,
        "created_at": fact.created_at.isoformat(),
        "updated_at": fact.updated_at.isoformat(),
    }
    hop = getattr(fact, "hop", None)
    if hop is not None:
        data["hop"] = hop
    return data
