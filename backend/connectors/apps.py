from django.apps import AppConfig


class ConnectorsConfig(AppConfig):
    """Outside-world bridges (ADR-010): manifested MCP servers and native
    bridges (Home Assistant, Google), their per-workspace bindings, fenced
    reads and gated, snapshot-backed writes. `integrations` stays the
    channels app (CLI, Telegram, facade).

    At startup every committed manifest is registered, and each active
    manifest's reads become `tools.registry.ReadToolSpec`s and its writes
    gated `ToolSpec`s -- the only way `assistant` and `approvals` ever reach
    a connector (they never import this app)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "connectors"

    def ready(self):
        from connectors.manifest import list_manifests
        from connectors.manifests import load_builtin
        from connectors.probes import register as register_probes
        from connectors.reads import register_reads
        from connectors.writes import register_writes

        load_builtin()
        for manifest in list_manifests():
            register_reads(manifest)
            register_writes(manifest)
        # Phase 12 D1/D12: finished n8n writes as notices (core.probes).
        register_probes()
        # Phase 13 D13: the `home.control` resolver and the `ha_call_service`
        # draft check, reached by `assistant` through `tools.home_ports`.
        from connectors.home_phrases import register as register_home_ports

        register_home_ports()
        # Phase 13b (ADR-018): the direct-media classifier (branch 1b) and
        # executor (branch 1c) ports.
        from connectors.home_direct import register as register_home_direct

        register_home_direct()
