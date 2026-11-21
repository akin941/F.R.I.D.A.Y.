from django.apps import AppConfig


class NodesConfig(AppConfig):
    """Phase 9 (ADR-013, brief D2): the Friday Node server side -- the node
    row, the capability snapshot, the hash-chained invocation audit, the
    pairing endpoint and (branch 4) the WSS gateway.

    Import contract (ADR-013 decision 9, `backend/pyproject.toml`): `nodes`
    may import `approvals`, `audit`, `workspaces`, `accounts` and `core`
    only -- never `config`, `ai_core` (the node makes no model call),
    `memory` (node events reach memory through a sink the composition root
    injects, D10), `tools` or any other app. Only `tools`, `hud`, `ops` and
    `config` may import `nodes`. `nodes.protocol` and `nodes.capabilities`
    import no Django at all: they are the spec the Swift node mirrors.

    Phase 9 `nodes` imports no `approvals` code either (brief follow-up 2):
    `tools` will import `nodes` (branch 5) and `tools` must not import
    `approvals`, so any such edge would break that contract transitively."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    verbose_name = "Friday Node"
