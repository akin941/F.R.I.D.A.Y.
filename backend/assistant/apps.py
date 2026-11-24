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

        # Phase 12 D8: the wake-route table is validated at boot -- an
        # effect other than read/proposal, a malformed name or a phrase
        # clash refuses to start.
        from assistant.wake_routes import validate_routes
        from tools.node_action_ports import register_evidence_source, register_trust_check

        validate_routes()
        register_trust_check(check_node_action)
        register_evidence_source(input_mode_of)

        # Phase 12 D11: async read follow-ups wake on the read's completion
        # (`tools.registry`; assistant never imports connectors), and the
        # `assistant.followups_ready` probe announces them (core.probes).
        from assistant.followups import on_async_read_finished
        from assistant.probes import register as register_probes
        from tools.registry import register_async_read_listener

        register_async_read_listener(on_async_read_finished)
        register_probes()
        post_migrate.connect(
            _refresh_self_block, sender=self, dispatch_uid="assistant.refresh_self_block"
        )
