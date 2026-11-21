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
        # Phase 10 D9: the node action trust check and its evidence source.
        # Branch 4 (`feat/p10-trust-check`) replaced branch 3's stub line
        # with the real check. Removing the registration leaves the port
        # unregistered, which blocks every proposal
        # (`trust_check_unavailable_config`); that default never changes.
        from assistant.trust_check import check_node_action, input_mode_of
        from tools.node_action_ports import register_evidence_source, register_trust_check

        register_trust_check(check_node_action)
        register_evidence_source(input_mode_of)
        post_migrate.connect(
            _refresh_self_block, sender=self, dispatch_uid="assistant.refresh_self_block"
        )
