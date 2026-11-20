"""Helpers for the Phase 8A ingestion tests (fixtures live in conftest)."""

from datetime import UTC, datetime

from memory.models import WorkspaceMappingRule

PASSWORD = "Str0ngDevPass!42"
WHEN = datetime(2026, 11, 19, 9, 30, tzinfo=UTC)


def add_rule(source, rule_type, pattern, target, enabled=True):
    return WorkspaceMappingRule.objects.create(
        workspace=source.workspace,
        source=source,
        rule_type=rule_type,
        pattern=pattern,
        target_workspace=target,
        enabled=enabled,
    )
