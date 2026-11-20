from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _refresh_self_block(**kwargs):
    # Phase 8A D8: re-assemble the deterministic `self` block on deploy.
    from assistant.self_block import refresh_self_block_safely

    refresh_self_block_safely(**kwargs)


class AssistantConfig(AppConfig):
    name = "assistant"
    verbose_name = "Assistant"

    def ready(self):
        post_migrate.connect(
            _refresh_self_block, sender=self, dispatch_uid="assistant.refresh_self_block"
        )
