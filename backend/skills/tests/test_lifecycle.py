"""Phase 12 D14: the staged lifecycle -- proposed -> approve (owner, web,
by hash) -> active; an edit is a new version while the current stays;
`version_changed` on hash drift; reject; archive / restore; caps; audit
rows carry ids and hashes only; versions are immutable; machine proposals
through `skills.ports` land pending and are capped."""

import pytest

from audit.models import AuditLog
from audit.unattended import recorded_unattended_run
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from core.unattended import UnattendedDenied
from skills import ports, services
from skills.models import InstructionSkill, InstructionSkillVersion, VersionImmutable
from skills.tests.helpers import (
    active_skill,
    approve,
    make_firm,
    make_owner,
    personal_of,
    propose,
    skill_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


def test_an_upload_is_proposed_until_the_owner_approves_that_hash(owner, workspace):
    version = propose(workspace, owner)
    skill = version.skill
    assert skill.status == "proposed" and skill.current_version_id is None
    assert version.decision == "pending"
    approve(version, owner)
    skill.refresh_from_db()
    version.refresh_from_db()
    assert skill.status == "active" and skill.current_version_id == version.pk
    assert version.decision == "approved" and version.decided_by == owner


def test_a_hash_drift_is_version_changed(owner, workspace):
    version = propose(workspace, owner)
    with pytest.raises(services.SkillVersionChanged):
        services.approve_version(
            actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256="0" * 64
        )
    version.refresh_from_db()
    assert version.decision == "pending"


def test_an_edit_is_a_new_version_and_the_current_stays(owner, workspace):
    skill = active_skill(workspace, owner)
    first = skill.current_version
    edited = propose(workspace, owner, skill_text(body="Yeni gövde."))
    skill.refresh_from_db()
    assert edited.version == 2 and edited.decision == "pending"
    assert skill.current_version_id == first.pk and skill.status == "active"
    approve(edited, owner)
    skill.refresh_from_db()
    assert skill.current_version_id == edited.pk
    first.refresh_from_db()
    assert first.body != edited.body


def test_edit_then_approve(owner, workspace):
    version = propose(workspace, owner)
    result = services.edit_and_approve(
        actor=owner,
        channel=CHANNEL_WEB,
        version=version,
        expected_sha256=version.content_sha256,
        text=skill_text(body="Sahibin düzelttiği gövde."),
    )
    version.refresh_from_db()
    assert version.decision == "superseded"
    assert result.decision == "approved" and result.version == 2
    assert result.provenance["edited_from"] == str(version.pk)
    assert result.skill.current_version_id == result.pk


def test_edit_cannot_rename(owner, workspace):
    version = propose(workspace, owner)
    with pytest.raises(services.SkillNameMismatch):
        services.edit_and_approve(
            actor=owner,
            channel=CHANNEL_WEB,
            version=version,
            expected_sha256=version.content_sha256,
            text=skill_text(name="baska-ad"),
        )


def test_reject(owner, workspace):
    version = propose(workspace, owner)
    services.reject_version(actor=owner, channel=CHANNEL_WEB, version=version)
    version.refresh_from_db()
    version.skill.refresh_from_db()
    assert version.decision == "rejected"
    assert version.skill.status == "rejected"
    with pytest.raises(services.SkillInvalidState):
        approve(version, owner)


def test_reject_of_a_new_version_keeps_the_active_one(owner, workspace):
    skill = active_skill(workspace, owner)
    edited = propose(workspace, owner, skill_text(body="Başka."))
    services.reject_version(actor=owner, channel=CHANNEL_WEB, version=edited)
    skill.refresh_from_db()
    assert skill.status == "active" and skill.current_version.version == 1


def test_a_new_proposal_supersedes_the_pending_one(owner, workspace):
    first = propose(workspace, owner)
    second = propose(workspace, owner, skill_text(body="İkinci."))
    first.refresh_from_db()
    assert first.decision == "superseded" and second.decision == "pending"
    assert propose(workspace, owner, skill_text(body="İkinci.")).pk == second.pk


def test_archive_and_restore(owner, workspace):
    skill = active_skill(workspace, owner)
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=skill)
    skill.refresh_from_db()
    assert skill.status == "archived"
    with pytest.raises(services.SkillInvalidState):
        propose(workspace, owner, skill_text(body="arşivde"))
    services.restore_skill(actor=owner, channel=CHANNEL_WEB, skill=skill)
    skill.refresh_from_db()
    assert skill.status == "active"
    with pytest.raises(services.SkillInvalidState):
        services.restore_skill(actor=owner, channel=CHANNEL_WEB, skill=skill)


def test_a_proposed_skill_cannot_be_archived(owner, workspace):
    version = propose(workspace, owner)
    with pytest.raises(services.SkillInvalidState):
        services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=version.skill)


def test_the_active_cap(owner, workspace, settings):
    settings.SKILLS_MAX_ACTIVE = 1
    active_skill(workspace, owner)
    other = propose(workspace, owner, skill_text(name="ikinci"))
    with pytest.raises(services.SkillLimit):
        approve(other, owner)


def test_the_cap_also_bounds_restore(owner, workspace, settings):
    first = active_skill(workspace, owner)
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=first)
    active_skill(workspace, owner, skill_text(name="ikinci"))
    settings.SKILLS_MAX_ACTIVE = 1
    with pytest.raises(services.SkillLimit):
        services.restore_skill(actor=owner, channel=CHANNEL_WEB, skill=first)


