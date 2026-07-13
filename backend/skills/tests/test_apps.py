"""Boot-time tool-reference validation (MVP 8 §2, brief) -- mirrors how
`ApprovalsConfig.ready()`'s `DEV_ALLOW_SELF_APPROVAL` fail-fast test is
structured (`approvals/tests/test_apps.py`): call the already-loaded app
config's validation method directly rather than re-running all of Django
app loading.
"""

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from skills.catalog import SkillSpec, SkillStep, register_skill, unregister_skill


def _skills_app_config():
    return apps.get_app_config("skills")


def test_validate_tool_references_is_fine_with_real_registered_skills():
    _skills_app_config()._validate_tool_references()  # must not raise


def test_validate_tool_references_raises_for_a_dangling_tool_name():
    bogus = SkillSpec(
        name="bogus_skill",
        description="References a tool that doesn't exist",
        trigger_hint="never matched",
        steps=(
            SkillStep(
                kind="tool_request",
                description="Do the thing",
                payload={"tool_name": "definitely_not_a_real_tool"},
            ),
        ),
    )
    register_skill(bogus)
    try:
        with pytest.raises(ImproperlyConfigured):
            _skills_app_config()._validate_tool_references()
    finally:
        unregister_skill("bogus_skill")
