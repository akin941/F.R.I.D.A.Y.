# ruff: noqa: F811 -- pytest fixtures imported from the fixture module are redefined as arguments
"""Phase 12 D16: the weekly curator -- the deterministic stale pass (manual
clock, no model call), the one `skills.curate` call, proposals that land
`pending` and are never applied, archive suggestions, caps, the budget
refusal, once per week, workspace isolation, the beat entry and the probe."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from ai_core.models import AIBudget, ModelProvider, ModelUsage
from audit.models import AuditLog
from core import probes
from skills import curator, ports, services
from skills.models import InstructionSkill, InstructionSkillVersion, SkillCuratorRun
from skills.probes import SPEC
from skills.tests.curator_fixtures import (  # noqa: F401
    curate_answer,
    owner,
    provider,
    seeded,
    skill_md,
    workspace,
)
from skills.tests.helpers import active_skill, make_firm, propose, skill_text

pytestmark = pytest.mark.django_db

Outcome = SkillCuratorRun.Outcome
DAYS = 45


def _skill(workspace, owner, name, body=None):
    text = skill_text(name=name, body=body) if body else skill_text(name=name)
    return active_skill(workspace, owner, text)


def _status(skill):
    skill.refresh_from_db()
    return skill.status


def _usage():
    return list(ModelUsage.objects.filter(purpose=curator.CURATE_PURPOSE))


# --- the deterministic pass ---------------------------------------------------


def test_an_unused_skill_goes_stale_after_the_window(owner, workspace, settings):
    settings.SKILLS_STALE_DAYS = DAYS
    skill = _skill(workspace, owner, "pr-yaz")
    now = timezone.now()
    assert curator.mark_stale(workspace, now=now + timedelta(days=DAYS - 1)) == (0, 0)
    assert _status(skill) == "active"
    assert curator.mark_stale(workspace, now=now + timedelta(days=DAYS + 1)) == (1, 0)
    assert _status(skill) == "stale"
    row = AuditLog.objects.get(action="skills.skill.stale")
    assert row.metadata["skill_id"] == str(skill.pk)
    assert set(row.metadata) == {"skill_id", "previous", "version_id", "reference", "source"}
    assert ModelUsage.objects.count() == 0


def test_a_recent_use_keeps_the_skill_active(owner, workspace):
    skill = _skill(workspace, owner, "pr-yaz")
    now = timezone.now()
    InstructionSkill.objects.filter(pk=skill.pk).update(last_used_at=now + timedelta(days=30))
    assert curator.mark_stale(workspace, now=now + timedelta(days=DAYS + 1)) == (0, 0)
    assert _status(skill) == "active"


def test_a_stale_skill_is_active_again_on_use(owner, workspace):
    skill = _skill(workspace, owner, "pr-yaz")
    InstructionSkill.objects.filter(pk=skill.pk).update(status="stale")
    services.record_use(skill.pk, now=timezone.now())
    skill.refresh_from_db()
    assert skill.status == "active" and skill.use_count == 1
    row = AuditLog.objects.get(action="skills.skill.reactivated")
    assert row.metadata["skill_id"] == str(skill.pk) and row.actor_id is None
    # A use of an active skill writes no status audit row.
    services.record_use(skill.pk)
    assert AuditLog.objects.filter(action="skills.skill.reactivated").count() == 1


def test_the_pass_reactivates_a_stale_skill_used_inside_the_window(owner, workspace):
    skill = _skill(workspace, owner, "pr-yaz")
    now = timezone.now()
    InstructionSkill.objects.filter(pk=skill.pk).update(status="stale", last_used_at=now)
    assert curator.mark_stale(workspace, now=now + timedelta(days=1)) == (0, 1)
    assert _status(skill) == "active"


def test_a_stale_skill_is_still_offered_and_archived_or_proposed_skills_are_ignored(
    owner, workspace
):
    stale = _skill(workspace, owner, "eski")
    archived = _skill(workspace, owner, "arsiv")
    InstructionSkill.objects.filter(pk=archived.pk).update(status="archived")
    pending = propose(workspace, owner, skill_text(name="taslak"))
    later = timezone.now() + timedelta(days=DAYS + 1)
    assert curator.mark_stale(workspace, now=later) == (1, 0)
    assert _status(stale) == "stale" and _status(archived) == "archived"
    assert _status(pending.skill) == "proposed"


def test_the_deterministic_pass_runs_without_a_provider_and_makes_no_call(owner, workspace, seeded):
    skill = _skill(workspace, owner, "pr-yaz")
    run = curator.curate_workspace(workspace, now=timezone.now() + timedelta(days=DAYS + 1))
    assert run.outcome == Outcome.NO_PROVIDER
    assert run.stale_marked == 1 and _status(skill) == "stale"
    assert ModelUsage.objects.count() == 0


# --- no call when there is nothing to curate -----------------------------------


def test_no_workspace_with_usable_skills_means_no_run_and_no_call(
    owner, workspace, seeded, provider, curate_answer
):
    propose(workspace, owner)  # proposed, never approved
    assert curator.curate_all() == []
    assert SkillCuratorRun.objects.count() == 0
    assert curate_answer.messages == [] and _usage() == []


def test_a_workspace_without_usable_skills_records_no_skills(owner, workspace, seeded, provider):
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.NO_SKILLS and ModelUsage.objects.count() == 0


def test_no_prompt_means_no_call(owner, workspace, seeded, provider):
    from ai_core.models import PromptTemplate

    _skill(workspace, owner, "pr-yaz")
    PromptTemplate.objects.filter(name=curator.CURATE_PROMPT_NAME).delete()
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.NO_PROMPT and ModelUsage.objects.count() == 0


# --- the call -------------------------------------------------------------------


def test_one_call_with_the_approved_text_and_a_quiet_answer(
    owner, workspace, seeded, provider, curate_answer
):
    skill = _skill(workspace, owner, "pr-yaz")
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.QUIET
    [usage] = _usage()
    assert str(run.model_usage_id) == str(usage.pk)
    assert usage.context_report["skills_curator"]["run_id"] == str(run.pk)
    assert usage.workspace_id == workspace.pk
    assert usage.context_report["personal_data"] is True
    [messages] = curate_answer.messages
    system = messages[0]["content"]
    assert "<skills>" in system and skill.current_version.body in system
    assert run.skills_considered == 1 and run.proposals_created == 0
    assert InstructionSkillVersion.objects.count() == 1


def test_a_firm_workspace_is_sent_without_personal_data_and_never_sees_personal_skills(
    owner, workspace, seeded, provider, curate_answer
):
    from accounts.services import register_user
    from skills.tests.helpers import PASSWORD

    member = register_user(email="m@test.dev", password=PASSWORD, name="M")
    firm = make_firm(owner, member)
    _skill(workspace, owner, "kisisel-beceri", body="Kişisel gizli talimat.")
    _skill(firm, owner, "firma-beceri", body="Firma talimatı.")
    run = curator.curate_workspace(firm)
    assert run.outcome == Outcome.QUIET
    [messages] = curate_answer.messages
    assert "Firma talimatı." in messages[0]["content"]
    assert "Kişisel gizli talimat." not in messages[0]["content"]
    usage = _usage()[0]
    assert "personal_data" not in usage.context_report


def test_a_personal_workspace_needs_a_personal_data_provider(
    owner, workspace, seeded, provider, curate_answer
):
    ModelProvider.objects.filter(pk=provider.pk).update(personal_data_allowed=False)
    _skill(workspace, owner, "pr-yaz")
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.NO_PROVIDER and curate_answer.messages == []


def test_an_edit_lands_pending_and_is_never_applied(
    owner, workspace, seeded, provider, curate_answer
):
    skill = _skill(workspace, owner, "pr-yaz")
    current = skill.current_version_id
    curate_answer.answer(
        [{"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "Daha iyi.")}]
    )
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.PROPOSALS and run.proposals_created == 1
    version = InstructionSkillVersion.objects.get(decision="pending")
    assert version.proposed_by == "curator" and version.skill_id == skill.pk
    assert version.provenance["curator_run_id"] == str(run.pk)
    assert version.provenance["action"] == "edit"
    assert version.provenance["base"]["version_id"] == str(current)
    assert run.proposal_version_ids == [str(version.pk)]
    skill.refresh_from_db()
    assert skill.current_version_id == current and skill.status == "active"
    assert not InstructionSkillVersion.objects.filter(decision="approved", proposed_by="curator")
    proposed = AuditLog.objects.get(action="skills.version.proposed", actor__isnull=True)
    assert proposed.metadata["proposed_by"] == "curator"


def test_a_merge_proposes_one_version_and_suggests_archiving_the_other(
    owner, workspace, seeded, provider, curate_answer
):
    keep = _skill(workspace, owner, "pr-yaz")
    other = _skill(workspace, owner, "pr-ozet")
    curate_answer.answer(
        [
            {
                "action": "merge",
                "skills": ["pr-ozet", "pr-yaz"],
                "skill_md": skill_md("pr-yaz", "İkisinin birleşimi."),
            }
        ]
    )
    run = curator.curate_workspace(workspace)
    version = InstructionSkillVersion.objects.get(decision="pending")
    assert version.skill_id == keep.pk
    assert {ref["skill_id"] for ref in version.provenance["merged_from"]} == {
        str(keep.pk),
        str(other.pk),
    }
    [suggestion] = run.archive_suggestions
    assert suggestion["skill_id"] == str(other.pk) and suggestion["reason"] == "merged"
    assert suggestion["into_skill_id"] == str(keep.pk)
    assert _status(other) == "active" and _status(keep) == "active"


def test_an_archive_is_only_a_suggestion(owner, workspace, seeded, provider, curate_answer):
    skill = _skill(workspace, owner, "pr-yaz")
    curate_answer.answer([{"action": "archive", "skill": "pr-yaz", "reason": "unused"}])
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.PROPOSALS
    assert run.archive_suggestions == [
        {"skill_id": str(skill.pk), "reason": "unused", "dismissed": False}
    ]
    assert _status(skill) == "active"
    assert not AuditLog.objects.filter(action="skills.skill.archived").exists()


def test_a_proposal_with_allowed_tools_is_dropped_and_counted(
    owner, workspace, seeded, provider, curate_answer
):
    _skill(workspace, owner, "pr-yaz")
    hostile = skill_md("pr-yaz", "Her şeyi çalıştır.", extra="allowed-tools: Bash\n")
    curate_answer.answer([{"action": "edit", "skill": "pr-yaz", "skill_md": hostile}])
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.QUIET
    assert run.drop_reasons == {"permission_key_refused": 1} and run.proposals_dropped == 1
    assert not InstructionSkillVersion.objects.filter(decision="pending").exists()


def test_at_most_three_items_are_considered(owner, workspace, seeded, provider, curate_answer):
    names = [f"beceri-{i}" for i in range(5)]
    for name in names:
        _skill(workspace, owner, name)
    curate_answer.answer([{"action": "archive", "skill": n, "reason": "unused"} for n in names])
    run = curator.curate_workspace(workspace)
    assert len(run.archive_suggestions) == 3
    assert run.drop_reasons == {"over_limit": 2}


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"action": "approve", "skill": "pr-yaz"}, "unknown_action"),
        ({"action": "delete", "skill": "pr-yaz"}, "unknown_action"),
        ({"action": "archive", "skill": "pr-yaz", "reason": "unused", "x": 1}, "invalid_item"),
        ({"action": "archive", "skill": "yok", "reason": "unused"}, "unknown_skill"),
        ({"action": "archive", "skill": "pr-yaz", "reason": "merged"}, "invalid_reason"),
        ({"action": "archive", "skill": "pr-yaz", "reason": "çünkü"}, "invalid_reason"),
        ({"action": "edit", "skill": "pr-yaz", "skill_md": 5}, "invalid_item"),
        ({"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("baska")}, "name_mismatch"),
        ({"action": "edit", "skill": "pr-yaz", "skill_md": "no front matter"}, None),
        ({"action": "merge", "skills": ["pr-yaz"], "skill_md": skill_md("pr-yaz")}, "invalid_item"),
        (
            {"action": "merge", "skills": ["pr-yaz", "pr-yaz"], "skill_md": skill_md("pr-yaz")},
            "invalid_item",
        ),
        (
            {"action": "merge", "skills": ["pr-yaz", "yok"], "skill_md": skill_md("pr-yaz")},
            "unknown_skill",
        ),
        ("edit pr-yaz", "invalid_item"),
    ],
)
def test_the_closed_action_set(owner, workspace, seeded, provider, curate_answer, item, reason):
    _skill(workspace, owner, "pr-yaz")
    curate_answer.answer([item])
    run = curator.curate_workspace(workspace)
    assert run.proposals_dropped == 1 and run.proposals_created == 0
    if reason is not None:
        assert run.drop_reasons == {reason: 1}
    assert run.archive_suggestions == []
    assert not InstructionSkillVersion.objects.filter(decision="pending").exists()


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"proposals": {}}', '{"x": []}'])
def test_a_malformed_answer_proposes_nothing(
    owner, workspace, seeded, provider, curate_answer, text
):
    _skill(workspace, owner, "pr-yaz")
    curate_answer.text = text
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.PARSE_ERROR
    assert len(_usage()) == 1
    assert not InstructionSkillVersion.objects.filter(decision="pending").exists()


def test_one_change_per_skill_per_run(owner, workspace, seeded, provider, curate_answer):
    _skill(workspace, owner, "pr-yaz")
    curate_answer.answer(
        [
            {"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "Bir.")},
            {"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "İki.")},
        ]
    )
    run = curator.curate_workspace(workspace)
    assert run.proposals_created == 1 and run.drop_reasons == {"duplicate_target": 1}
    assert InstructionSkillVersion.objects.get(decision="pending").body.startswith("Bir.")


def test_the_owners_pending_version_is_never_superseded(
    owner, workspace, seeded, provider, curate_answer
):
    _skill(workspace, owner, "pr-yaz")
    owners = propose(workspace, owner, skill_text(name="pr-yaz", body="Sahibin taslağı."))
    curate_answer.answer(
        [{"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "Model.")}]
    )
    run = curator.curate_workspace(workspace)
    assert run.drop_reasons == {"other_pending": 1}
    owners.refresh_from_db()
    assert owners.decision == "pending"


def test_a_body_not_shown_in_full_cannot_be_rewritten(
    owner, workspace, seeded, provider, curate_answer, settings
):
    settings.SKILLS_CURATE_BODY_MAX_CHARS = 20
    _skill(workspace, owner, "pr-yaz", body="x" * 100)
    curate_answer.answer(
        [{"action": "edit", "skill": "pr-yaz", "skill_md": skill_md("pr-yaz", "Kısa.")}]
    )
    run = curator.curate_workspace(workspace)
    assert run.drop_reasons == {"incomplete_input": 1}
    assert "x" * 21 not in curate_answer.messages[0][0]["content"]


def test_the_input_is_bounded(owner, workspace, seeded, provider, curate_answer, settings):
    settings.SKILLS_CURATE_INPUT_MAX_CHARS = 400
    for i in range(5):
        _skill(workspace, owner, f"beceri-{i}", body=f"govde-{i} " + "y" * 150)
    curator.curate_workspace(workspace)
    system = curate_answer.messages[0][0]["content"]
    assert "govde-0" in system and "govde-4" not in system
    assert '"name": "beceri-4"' in system


# --- caps, budget, once a week --------------------------------------------------


def test_the_pending_cap_skips_the_call(owner, workspace, seeded, provider, curate_answer):
    for i in range(5):
        ports.propose_version(
            workspace=workspace, text=skill_md(f"yeni-{i}"), proposed_by="curator"
        )
    _skill(workspace, owner, "pr-yaz")
    run = curator.curate_workspace(workspace)
    assert run.outcome == Outcome.SKIPPED_CAP
    assert curate_answer.messages == [] and _usage() == []


def test_the_cap_reached_mid_run_drops_the_rest(owner, workspace, seeded, provider, curate_answer):
    for i in range(4):
        ports.propose_version(
            workspace=workspace, text=skill_md(f"yeni-{i}"), proposed_by="curator"
        )
    _skill(workspace, owner, "bir")
    _skill(workspace, owner, "iki")
    curate_answer.answer(
        [
            {"action": "edit", "skill": "bir", "skill_md": skill_md("bir", "Yeni bir.")},
            {"action": "edit", "skill": "iki", "skill_md": skill_md("iki", "Yeni iki.")},
        ]
    )
    run = curator.curate_workspace(workspace)
    assert run.proposals_created == 1 and run.drop_reasons == {"limit": 1}
    assert (
        InstructionSkillVersion.objects.filter(decision="pending", proposed_by="curator").count()
        == 5
    )


def test_a_budget_refusal_skips_the_proposals(owner, workspace, seeded, provider, curate_answer):
    ModelProvider.objects.filter(pk=provider.pk).update(input_price_per_mtok=Decimal("1"))
    budget = AIBudget.objects.get(name="skills_curate_weekly")
    ModelUsage.objects.create(
        purpose=curator.CURATE_PURPOSE,
        estimated_cost=budget.daily_limit_usd,
        success=True,
        workspace=workspace,
    )
    skill = _skill(workspace, owner, "pr-yaz")
    run = curator.curate_workspace(workspace, now=timezone.now())
    assert run.outcome == Outcome.SKIPPED_BUDGET
    assert curate_answer.messages == []
    refused = ModelUsage.objects.get(purpose=curator.CURATE_PURPOSE, error_type="budget_exceeded")
    assert str(run.model_usage_id) == str(refused.pk)
    assert _status(skill) == "active"


def test_at_most_one_run_per_workspace_per_week(owner, workspace, seeded, provider, curate_answer):
    _skill(workspace, owner, "pr-yaz")
    now = timezone.now()
    assert curator.curate_workspace(workspace, now=now) is not None
    assert curator.curate_workspace(workspace, now=now) is None
    assert len(_usage()) == 1
    assert curator.curate_workspace(workspace, now=now + timedelta(days=7)) is not None
    assert SkillCuratorRun.objects.count() == 2


def test_curate_all_covers_each_workspace_once(owner, workspace, seeded, provider):
    from accounts.services import register_user
    from skills.tests.helpers import PASSWORD

    member = register_user(email="m@test.dev", password=PASSWORD, name="M")
    firm = make_firm(owner, member)
    _skill(workspace, owner, "pr-yaz")
    _skill(firm, owner, "pr-yaz")
    results = curator.curate_all()
    assert {r["workspace"] for r in results} == {str(workspace.pk), str(firm.pk)}
    assert {r["outcome"] for r in results} == {"quiet"}
    assert {r["outcome"] for r in curator.curate_all()} == {"already_ran"}


# --- a week of fake usage --------------------------------------------------------


def test_a_week_of_fake_usage(owner, workspace, seeded, provider, curate_answer):
    """Done-when: stale marks and at most three proposals the owner can
    decide, with no other effect."""
    from core.channels import CHANNEL_WEB

    used = _skill(workspace, owner, "kullanilan")
    idle = _skill(workspace, owner, "bosta")
    third = _skill(workspace, owner, "ucuncu")
    start = timezone.now() + timedelta(days=DAYS)
    for day in range(7):
        services.record_use(used.pk, now=start + timedelta(days=day))
    curate_answer.answer(
        [
            {"action": "edit", "skill": "kullanilan", "skill_md": skill_md("kullanilan", "v2")},
            {"action": "archive", "skill": "bosta", "reason": "unused"},
            {"action": "edit", "skill": "ucuncu", "skill_md": skill_md("ucuncu", "v2")},
            {"action": "archive", "skill": "ucuncu", "reason": "outdated"},
        ]
    )
    before = {
        "audit": set(AuditLog.objects.values_list("action", flat=True).distinct()),
    }
    run = curator.curate_workspace(workspace, now=start + timedelta(days=7))
    assert run.stale_marked == 2
    assert _status(used) == "active" and _status(idle) == "stale" and _status(third) == "stale"
    assert run.proposals_created + len(run.archive_suggestions) == 3
    assert run.drop_reasons == {"over_limit": 1}
    new_actions = (
        set(AuditLog.objects.values_list("action", flat=True).distinct()) - before["audit"]
    )
    assert new_actions <= {"skills.skill.stale", "skills.version.proposed"}

    # The owner decides on /skills: approve one edit, archive the idle one.
    pending = InstructionSkillVersion.objects.get(skill=used, decision="pending")
    services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=pending, expected_sha256=pending.content_sha256
    )
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=idle)
    result = SPEC.collect(workspace, owner, timezone.now())
    probes.validate_result(SPEC, result, max_keys=200)
    assert result.count == 1  # the other edit, still pending


def test_the_task_runs_the_curator(owner, workspace, seeded, provider):
    from skills.tasks import curate

    _skill(workspace, owner, "pr-yaz")
    assert curate() == [{"workspace": str(workspace.pk), "outcome": "quiet"}]