@pytest.mark.parametrize("channel", [CHANNEL_NODE_VOICE, "cli", "telegram", "system"])
def test_decisions_are_owner_web_only(owner, workspace, channel):
    version = propose(workspace, owner)
    with pytest.raises(services.SkillPermissionDenied):
        services.approve_version(
            actor=owner, channel=channel, version=version, expected_sha256=version.content_sha256
        )
    with pytest.raises(services.SkillPermissionDenied):
        services.reject_version(actor=owner, channel=channel, version=version)


def test_a_member_cannot_decide_in_a_firm(owner):
    from accounts.services import register_user

    member = register_user(email="member@test.dev", password="Str0ngDevPass!42", name="M")
    firm = make_firm(owner, member)
    version = propose(firm, owner)
    with pytest.raises(services.SkillPermissionDenied):
        services.approve_version(
            actor=member,
            channel=CHANNEL_WEB,
            version=version,
            expected_sha256=version.content_sha256,
        )


def test_an_agent_cannot_decide(owner, workspace):
    from accounts.models import User

    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    version = propose(workspace, owner)
    with pytest.raises(services.SkillPermissionDenied):
        services.approve_version(
            actor=agent,
            channel=CHANNEL_WEB,
            version=version,
            expected_sha256=version.content_sha256,
        )


def test_owner_services_are_denied_inside_an_unattended_run(owner, workspace):
    version = propose(workspace, owner)
    with recorded_unattended_run("run-1", allowed_purposes=frozenset()):
        with pytest.raises(UnattendedDenied):
            approve(version, owner)
        with pytest.raises(UnattendedDenied):
            propose(workspace, owner, skill_text(name="baska"))
    version.refresh_from_db()
    assert version.decision == "pending"


def test_versions_are_immutable(owner, workspace):
    skill = active_skill(workspace, owner)
    version = skill.current_version
    version.body = "changed"
    with pytest.raises(VersionImmutable):
        version.save()
    with pytest.raises(VersionImmutable):
        version.save(update_fields=["body"])
    version.refresh_from_db()
    version.decision = "rejected"
    with pytest.raises(VersionImmutable):
        version.save(update_fields=["decision"])  # decided once
    with pytest.raises(VersionImmutable):
        version.delete()
    with pytest.raises(VersionImmutable):
        skill.delete()


def test_audit_rows_carry_ids_and_hashes_only(owner, workspace):
    body = "GİZLİ-GÖVDE-METNİ talimatlar"
    version = propose(workspace, owner, skill_text(body=body))
    approve(version, owner)
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=version.skill)
    services.restore_skill(actor=owner, channel=CHANNEL_WEB, skill=version.skill)
    actions = list(
        AuditLog.objects.filter(action__startswith="skills.").values_list("action", "metadata")
    )
    names = {action for action, _ in actions}
    assert {
        "skills.version.proposed",
        "skills.version.approved",
        "skills.skill.archived",
        "skills.skill.restored",
    } <= names
    for _, metadata in actions:
        text = str(metadata)
        assert body not in text and "PR açıklaması" not in text
    approved = next(m for a, m in actions if a == "skills.version.approved")
    assert approved["sha256"] == version.content_sha256
    assert approved["version_id"] == str(version.pk)


# --- machine proposals (skills.ports) -------------------------------------------


def test_a_curator_proposal_lands_pending(owner, workspace):
    version_id = ports.propose_version(
        workspace=workspace, text=skill_text(), proposed_by="curator", provenance={"run": "x"}
    )
    version = InstructionSkillVersion.objects.get(pk=version_id)
    assert version.decision == "pending" and version.proposed_by == "curator"
    assert version.skill.status == "proposed" and version.skill.origin == "curator"


def test_machine_proposals_pass_the_parser(owner, workspace):
    with pytest.raises(ports.SkillProposalRefused) as exc_info:
        ports.propose_version(
            workspace=workspace,
            text="---\nname: ab\ndescription: x\nallowed-tools: Bash\n---\nb\n",
            proposed_by="curator",
        )
    assert exc_info.value.code == "permission_key_refused"
    assert not InstructionSkill.objects.exists()


def test_machine_proposal_caps(owner, workspace, settings):
    settings.SKILLS_MAX_PENDING_PLAYBOOK = 2
    for index in range(2):
        ports.propose_version(
            workspace=workspace, text=skill_text(name=f"pb-{index}"), proposed_by="playbook"
        )
    with pytest.raises(ports.SkillProposalRefused) as exc_info:
        ports.propose_version(
            workspace=workspace, text=skill_text(name="pb-9"), proposed_by="playbook"
        )
    assert exc_info.value.code == "limit"


def test_an_owner_is_not_a_machine_proposer(workspace):
    with pytest.raises(ports.SkillProposalRefused):
        ports.propose_version(workspace=workspace, text=skill_text(), proposed_by="owner")


def test_ports_proposals_are_allowed_inside_an_unattended_run(workspace):
    with recorded_unattended_run("curator-1", allowed_purposes=frozenset({"skills.curate"})):
        version_id = ports.propose_version(
            workspace=workspace, text=skill_text(), proposed_by="curator"
        )
    assert InstructionSkillVersion.objects.get(pk=version_id).decision == "pending"
