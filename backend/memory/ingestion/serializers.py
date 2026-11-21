"""Owner API shapes for ingestion (Phase 8A branch 1). Episode text shown
here is the stored, already-scrubbed text; nothing raw exists to show."""

from rest_framework import serializers

from memory.ingestion.mapping import SHARED_RULE_SOURCES, allowed_rule_types
from memory.ingestion.services import SHARED_RULES_MESSAGE
from memory.models import (
    Episode,
    IngestionCounter,
    ScrubTerm,
    WorkspaceMappingRule,
)


def serialize_source(source, today=None):
    return {
        "id": str(source.id),
        "workspace": str(source.workspace_id),
        "kind": source.kind,
        "enabled": source.enabled,
        "paused": source.paused_at is not None,
        "paused_at": source.paused_at.isoformat() if source.paused_at else None,
        "daily_token_budget": source.daily_token_budget,
        "forgotten_at": source.forgotten_at.isoformat() if source.forgotten_at else None,
        "restorable": source.forget_operation_id is not None,
        "enabled_at": source.enabled_at.isoformat() if source.enabled_at else None,
        # Phase 8B branch 8 (D13, Q2): the transcript sources route through
        # the hooks source's path rules; the audit view says so.
        "rules_shared_with": SHARED_RULE_SOURCES.get(source.kind),
        "today": serialize_counter(today) if today else None,
    }


def serialize_counter(counter: IngestionCounter):
    return {
        "day": counter.day.isoformat(),
        "stored": counter.stored,
        "duplicate": counter.duplicate,
        "dropped_unmapped": counter.dropped_unmapped,
        "dropped_paused": counter.dropped_paused,
        "dropped_not_owner": counter.dropped_not_owner,
        "dropped_foreign": counter.dropped_foreign,
        "scrubber_hits": counter.scrubber_hits,
        "tokens_spent": counter.tokens_spent,
        "proposals_created": counter.proposals_created,
        "proposals_accepted": counter.proposals_accepted,
        "proposals_rejected": counter.proposals_rejected,
    }


def serialize_episode(episode: Episode):
    return {
        "id": str(episode.id),
        "workspace": str(episode.workspace_id),
        "workspace_name": episode.workspace.name,
        "kind": episode.kind,
        "occurred_at": episode.occurred_at.isoformat(),
        "owner_text": episode.owner_text,
        "context_text": episode.context_text,
        "metadata": episode.metadata,
        "sensitivity": episode.sensitivity,
        "scrub_report": episode.scrub_report,
        "token_estimate": episode.token_estimate,
        "expires_at": episode.expires_at.isoformat(),
        "extracted": episode.extracted_at is not None,
    }


def serialize_rule(rule: WorkspaceMappingRule):
    return {
        "id": str(rule.id),
        "source": str(rule.source_id),
        "rule_type": rule.rule_type,
        "pattern": rule.pattern,
        "target_workspace": str(rule.target_workspace_id),
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
    }


def serialize_term(term: ScrubTerm):
    return {"id": str(term.id), "workspace": str(term.workspace_id), "term": term.term}


def serialize_audit(view):
    return {
        **serialize_source(view["source"]),
        "counters": [serialize_counter(c) for c in view["counters"]],
        "episodes": [serialize_episode(e) for e in view["episodes"]],
        "scrubber_hits": view["scrubber_hits"],
        "unmapped_keys": view["unmapped_keys"],
        "tokens_spent_today": view["tokens_spent_today"],
        "acceptance": view["acceptance"],
        "forget_preview": view["forget_preview"],
        "transcript_cursors": view["transcript_cursors"],
    }


class WorkspaceQuerySerializer(serializers.Serializer):
    workspace = serializers.UUIDField()


class SourceUpdateSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    paused = serializers.BooleanField(required=False)
    daily_token_budget = serializers.IntegerField(required=False, min_value=0, max_value=10_000_000)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to change.")
        return attrs


class RuleCreateSerializer(serializers.Serializer):
    """`context["source"]` (the rule's source) narrows the rule types it
    accepts (`allowed_rule_types`); the service re-checks."""

    rule_type = serializers.ChoiceField(choices=WorkspaceMappingRule.RuleType.choices)
    pattern = serializers.CharField(max_length=500)
    target_workspace = serializers.UUIDField()

    def validate_rule_type(self, value):
        source = self.context.get("source")
        if source is not None and source.kind in SHARED_RULE_SOURCES:
            raise serializers.ValidationError(SHARED_RULES_MESSAGE)
        if source is not None and value not in allowed_rule_types(source.kind):
            raise serializers.ValidationError(
                f"A {value} rule is not valid on a {source.kind} source."
            )
        return value


class RuleUpdateSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    target_workspace = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to change.")
        return attrs


class ScrubTermCreateSerializer(serializers.Serializer):
    workspace = serializers.UUIDField()
    term = serializers.CharField(max_length=200)


class SessionQuerySerializer(serializers.Serializer):
    """`GET /memory/sessions` filters: an optional workspace (else every
    workspace the owner is a member of) and a bounded page size."""

    workspace = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=50)


def serialize_coding_session(session, *, status):
    """A `CodingSession` row for the sessions panel (D12). Every text field
    is the stored, already-scrubbed value; `last_notification` is tool/model
    text and must be fenced as untrusted wherever a model sees it."""
    return {
        "id": str(session.id),
        "workspace": {"id": str(session.workspace_id), "name": session.workspace.name},
        "tool": session.tool,
        "session_id": session.session_id,
        "project": session.project,
        "status": status,
        "stored_status": session.status,
        "started_at": session.started_at.isoformat(),
        "last_event_at": session.last_event_at.isoformat(),
        "last_event": session.last_event,
        "last_notification": session.last_notification,
        "event_count": session.event_count,
    }
