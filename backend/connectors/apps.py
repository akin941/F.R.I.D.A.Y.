from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    """Outside-world bridges (ADR-010): manifested MCP servers and native
    bridges (Home Assistant, Google), their per-workspace bindings, fenced
    reads and gated, snapshot-backed writes. Registered empty in Phase 5
    branch 1 so its import-linter contracts bite before any code lands;
    the framework itself arrives in branch 4. `integrations` stays the
    channels app (CLI, Telegram, facade)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "connectors"
