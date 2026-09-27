"""Cross-workspace overlay for the personal assistant (Phase 1
p1-personal-overlay, MASTER_PLAN §O). `select_overlay_memories()` is a
SIBLING to `select_context_memories` -- these tests exercise it in
isolation; `assistant/tests/test_context.py` covers the full
`build_context()` integration (channel gating, rendering, provenance)."""

import pytest

from accounts.services import register_user
from audit.models import AuditLog
from memory.context import select_overlay_memories
from memory.models import Memory, compute_fingerprint
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def personal_workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_memory(workspace, content, **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.WORK_LOG,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.SYSTEM_GENERATED,
        "title": content[:30],
        "content": content,
        "fingerprint": compute_fingerprint(content),
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


class TestOverlayGate:
    def test_work_log_memory_from_another_owned_workspace_is_included(
        self, owner, personal_workspace
    ):
        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        memory = make_memory(company, "shipped the migration")
        result = select_overlay_memories(actor=owner, workspace=personal_workspace)
        assert [m for m, _ in result] == [memory]
        assert [ws.id for _, ws in result] == [company.id]

    def test_non_work_log_type_is_excluded(self, owner, personal_workspace):
        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        make_memory(company, "a preference", memory_type=Memory.Type.PREFERENCE)
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_private_sensitivity_is_excluded(self, owner, personal_workspace):
        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        make_memory(company, "private log", sensitivity=Memory.Sensitivity.PRIVATE)
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_non_approved_status_is_excluded(self, owner, personal_workspace):
        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        make_memory(company, "pending log", status=Memory.Status.PENDING_APPROVAL)
        make_memory(company, "archived log", status=Memory.Status.ARCHIVED)
        make_memory(company, "rejected log", status=Memory.Status.REJECTED)
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_company_workspace_conversation_gets_no_overlay(self, owner, personal_workspace):
        """One-way: personal reads outward. A company-workspace conversation
        must never pull from the owner's personal (or any other) workspace."""
        make_memory(personal_workspace, "a personal work log")
        other_company = Workspace.objects.create(
            name="Beta", owner=owner, type=Workspace.Type.COMPANY
        )
        WorkspaceMember.objects.create(
            workspace=other_company, user=owner, role=WorkspaceMember.Role.OWNER
        )
        assert select_overlay_memories(actor=owner, workspace=other_company) == []

    def test_actor_who_does_not_own_the_target_personal_workspace_gets_nothing(
        self, owner, personal_workspace
    ):
        """Codex round-1 HIGH: for_actor()-membership is not the right gate.
        A personal workspace is always owned by its own creator, so this
        exercises the (b) condition directly: an actor who is merely a
        MEMBER (not the owner) of what LOOKS like a personal workspace
        never gets an overlay through it."""
        other_owner = register_user(email="other@test.dev", password=PASSWORD, name="Other")
        WorkspaceMember.objects.create(
            workspace=personal_workspace, user=other_owner, role=WorkspaceMember.Role.MEMBER
        )
        make_memory(
            Workspace.objects.create(name="Acme", owner=other_owner, type=Workspace.Type.COMPANY),
            "someone else's work log",
        )
        assert select_overlay_memories(actor=other_owner, workspace=personal_workspace) == []

    def test_shared_personal_workspace_never_gets_overlay(self, owner, personal_workspace):
        """Codex round-2 P1: nothing in the schema stops a "personal"
        workspace from gaining a second WorkspaceMember (no
        member-management UI exists yet, but the model doesn't prevent it).
        Without this gate, the owner's own web-channel turn could inject
        overlay content that a fellow member of this "personal" workspace
        -- who has zero access to the source workspace -- could then read
        straight off the shared conversation. The overlay's whole premise
        assumes single-user use; a shared personal workspace gets none of
        it, even for the owner."""
        fellow = register_user(email="fellow@test.dev", password=PASSWORD, name="Fellow")
        WorkspaceMember.objects.create(
            workspace=personal_workspace, user=fellow, role=WorkspaceMember.Role.VIEWER
        )
        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        make_memory(company, "shipped the migration")
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_source_workspace_actor_merely_has_membership_in_is_excluded(
        self, owner, personal_workspace
    ):
        """(d): a workspace the owner merely has membership in (not
        ownership of) is never a valid overlay source, even though
        for_actor()-style membership would normally grant access."""
        other_owner = register_user(email="other@test.dev", password=PASSWORD, name="Other")
        foreign_company = Workspace.objects.create(
            name="Foreign Co", owner=other_owner, type=Workspace.Type.COMPANY
        )
        WorkspaceMember.objects.create(
            workspace=foreign_company, user=owner, role=WorkspaceMember.Role.MEMBER
        )
        make_memory(foreign_company, "foreign work log")
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_own_workspace_is_never_its_own_overlay_source(self, owner, personal_workspace):
        make_memory(personal_workspace, "same-workspace work log")
        assert select_overlay_memories(actor=owner, workspace=personal_workspace) == []

    def test_multiple_owned_source_workspaces_all_contribute(self, owner, personal_workspace):
        company_a = Workspace.objects.create(name="A Co", owner=owner, type=Workspace.Type.COMPANY)
        company_b = Workspace.objects.create(name="B Co", owner=owner, type=Workspace.Type.COMPANY)
        memory_a = make_memory(company_a, "log from A")
        memory_b = make_memory(company_b, "log from B")
        result = select_overlay_memories(actor=owner, workspace=personal_workspace)
        assert {m.id for m, _ in result} == {memory_a.id, memory_b.id}


class TestOverlayBudget:
    def test_respects_max_memories(self, owner, personal_workspace):
        from memory.context import OVERLAY_MAX_MEMORIES

        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        for i in range(OVERLAY_MAX_MEMORIES + 3):
            make_memory(company, f"log entry number {i}")
        result = select_overlay_memories(actor=owner, workspace=personal_workspace)
        assert len(result) == OVERLAY_MAX_MEMORIES

    def test_respects_char_budget(self, owner, personal_workspace):
        from memory.context import MEMORY_CONTENT_MAX_CHARS, OVERLAY_CHAR_BUDGET

        company = Workspace.objects.create(name="Acme", owner=owner, type=Workspace.Type.COMPANY)
        # Per-memory cost is capped at MEMORY_CONTENT_MAX_CHARS (the same
        # rule select_context_memories uses) -- two entries each costing
        # roughly that cap together exceed OVERLAY_CHAR_BUDGET, so only the
        # first (by -updated_at, id ordering) fits.
        make_memory(company, "x" * MEMORY_CONTENT_MAX_CHARS, title="first")
        second = make_memory(company, "y" * MEMORY_CONTENT_MAX_CHARS, title="second")
        assert 2 * MEMORY_CONTENT_MAX_CHARS > OVERLAY_CHAR_BUDGET
        result = select_overlay_memories(actor=owner, workspace=personal_workspace)
        # Ordering is -updated_at, id -- the more-recently-updated "second"
        # entry is scanned first and fits; "first" no longer fits the
        # remaining budget and is skipped.
        assert len(result) == 1
        assert result[0][0].id == second.id


class TestOverlayAudit:
    def test_no_audit_row_when_overlay_contributes_nothing(self, owner, personal_workspace):
        before = AuditLog.objects.filter(action="memory.context_accessed").count()
        select_overlay_memories(actor=owner, workspace=personal_workspace)
        after = AuditLog.objects.filter(action="memory.context_accessed").count()
        assert after == before

    def test_one_audit_row_per_contributing_source_workspace(self, owner, personal_workspace):
        company_a = Workspace.objects.create(name="A Co", owner=owner, type=Workspace.Type.COMPANY)
        company_b = Workspace.objects.create(name="B Co", owner=owner, type=Workspace.Type.COMPANY)
        memory_a = make_memory(company_a, "log from A")
        memory_b = make_memory(company_b, "log from B")

        select_overlay_memories(actor=owner, workspace=personal_workspace)

        entries = AuditLog.objects.filter(action="memory.context_accessed", metadata__overlay=True)
        assert entries.count() == 2
        by_workspace = {entry.workspace_id: entry for entry in entries}
        assert by_workspace[company_a.id].metadata["memory_ids"] == [str(memory_a.id)]
        assert by_workspace[company_b.id].metadata["memory_ids"] == [str(memory_b.id)]
        for entry in entries:
            assert entry.metadata["target_workspace_id"] == str(personal_workspace.id)
