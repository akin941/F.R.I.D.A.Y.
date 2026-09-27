from django.apps import AppConfig


class HarnessConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "harness"

    def ready(self):
        import harness.checks  # noqa: F401 -- registers the agent-policy system check
        import harness.signals  # noqa: F401 -- connects the gate-decision receiver
        from connectors.github import register_repo_resolver
        from harness.pull_requests import resolve_repo_scope
        from harness.tools import _register

        _register()
        # The GitHub scope check (connectors may not import harness):
        # `owner/repo` must canonicalize to a HarnessRepo of the workspace.
        register_repo_resolver(resolve_repo_scope)
