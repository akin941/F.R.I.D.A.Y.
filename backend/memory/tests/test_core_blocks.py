"""Core memory blocks: services, versions, proposals and the injection
selection rules (Phase 8A branch 2, D7)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_TELEGRAM, CHANNEL_WEB
from memory.core_blocks import (
    CORE_BLOCK_CHAR_BUDGETS,
    CORE_BLOCKS_TOTAL_CHAR_BUDGET,
    owner_personal_workspace,
    select_core_blocks,
)
from memory.models import CoreBlockProposal, CoreMemoryBlock, CoreMemoryBlockVersion
from memory.services import (
    CoreBlockInvalid,
    CoreBlockPermissionDenied,
    CoreBlockProposalCapReached,
    CoreBlockStale,
    InvalidTransition,
    approve_core_block_proposal,
    create_core_block_proposal,
    reject_core_block_proposal,
    rollback_core_block,
    set_assembled_self_block,
    update_core_block,
)
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
Label = CoreMemoryBlock.Label


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    ws = Workspace.objects.create(name="Firm", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=ws, user=owner, role=WorkspaceMember.Role.OWNER)
    return ws


def edit(workspace, label, content, owner, **kwargs):
    return update_core_block(
        workspace=workspace,
        label=label,
        content=content,
        actor=owner,
        channel=CHANNEL_WEB,
        **kwargs,
    )


# --- budgets ---


def test_budgets_match_the_brief():
    assert CORE_BLOCK_CHAR_BUDGETS == {
        "self": 1500,
        "developer": 1200,
        "current_focus": 600,
        "active_projects": 800,
        "preferences": 1000,
    }
    assert CORE_BLOCKS_TOTAL_CHAR_BUDGET == 5100


def test_owner_edit_over_budget_is_refused(owner, personal):
    with pytest.raises(CoreBlockInvalid):
        edit(personal, Label.CURRENT_FOCUS, "x" * 601, owner)
    assert not CoreMemoryBlock.objects.exists()


# --- who writes what ---


def test_owner_edit_creates_versions_and_audits_without_content(owner, personal):
    block = edit(personal, Label.DEVELOPER, "Akın geliştiriyor.", owner)
    assert block.version == 1
    assert block.updated_by_kind == CoreMemoryBlock.WriterKind.OWNER
    block = edit(personal, Label.DEVELOPER, "Akın Friday'i geliştiriyor.", owner)
    assert block.version == 2
    assert list(
        CoreMemoryBlockVersion.objects.filter(block=block).values_list("version", "content")
    ) == [(2, "Akın Friday'i geliştiriyor."), (1, "Akın geliştiriyor.")]
    rows = AuditLog.objects.filter(action="memory.core_block.updated")
    assert rows.count() == 2
    for row in rows:
        assert set(row.metadata) == {"label", "version", "writer_kind"}
        assert "Akın" not in str(row.metadata)


def test_noop_edit_writes_nothing(owner, personal):
    edit(personal, Label.PREFERENCES, "Kısa cevaplar.", owner)
    block = edit(personal, Label.PREFERENCES, "Kısa cevaplar.", owner)
    assert block.version == 1
    assert CoreMemoryBlockVersion.objects.count() == 1


def test_stale_base_version_refused(owner, personal):
    edit(personal, Label.PREFERENCES, "v1", owner)
    with pytest.raises(CoreBlockStale):
        edit(personal, Label.PREFERENCES, "v2", owner, base_version=0)
    assert edit(personal, Label.PREFERENCES, "v2", owner, base_version=1).version == 2


def test_self_cannot_be_written_by_the_owner(owner, personal):
    with pytest.raises(CoreBlockPermissionDenied):
        edit(personal, Label.SELF, "Ben bir korsanım.", owner)
    assert not CoreMemoryBlock.objects.filter(label=Label.SELF).exists()


@pytest.mark.parametrize("channel", [CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_TELEGRAM, "unknown"])
def test_owner_edit_refused_off_the_web_channel(owner, personal, channel):
    with pytest.raises(CoreBlockPermissionDenied):
        update_core_block(
            workspace=personal, label=Label.DEVELOPER, content="x", actor=owner, channel=channel
        )


def test_agents_and_non_owners_cannot_edit(owner, personal):
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True, is_owner=False)
    WorkspaceMember.objects.create(workspace=personal, user=agent, role=WorkspaceMember.Role.OWNER)
    with pytest.raises(CoreBlockPermissionDenied):
        edit(personal, Label.DEVELOPER, "x", agent)
    other = register_user(email="other@test.dev", password=PASSWORD)
    with pytest.raises(CoreBlockPermissionDenied):
        edit(Workspace.objects.get(owner=other), Label.DEVELOPER, "x", other)


def test_personal_labels_do_not_exist_in_a_firm(owner, firm):
    with pytest.raises(CoreBlockInvalid):
        edit(firm, Label.DEVELOPER, "x", owner)
    assert edit(firm, Label.CURRENT_FOCUS, "Sprint 3", owner).workspace == firm


def test_rollback_restores_content_as_a_new_version(owner, personal):
    edit(personal, Label.ACTIVE_PROJECTS, "Friday", owner)
    edit(personal, Label.ACTIVE_PROJECTS, "Friday, Harness", owner, sensitivity="private")
    block = rollback_core_block(
        workspace=personal, label=Label.ACTIVE_PROJECTS, version=1, actor=owner, channel=CHANNEL_WEB
    )
    assert (block.version, block.content, block.sensitivity) == (3, "Friday", "normal")
    assert CoreMemoryBlockVersion.objects.filter(block=block).count() == 3
    with pytest.raises(CoreBlockInvalid):
        rollback_core_block(
            workspace=personal,
            label=Label.ACTIVE_PROJECTS,
            version=99,
            actor=owner,
            channel=CHANNEL_WEB,
        )


def test_rollback_refused_off_web_and_for_self(owner, personal):
    set_assembled_self_block(workspace=personal, content="a")
    set_assembled_self_block(workspace=personal, content="b")
    with pytest.raises(CoreBlockPermissionDenied):
        rollback_core_block(
            workspace=personal, label=Label.SELF, version=1, actor=owner, channel=CHANNEL_WEB
        )
    with pytest.raises(CoreBlockPermissionDenied):
        rollback_core_block(
            workspace=personal, label=Label.DEVELOPER, version=1, actor=owner, channel=CHANNEL_CLI
        )


def test_assembler_versions_only_on_change(personal):
    block, changed = set_assembled_self_block(workspace=personal, content="Ben Friday'im.")
    assert changed and block.version == 1
    assert block.updated_by_kind == CoreMemoryBlock.WriterKind.ASSEMBLER
    block, changed = set_assembled_self_block(workspace=personal, content="Ben Friday'im.")
    assert not changed and block.version == 1
    block, changed = set_assembled_self_block(workspace=personal, content="Ben Friday'im!")
    assert changed and block.version == 2
    row = AuditLog.objects.filter(action="memory.core_block.updated").latest("created_at")
    assert row.actor_type == AuditLog.ActorType.SYSTEM
    assert row.metadata["writer_kind"] == "assembler"


def test_assembler_refuses_firm_workspace_and_over_budget(personal, firm):
    with pytest.raises(CoreBlockInvalid):
        set_assembled_self_block(workspace=firm, content="x")
    with pytest.raises(CoreBlockInvalid):
        set_assembled_self_block(workspace=personal, content="x" * 1501)


# --- proposals (services only; the producer is branch 5) ---


def propose(workspace, label=Label.CURRENT_FOCUS, content="Odak: Phase 8A"):
    return create_core_block_proposal(
        workspace=workspace,
        label=label,
        proposed_content=content,
        provenance_quote="bu hafta Phase 8A üzerinde çalışıyorum",
    )


def test_proposal_never_applies_and_carries_a_diff(owner, personal):
    edit(personal, Label.CURRENT_FOCUS, "Odak: Phase 6", owner)
    proposal = propose(personal)
    assert proposal.status == CoreBlockProposal.Status.PENDING
    assert proposal.inferred is True
    assert proposal.base_version == 1
    assert "-Odak: Phase 6" in proposal.diff and "+Odak: Phase 8A" in proposal.diff
    assert CoreMemoryBlock.objects.get(label=Label.CURRENT_FOCUS).content == "Odak: Phase 6"


def test_proposal_for_self_or_without_quote_refused(personal):
    with pytest.raises(CoreBlockPermissionDenied):
        propose(personal, label=Label.SELF)
    with pytest.raises(CoreBlockInvalid):
        create_core_block_proposal(
            workspace=personal, label=Label.DEVELOPER, proposed_content="x", provenance_quote="  "
        )


def test_approve_applies_per_item_over_web_only(owner, personal):
    proposal = propose(personal, label=Label.DEVELOPER, content="Geliştirici: Akın")
    with pytest.raises(CoreBlockPermissionDenied):
        approve_core_block_proposal(proposal=proposal, actor=owner, channel=CHANNEL_TELEGRAM)
    approve_core_block_proposal(proposal=proposal, actor=owner, channel=CHANNEL_WEB)
    block = CoreMemoryBlock.objects.get(label=Label.DEVELOPER)
    assert block.content == "Geliştirici: Akın"
    assert block.updated_by_kind == CoreMemoryBlock.WriterKind.APPROVED_PROPOSAL
    proposal.refresh_from_db()
    assert proposal.status == CoreBlockProposal.Status.APPROVED
    with pytest.raises(InvalidTransition):
        approve_core_block_proposal(proposal=proposal, actor=owner, channel=CHANNEL_WEB)


def test_stale_proposal_refused_at_approval(owner, personal):
    proposal = propose(personal)
    edit(personal, Label.CURRENT_FOCUS, "owner changed it meanwhile", owner)
    with pytest.raises(CoreBlockStale):
        approve_core_block_proposal(proposal=proposal, actor=owner, channel=CHANNEL_WEB)
    proposal.refresh_from_db()
    assert proposal.status == CoreBlockProposal.Status.PENDING
    assert CoreMemoryBlock.objects.get(label=Label.CURRENT_FOCUS).version == 1


def test_reject(owner, personal):
    proposal = propose(personal)
    reject_core_block_proposal(proposal=proposal, actor=owner, channel=CHANNEL_WEB)
    proposal.refresh_from_db()
    assert proposal.status == CoreBlockProposal.Status.REJECTED
    assert not CoreMemoryBlock.objects.exists()


def test_weekly_cap_counts_pending_and_approved_across_workspaces(owner, personal, firm):
    first = propose(personal)
    propose(firm, content="firma odak")
    propose(personal, label=Label.PREFERENCES, content="kısa")
    with pytest.raises(CoreBlockProposalCapReached):
        propose(firm, content="dördüncü")
    assert CoreBlockProposal.objects.count() == 3
    # A rejected proposal frees its slot.
    reject_core_block_proposal(proposal=first, actor=owner, channel=CHANNEL_WEB)
    propose(firm, content="dördüncü")
    # Last week's proposals do not count.
    CoreBlockProposal.objects.update(created_at=timezone.now() - timedelta(days=8))
    propose(personal, content="yeni hafta")


# --- injection selection (D7) ---


def test_owner_personal_workspace(owner, personal):
    assert owner_personal_workspace() == personal


def test_owner_personal_workspace_none_without_owner(db):
    register_user(email="x@test.dev", password=PASSWORD)
    assert owner_personal_workspace() is None


@pytest.fixture
def blocks(owner, personal, firm):
    set_assembled_self_block(workspace=personal, content="Ben Friday'im.")
    edit(personal, Label.DEVELOPER, "Geliştirici: Akın", owner)
    edit(personal, Label.PREFERENCES, "Türkçe, kısa", owner)
    edit(personal, Label.CURRENT_FOCUS, "kişisel odak", owner)
    edit(firm, Label.CURRENT_FOCUS, "firma odak", owner)
    edit(firm, Label.ACTIVE_PROJECTS, "firma projeleri", owner)


def labels(selected):
    return [b.label for b in selected]


def test_owner_web_personal_gets_everything_in_order(owner, personal, blocks):
    selected = select_core_blocks(actor=owner, workspace=personal, channel=CHANNEL_WEB)
    assert labels(selected) == ["self", "developer", "current_focus", "preferences"]
    assert {b.label: b.content for b in selected}["current_focus"] == "kişisel odak"


@pytest.mark.parametrize("channel", [CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_TELEGRAM])
def test_personal_blocks_absent_off_web(owner, personal, blocks, channel):
    selected = select_core_blocks(actor=owner, workspace=personal, channel=channel)
    assert labels(selected) == ["self", "current_focus"]
    assert not AuditLog.objects.filter(action="memory.core_block.read").exists()


def test_single_member_firm_gets_personal_blocks_with_audit(owner, personal, firm, blocks):
    selected = select_core_blocks(actor=owner, workspace=firm, channel=CHANNEL_WEB)
    assert labels(selected) == [
        "self",
        "developer",
        "current_focus",
        "active_projects",
        "preferences",
    ]
    assert {b.label: b.content for b in selected}["current_focus"] == "firma odak"
    row = AuditLog.objects.get(action="memory.core_block.read")
    assert row.workspace == personal
    assert row.metadata["labels"] == ["developer", "preferences"]
    assert row.metadata["target_workspace_id"] == str(firm.id)
    assert "Akın" not in str(row.metadata)


def test_firm_with_second_member_gets_no_personal_blocks(owner, firm, blocks):
    colleague = register_user(email="colleague@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=colleague, role=WorkspaceMember.Role.MEMBER)
    assert labels(select_core_blocks(actor=owner, workspace=firm, channel=CHANNEL_WEB)) == [
        "self",
        "current_focus",
        "active_projects",
    ]
    # The colleague sees `self` and the firm's own blocks, never the owner's.
    assert labels(select_core_blocks(actor=colleague, workspace=firm, channel=CHANNEL_WEB)) == [
        "self",
        "current_focus",
        "active_projects",
    ]


def test_private_block_never_selected(owner, personal, blocks):
    edit(personal, Label.DEVELOPER, "gizli", owner, sensitivity="private")
    selected = select_core_blocks(actor=owner, workspace=personal, channel=CHANNEL_WEB)
    assert "developer" not in labels(selected)


def test_selection_truncates_to_budget(owner, personal):
    CoreMemoryBlock.objects.create(
        workspace=personal,
        label=Label.CURRENT_FOCUS,
        content="x" * 900,
        version=1,
        updated_by_kind="owner",
    )
    (block,) = select_core_blocks(actor=owner, workspace=personal, channel=CHANNEL_WEB)
    assert len(block.content) == 600
