from django.apps import AppConfig


class ToolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tools"

    def ready(self):
        # Registration happens once, at process startup, after every app's
        # models have loaded -- `tools.export` imports `memory.models`.
        from tools.coding_sessions import _register as _register_sessions_read
        from tools.export import _register
        from tools.memory_graph import _register as _register_graph_read
        from tools.node_action_reads import _register as _register_node_action_read
        from tools.node_actions import _register as _register_node_action
        from tools.purge import _register as _register_purge

        _register()
        _register_purge()
        # Phase 8A D12: the ungated, web-only coding-sessions read tool.
        _register_sessions_read()
        # Phase 8B D8: the ungated, web-only temporal-graph read tool.
        _register_graph_read()
        # Phase 10 D6: the gated `node_action` tool (and its node ports).
        _register_node_action()
        # Phase 10 D12: the ungated, web-only node action result read tool.
        _register_node_action_read()
