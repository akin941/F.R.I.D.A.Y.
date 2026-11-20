from django.apps import AppConfig


class ToolsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tools"

    def ready(self):
        # Registration happens once, at process startup, after every app's
        # models have loaded -- `tools.export` imports `memory.models`.
        from tools.coding_sessions import _register as _register_sessions_read
        from tools.export import _register
        from tools.purge import _register as _register_purge

        _register()
        _register_purge()
        # Phase 8A D12: the ungated, web-only coding-sessions read tool.
        _register_sessions_read()
