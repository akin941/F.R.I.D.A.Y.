from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class SkillsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "skills"

    def ready(self):
        # Registration happens once, at process startup, mirroring
        # tools/apps.py's convention.
        from skills.catalog import register_skill
        from skills.definitions.note_to_task import SPEC as NOTE_TO_TASK
        from skills.definitions.weekly_export import SPEC as WEEKLY_EXPORT

        register_skill(WEEKLY_EXPORT)
        register_skill(NOTE_TO_TASK)

        self._validate_tool_references()

    def _validate_tool_references(self):
        """Boot-time tool-reference validation (MVP 8 §2, brief): a skill
        can be renamed, removed, or have its expected payload shape changed
        by a later MVP without anyone remembering a skill still names it --
        without this check, that skill breaks silently and only surfaces
        when an agent actually tries to run it, buried inside a running
        agent's step instead of at deploy time. Fail loud at startup
        instead -- the same fail-fast-at-boot convention ApprovalsConfig.
        ready() established with its (since-removed, ADR-008) DEV_ALLOW_
        SELF_APPROVAL guard.

        ORDERING CONSTRAINT: this only sees every registered tool if
        ToolsConfig.ready() has already run (Django calls ready() in
        INSTALLED_APPS order) -- "skills" MUST be listed after "tools" in
        INSTALLED_APPS. Re-ordering that would silently turn this into a
        false negative: skills validated before tools exist, so nothing
        ever fails even though a reference actually dangles.
        """
        from skills.catalog import get_skill, list_skill_names
        from tools.registry import list_tool_names

        known_tools = set(list_tool_names())
        for name in list_skill_names():
            spec = get_skill(name)
            for step in spec.steps:
                if step.kind != "tool_request":
                    continue
                tool_name = step.payload["tool_name"]
                if tool_name not in known_tools:
                    raise ImproperlyConfigured(
                        f"skill {spec.name!r} references unknown tool "
                        f"{tool_name!r} -- check INSTALLED_APPS ordering "
                        f"('skills' must come after 'tools') or fix the "
                        f"skill definition."
                    )
