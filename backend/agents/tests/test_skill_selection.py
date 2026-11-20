"""`purpose="agent.skill_match"` orchestration tests (MVP 8 §3, brief) --
every degrade path (missing config, `GatewayError`, parse failure,
hallucinated/unregistered name) proves `agent.plan` still runs normally
afterward; the match-and-skip path proves `agent.plan`'s own `ModelUsage`
row is genuinely never written, not just its result ignored.
"""

import pytest
from django.test import override_settings

from accounts.services import register_user
from agents.models import Agent, AgentStep
from agents.planner_service import create_agent_from_goal
from agents.skill_match import SkillMatchParseError
from ai_core.adapters.base import AdapterError
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion
from skills.catalog import (
    SkillSpec,
    SkillStep,
    get_skill,
    register_skill,
    skill_steps_to_plan_dicts,
    unregister_skill,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return settings


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def fake_provider():
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
    )


@pytest.fixture
def agent_plan_prompt():
    template = PromptTemplate.objects.create(name="friday_agent_plan", purpose="agent.plan")
    version = PromptVersion.objects.create(template=template, version=1, content="Plan the goal.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


@pytest.fixture
def skill_match_prompt():
    template = PromptTemplate.objects.create(
        name="friday_agent_skill_match", purpose="agent.skill_match"
    )
    version = PromptVersion.objects.create(template=template, version=1, content="Match a skill.")
    template.active_version = version
    template.save(update_fields=["active_version"])
    return version


class TestSkillMatchAndSkip:
    def test_content_trigger_matches_skill_and_skips_agent_plan(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt
    ):
        agent = create_agent_from_goal(
            workspace=workspace, actor=owner, goal="please export my weekly notes for me"
        )

        assert agent.status == Agent.Status.RUNNING
        assert agent.skill_name == "weekly_export"
        spec = get_skill("weekly_export")
        assert agent.skill_content_hash == spec.content_hash

        steps = list(agent.steps.order_by("order"))
        expected = skill_steps_to_plan_dicts(spec)
        assert [(s.kind, s.description, s.payload) for s in steps] == [
            (d["kind"], d["description"], d["payload"]) for d in expected
        ]

        # The actual efficiency gain: agent.plan was never called at all.
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 0
        skill_match_usage = ModelUsage.objects.get(purpose="agent.skill_match")
        assert skill_match_usage.success


class TestNonMatchRegression:
    def test_unrelated_goal_falls_through_to_ordinary_agent_plan(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt
    ):
        """Proves zero regression for every agent created before this MVP
        shipped -- a goal that doesn't match any skill still produces an
        ordinary freeform plan identical to MVP 7's existing behavior."""
        agent = create_agent_from_goal(
            workspace=workspace, actor=owner, goal="do something entirely unrelated"
        )

        assert agent.status == Agent.Status.RUNNING
        assert agent.skill_name == ""
        assert agent.skill_content_hash == ""
        skill_match_usage = ModelUsage.objects.get(purpose="agent.skill_match")
        assert skill_match_usage.success
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1
        steps = list(agent.steps.order_by("order"))
        assert len(steps) == 1
        assert steps[0].kind == AgentStep.Kind.READ


class TestSkillMatchDegradePaths:
    def test_missing_skill_match_config_writes_no_modelusage_row_and_falls_through(
        self, workspace, owner, fake_provider, agent_plan_prompt
    ):
        """No `friday_agent_skill_match` PromptTemplate/active version at
        all -- resolution fails before any gateway call is attempted, so no
        `ModelUsage` row for `purpose="agent.skill_match"` is written,
        unlike a missing `agent.plan` config, which must still fail today
        exactly as it did before this MVP (proven elsewhere)."""
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.RUNNING
        assert ModelUsage.objects.filter(purpose="agent.skill_match").count() == 0
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1
        assert agent.skill_name == ""

    def test_gateway_error_on_skill_match_records_failure_and_falls_through(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt, monkeypatch
    ):
        original_complete = FakeAdapter.complete

        def _flaky_complete(self, messages, params, *, purpose=""):
            if purpose == "agent.skill_match":
                raise AdapterError("provider_error", retryable=False)
            return original_complete(self, messages, params, purpose=purpose)

        monkeypatch.setattr(FakeAdapter, "complete", _flaky_complete)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.RUNNING
        skill_match_usage = ModelUsage.objects.get(purpose="agent.skill_match")
        assert not skill_match_usage.success
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1
        assert ModelUsage.objects.get(purpose="agent.plan").success

    def test_parse_failure_on_skill_match_still_writes_success_row_and_falls_through(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt, monkeypatch
    ):
        """Mirrors exactly how agent.plan's own parse-failure test asserts
        success on its ModelUsage row despite the response being rejected
        (test_malformed_planner_output_fails_agent_with_planner_error) --
        the gateway call itself succeeded; only this code's own schema
        check rejected the response."""
        import agents.planner_service as planner_service_module

        def _boom(raw_text):
            raise SkillMatchParseError("boom")

        monkeypatch.setattr(planner_service_module, "parse_skill_match", _boom)
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.RUNNING
        skill_match_usage = ModelUsage.objects.get(purpose="agent.skill_match")
        assert skill_match_usage.success
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1

    def test_hallucinated_or_unregistered_skill_name_degrades_cleanly(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt, monkeypatch
    ):
        """Even a non-null, well-shaped response is checked against the
        live registry before being trusted -- a hallucinated or
        since-removed skill name degrades to agent.plan exactly like null
        would, never raises."""
        import agents.planner_service as planner_service_module

        monkeypatch.setattr(
            planner_service_module, "parse_skill_match", lambda raw_text: "not_a_real_skill"
        )
        agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

        assert agent.status == Agent.Status.RUNNING
        assert agent.skill_name == ""
        assert ModelUsage.objects.filter(purpose="agent.plan").count() == 1


class TestSkillPlanSizeBoundStillApplies:
    @override_settings(AGENT_MAX_PLAN_STEPS=1)
    def test_over_bound_skill_fails_with_plan_too_large_not_matched(
        self, workspace, owner, fake_provider, agent_plan_prompt, skill_match_prompt, monkeypatch
    ):
        """Proves finalize_plan's existing plan-size bound applies to a
        skill's step list exactly as to a freeform LLM plan -- no changes
        needed there, rather than just asserting the claim."""
        import agents.planner_service as planner_service_module

        oversized = SkillSpec(
            name="oversized_test_skill",
            description="A deliberately over-bound test skill",
            trigger_hint="never matched by a real prompt",
            steps=(
                SkillStep(kind="read", description="First", payload={}),
                SkillStep(kind="read", description="Second", payload={}),
            ),
        )
        register_skill(oversized)
        try:
            monkeypatch.setattr(
                planner_service_module,
                "parse_skill_match",
                lambda raw_text: "oversized_test_skill",
            )
            agent = create_agent_from_goal(workspace=workspace, actor=owner, goal="Do a thing")

            assert agent.status == Agent.Status.FAILED
            assert agent.failure_reason == Agent.FailureReason.PLAN_TOO_LARGE
            assert agent.steps.count() == 0
            # The skipped-agent.plan optimization still applied -- the
            # bound rejected the SKILL's plan, not a fallback freeform one.
            assert ModelUsage.objects.filter(purpose="agent.plan").count() == 0
        finally:
            unregister_skill("oversized_test_skill")
