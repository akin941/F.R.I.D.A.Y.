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
        # Phase 10 D9: the node action trust check. Branch 3 registers the
        # explicit stub (`warn:trust_check_pending`, no model call); branch 4
        # (`feat/p10-trust-check`) replaces THIS line with the real
        # `assistant.trust_check.check_node_action` and its evidence source.
        # Removing it leaves the port unregistered, which blocks every
        # proposal (`trust_check_unavailable_config`).
        from tools.node_action_ports import pending_trust_check, register_trust_check

        register_trust_check(pending_trust_check)
        post_migrate.connect(
            _refresh_self_block, sender=self, dispatch_uid="assistant.refresh_self_block"
        )
