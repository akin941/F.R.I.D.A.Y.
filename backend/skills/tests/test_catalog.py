import json
from types import MappingProxyType

import pytest

from skills.catalog import (
    SkillSpec,
    SkillStep,
    SkillValidationError,
    get_skill,
    list_skill_names,
    list_skill_prompt_entries,
    register_skill,
    skill_steps_to_plan_dicts,
    unregister_skill,
)


def _spec(name="stub_skill", steps=None):
    return SkillSpec(
        name=name,
        description="A stub skill",
        trigger_hint="e.g. 'do the stub thing'",
        steps=steps
        if steps is not None
        else (SkillStep(kind="read", description="Read something", payload={}),),
    )


class TestRegistry:
    def test_unknown_skill_returns_none(self):
        assert get_skill("does_not_exist") is None

    def test_register_then_get_returns_the_same_spec(self):
        spec = _spec()
        register_skill(spec)
        try:
            assert get_skill("stub_skill") is spec
        finally:
            unregister_skill("stub_skill")

    def test_registering_the_same_name_twice_replaces_it(self):
        first = _spec()
        second = _spec()
        register_skill(first)
        try:
            register_skill(second)
            assert get_skill("stub_skill") is second
        finally:
            unregister_skill("stub_skill")

    def test_unregister_is_idempotent(self):
        unregister_skill("never_registered")  # must not raise


class TestListSkillNames:
    def test_returns_registered_names_sorted(self):
        register_skill(_spec(name="zeta_skill"))
        register_skill(_spec(name="alpha_skill"))
        try:
            names = list_skill_names()
            assert "alpha_skill" in names
            assert "zeta_skill" in names
            assert names.index("alpha_skill") < names.index("zeta_skill")
        finally:
            unregister_skill("zeta_skill")
            unregister_skill("alpha_skill")

    def test_unregistered_name_is_absent(self):
        assert "definitely_not_registered" not in list_skill_names()


class TestListSkillPromptEntries:
    def test_returns_name_description_trigger_hint_tuples_sorted_by_name(self):
        register_skill(_spec(name="zeta_skill"))
        register_skill(_spec(name="alpha_skill"))
        try:
            entries = list_skill_prompt_entries()
            names = [name for name, _, _ in entries]
            assert names.index("alpha_skill") < names.index("zeta_skill")
            entry = next(e for e in entries if e[0] == "alpha_skill")
            assert entry == ("alpha_skill", "A stub skill", "e.g. 'do the stub thing'")
        finally:
            unregister_skill("zeta_skill")
            unregister_skill("alpha_skill")


class TestContentHash:
    def test_stable_across_repeated_construction_of_the_same_spec(self):
        spec_a = _spec()
        spec_b = _spec()
        assert spec_a.content_hash == spec_b.content_hash

    def test_changes_when_name_changes(self):
        spec_a = _spec(name="stub_skill_a")
        spec_b = _spec(name="stub_skill_b")
        assert spec_a.content_hash != spec_b.content_hash

    def test_changes_when_a_step_field_changes(self):
        spec_a = _spec(steps=(SkillStep(kind="read", description="A", payload={}),))
        spec_b = _spec(steps=(SkillStep(kind="read", description="B", payload={}),))
        assert spec_a.content_hash != spec_b.content_hash

    def test_changes_when_step_payload_changes(self):
        spec_a = _spec(
            steps=(
                SkillStep(kind="tool_request", description="Export", payload={"tool_name": "a"}),
            )
        )
        spec_b = _spec(
            steps=(
                SkillStep(kind="tool_request", description="Export", payload={"tool_name": "b"}),
            )
        )
        assert spec_a.content_hash != spec_b.content_hash

    def test_content_hash_cannot_be_supplied_by_the_caller(self):
        with pytest.raises(TypeError):
            SkillSpec(
                name="x",
                description="x",
                trigger_hint="x",
                steps=(),
                content_hash="not-allowed",
            )


