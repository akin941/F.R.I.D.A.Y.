from django.apps import AppConfig


class HudConfig(AppConfig):
    """Phase 6 branch 5 (brief A8, "5. `feat/p6-hud`"): the HUD's read-only
    event stream -- approval, task, cost and health deltas for the owner's
    full-screen `/hud` route. A leaf kernel app: it may import `core`,
    `workspaces`, `ai_core` (ModelUsage, provider and budget reads only --
    never a gateway call), `approvals` and `tasks`; it writes nothing, has
    no models, and nothing imports it (import-linter contracts in
    `pyproject.toml`, ADR-002 kernel-list note). It is not part of `voice`,
    which may not import `approvals` or `tasks` (D11).

    Phase 10 branch 5 (brief D12) adds the `terminal` section and the
    terminal output stream; for those `hud` reads `nodes` (its Phase 9 D2
    edge: execution rows, contract labels and the Redis output reader)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "hud"