class TestImmutability:
    def test_step_payload_is_read_only(self):
        step = SkillStep(kind="read", description="Read", payload={"a": 1})
        assert isinstance(step.payload, MappingProxyType)
        with pytest.raises(TypeError):
            step.payload["a"] = 2

    def test_spec_steps_is_normalized_to_a_tuple_even_if_a_list_is_passed(self):
        steps_list = [SkillStep(kind="read", description="Read", payload={})]
        spec = SkillSpec(name="x", description="x", trigger_hint="x", steps=steps_list)
        assert isinstance(spec.steps, tuple)

    def test_mutating_the_original_list_after_construction_does_not_affect_the_hash(self):
        steps_list = [SkillStep(kind="read", description="Read", payload={})]
        spec = SkillSpec(name="x", description="x", trigger_hint="x", steps=steps_list)
        original_hash = spec.content_hash
        steps_list.append(SkillStep(kind="read", description="Another", payload={}))
        assert spec.content_hash == original_hash
        assert len(spec.steps) == 1


class TestStepShapeValidationAtRegistration:
    def test_valid_shapes_register_cleanly(self):
        spec = _spec(
            steps=(
                SkillStep(kind="read", description="Read", payload={}),
                SkillStep(
                    kind="tool_request",
                    description="Export",
                    payload={"tool_name": "vault_export"},
                ),
                SkillStep(
                    kind="task_draft",
                    description="Draft",
                    payload={"title": "Follow up", "due_date": None},
                ),
            )
        )
        register_skill(spec)
        try:
            assert get_skill("stub_skill") is spec
        finally:
            unregister_skill("stub_skill")

    def test_invalid_payload_shape_fails_at_registration_not_construction(self):
        # A dict-shaped-but-wrong-keys payload passes SkillStep's own
        # basic dict-type check at construction (that's the registry's
        # job, at register_skill time, via _validate_step_shape) --
        # constructing it must not raise.
        step = SkillStep(kind="read", description="Read", payload={"unexpected": 1})
        spec = _spec(steps=(step,))
        with pytest.raises(SkillValidationError):
            register_skill(spec)
        # And it must not have been registered.
        assert get_skill("stub_skill") is None

    def test_unrecognized_kind_fails_at_registration(self):
        step = SkillStep(kind="bogus", description="Nope", payload={})
        spec = _spec(steps=(step,))
        with pytest.raises(SkillValidationError):
            register_skill(spec)

    def test_non_dict_payload_fails_at_construction_not_registration(self):
        # Codex round-2 P2 fix: a non-dict iterable (e.g. a list, or a list
        # of pairs) would otherwise be silently coerced into a dict by
        # `dict(...)` before any shape check ever ran -- accepting shapes
        # `agents.planner._parse_step` rejects outright. This must fail as
        # soon as the type is wrong, not later.
        with pytest.raises(SkillValidationError):
            SkillStep(kind="read", description="Read", payload=[])

    def test_non_dict_payload_that_would_coerce_to_a_valid_shape_still_rejected(self):
        with pytest.raises(SkillValidationError):
            SkillStep(
                kind="tool_request",
                description="Export",
                payload=[("tool_name", "vault_export")],
            )


class TestSkillStepsToPlanDicts:
    """Codex round-1 P2 fix: `SkillStep.payload` is a `MappingProxyType`
    (immutable by design, see `SkillStep.__post_init__`), which is not
    JSON-serializable -- branch 2's `finalize_plan()` call needs a plain,
    JSON-safe dict shape instead. This is the boundary that conversion must
    happen at, exercised here rather than left implicit."""

    def test_produces_the_same_shape_parse_plan_expects_and_is_json_safe(self):
        spec = _spec(
            steps=(
                SkillStep(kind="read", description="Read", payload={}),
                SkillStep(
                    kind="tool_request",
                    description="Export",
                    payload={"tool_name": "vault_export"},
                ),
            )
        )
        plan_dicts = skill_steps_to_plan_dicts(spec)
        assert plan_dicts == [
            {"kind": "read", "description": "Read", "payload": {}},
            {
                "kind": "tool_request",
                "description": "Export",
                "payload": {"tool_name": "vault_export"},
            },
        ]
        json.dumps(plan_dicts)  # must not raise
        assert all(type(d["payload"]) is dict for d in plan_dicts)
