import pytest

from accounts.services import register_user
from audit.models import AuditLog
from memory.models import Memory, compute_fingerprint
from memory.services import (
    DuplicateMemory,
    InvalidTransition,
    MemoryPermissionDenied,
    approve_memory,
    create_memory,
    delete_memory,
    edit_memory,
    forget_memory,
    reject_memory,
    restore_memory,
)
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def member(workspace):
    user = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.MEMBER)
    return user


@pytest.fixture
def viewer(workspace):
    user = register_user(email="viewer@test.dev", password=PASSWORD, name="Viewer")
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.VIEWER)
    return user


def suggestion(workspace, content="Prefers dark mode."):
    return Memory.objects.create(
        workspace=workspace,
        memory_type=Memory.Type.PREFERENCE,
        status=Memory.Status.PENDING_APPROVAL,
        source=Memory.Source.AI_SUGGESTED,
        title="Dark mode",
        content=content,
        fingerprint=compute_fingerprint(content),
    )


def audit_actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


class TestCreate:
    def test_user_memory_is_born_approved(self, workspace, owner):
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        assert memory.status == Memory.Status.APPROVED
        assert memory.source == Memory.Source.USER_CREATED
        assert memory.created_by == owner
        assert "memory.created" in audit_actions()

    def test_duplicate_content_rejected(self, workspace, owner):
        kwargs = dict(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        create_memory(**kwargs)
        with pytest.raises(DuplicateMemory):
            create_memory(**{**kwargs, "content": "  drinks   COFFEE black. "})

    def test_viewer_cannot_create(self, workspace, viewer):
        with pytest.raises(MemoryPermissionDenied):
            create_memory(
                workspace=workspace,
                actor=viewer,
                title="X",
                content="Y",
                memory_type=Memory.Type.SEMANTIC,
                sensitivity=Memory.Sensitivity.NORMAL,
            )


class TestStateMachine:
    def test_approve_pending(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        assert row.status == Memory.Status.APPROVED
        assert "memory.approved" in audit_actions()

    def test_approve_applies_curated_fields_atomically(self, workspace, owner):
        row = approve_memory(
            memory=suggestion(workspace),
            actor=owner,
            title="Theme preference",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        row.refresh_from_db()
        assert row.status == Memory.Status.APPROVED
        assert row.title == "Theme preference"
        assert row.sensitivity == Memory.Sensitivity.PRIVATE

    def test_approve_repeat_is_noop_without_audit(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        before = AuditLog.objects.count()
        approve_memory(memory=row, actor=owner)
        assert AuditLog.objects.count() == before

    def test_reject_then_approve_is_illegal(self, workspace, owner):
        row = reject_memory(memory=suggestion(workspace), actor=owner)
        with pytest.raises(InvalidTransition):
            approve_memory(memory=row, actor=owner)

    def test_forget_and_restore_cycle(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row = forget_memory(memory=row, actor=owner)
        assert row.status == Memory.Status.ARCHIVED
        row = restore_memory(memory=row, actor=owner)
        assert row.status == Memory.Status.APPROVED
        actions = audit_actions()
        assert "memory.forgotten" in actions
        assert "memory.restored" in actions

    def test_forget_pending_is_illegal(self, workspace, owner):
        with pytest.raises(InvalidTransition):
            forget_memory(memory=suggestion(workspace), actor=owner)

    def test_restore_pending_is_illegal(self, workspace, owner):
        with pytest.raises(InvalidTransition):
            restore_memory(memory=suggestion(workspace), actor=owner)

    def test_hard_delete_only_never_approved(self, workspace, owner):
        pending = suggestion(workspace, content="pending one")
        delete_memory(memory=pending, actor=owner)
        assert not Memory.objects.filter(pk=pending.pk).exists()
        assert "memory.deleted" in audit_actions()

        approved = approve_memory(memory=suggestion(workspace, content="kept"), actor=owner)
        with pytest.raises(InvalidTransition):
            delete_memory(memory=approved, actor=owner)


class TestEdit:
    def test_edit_recomputes_fingerprint(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        old = row.fingerprint
        edit_memory(memory=row, actor=owner, content="Prefers light mode.")
        row.refresh_from_db()
        assert row.fingerprint != old
        assert "memory.updated" in audit_actions()

    def test_edit_to_duplicate_content_rejected(self, workspace, owner):
        approve_memory(memory=suggestion(workspace, content="first"), actor=owner)
        row = approve_memory(memory=suggestion(workspace, content="second"), actor=owner)
        with pytest.raises(DuplicateMemory):
            edit_memory(memory=row, actor=owner, content="FIRST")

    def test_edit_archived_is_illegal(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        forget_memory(memory=row, actor=owner)
        with pytest.raises(InvalidTransition):
            edit_memory(memory=row, actor=owner, title="New")

    def test_pending_rows_are_editable_before_approval(self, workspace, owner):
        row = suggestion(workspace)
        edit_memory(memory=row, actor=owner, content="Prefers dark mode everywhere.")
        row.refresh_from_db()
        assert row.status == Memory.Status.PENDING_APPROVAL
        assert row.content == "Prefers dark mode everywhere."

    def test_noop_edit_writes_no_audit(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        before = AuditLog.objects.count()
        edit_memory(memory=row, actor=owner, title=row.title)
        assert AuditLog.objects.count() == before


class TestRolePolicy:
    def test_member_curation_is_denied(self, workspace, member):
        row = suggestion(workspace)
        for action in (approve_memory, reject_memory):
            with pytest.raises(MemoryPermissionDenied):
                action(memory=row, actor=member)

    def test_member_manages_own_user_created_rows_only(self, workspace, owner, member):
        own = create_memory(
            workspace=workspace,
            actor=member,
            title="Mine",
            content="member's own note",
            memory_type=Memory.Type.SEMANTIC,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        edit_memory(memory=own, actor=member, title="Mine (edited)")
        forget_memory(memory=own, actor=member)

        owners_row = create_memory(
            workspace=workspace,
            actor=owner,
            title="Owner's",
            content="owner's note",
            memory_type=Memory.Type.SEMANTIC,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        with pytest.raises(MemoryPermissionDenied):
            edit_memory(memory=owners_row, actor=member, title="Hijack")
        with pytest.raises(MemoryPermissionDenied):
            forget_memory(memory=owners_row, actor=member)

    def test_member_cannot_manage_ai_suggested_even_after_approval(self, workspace, owner, member):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        with pytest.raises(MemoryPermissionDenied):
            edit_memory(memory=row, actor=member, title="Nope")
        with pytest.raises(MemoryPermissionDenied):
            forget_memory(memory=row, actor=member)

    def test_member_cannot_hard_delete(self, workspace, member):
        row = suggestion(workspace)
        with pytest.raises(MemoryPermissionDenied):
            delete_memory(memory=row, actor=member)

    def test_viewer_can_do_nothing_mutating(self, workspace, owner, viewer):
        row = suggestion(workspace)
        for action in (approve_memory, reject_memory, delete_memory):
            with pytest.raises(MemoryPermissionDenied):
                action(memory=row, actor=viewer)
        approved = approve_memory(memory=row, actor=owner)
        for action in (edit_memory, forget_memory):
            with pytest.raises(MemoryPermissionDenied):
                action(memory=approved, actor=viewer)

    def test_audit_metadata_never_contains_title_or_content(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace, content="Secret content."), actor=owner)
        edit_memory(memory=row, actor=owner, content="Even more secret.")
        for entry in AuditLog.objects.all():
            blob = str(entry.metadata).lower()
            assert "secret" not in blob
            assert "dark mode" not in blob


class TestConcurrencySafety:
    """Every mutating service re-fetches under select_for_update, so a stale
    Python instance can never bypass the state machine (Codex round-1
    blocker). Simulated with two instances of the same row: the second call
    must see the first call's committed state, not its own stale snapshot."""

    def test_stale_instance_cannot_delete_after_approval(self, workspace, owner):
        row = suggestion(workspace)
        stale = Memory.objects.get(pk=row.pk)
        approve_memory(memory=row, actor=owner)
        with pytest.raises(InvalidTransition):
            delete_memory(memory=stale, actor=owner)
        assert Memory.objects.filter(pk=row.pk).exists()

    def test_stale_instance_cannot_double_transition(self, workspace, owner):
        row = suggestion(workspace)
        stale = Memory.objects.get(pk=row.pk)
        approve_memory(memory=row, actor=owner)
        with pytest.raises(InvalidTransition):
            reject_memory(memory=stale, actor=owner)
        row.refresh_from_db()
        assert row.status == Memory.Status.APPROVED

    def test_transition_on_concurrently_deleted_row(self, workspace, owner):
        row = suggestion(workspace)
        stale = Memory.objects.get(pk=row.pk)
        delete_memory(memory=row, actor=owner)
        with pytest.raises(InvalidTransition):
            approve_memory(memory=stale, actor=owner)


class TestIdempotentRepeats:
    def test_reject_repeat_is_noop_without_audit(self, workspace, owner):
        row = reject_memory(memory=suggestion(workspace), actor=owner)
        before = AuditLog.objects.count()
        reject_memory(memory=row, actor=owner)
        assert AuditLog.objects.count() == before

    def test_forget_repeat_is_noop_without_audit(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row = forget_memory(memory=row, actor=owner)
        before = AuditLog.objects.count()
        forget_memory(memory=row, actor=owner)
        assert AuditLog.objects.count() == before

    def test_restore_repeat_is_noop_without_audit(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        before = AuditLog.objects.count()
        restore_memory(memory=row, actor=owner)  # already approved
        assert AuditLog.objects.count() == before


class TestReadAuditChunking:
    def test_large_reads_chunk_but_keep_every_id(self, workspace, owner, monkeypatch):
        from memory import services

        monkeypatch.setattr(services, "_AUDIT_ID_CAP", 2)
        rows = [suggestion(workspace, content=f"row {i}") for i in range(5)]
        services.record_read(actor=owner, workspace=workspace, memories=rows)
        entries = list(AuditLog.objects.filter(action="memory.read").order_by("created_at"))
        assert len(entries) == 3
        seen = [mid for e in entries for mid in e.metadata["memory_ids"]]
        assert seen == [str(r.id) for r in rows]
        assert all(e.metadata["count"] == 5 for e in entries)
        assert [e.metadata["chunk"] for e in entries] == [1, 2, 3]


class TestLockedScoping:
    """Codex round-2 High: the lock query itself must be actor-scoped so a
    forged/stale instance carrying a foreign row's pk can never reach the
    mutation path."""

    def test_forged_instance_cannot_mutate_foreign_row(self, workspace, owner):
        stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="S")
        foreign_ws = Workspace.objects.get(owner=stranger)
        foreign = Memory.objects.create(
            workspace=foreign_ws,
            memory_type=Memory.Type.PREFERENCE,
            status=Memory.Status.PENDING_APPROVAL,
            source=Memory.Source.AI_SUGGESTED,
            title="Foreign",
            content="foreign secret",
            fingerprint=compute_fingerprint("foreign secret"),
        )
        # Forge: an instance whose workspace attribute points at a workspace
        # the actor CAN access, but whose pk is the foreign row.
        forged = Memory.objects.get(pk=foreign.pk)
        forged.workspace = workspace
        with pytest.raises(InvalidTransition):
            approve_memory(memory=forged, actor=owner)
        with pytest.raises(InvalidTransition):
            edit_memory(memory=forged, actor=owner, title="Hijacked")
        with pytest.raises(InvalidTransition):
            delete_memory(memory=forged, actor=owner)
        foreign.refresh_from_db()
        assert foreign.status == Memory.Status.PENDING_APPROVAL
        assert foreign.title == "Foreign"


class TestFingerprintRaceTranslation:
    """Codex round-2 Medium: a fingerprint race between two different rows is
    caught by the unique constraint, and edit/approve must surface it as
    DuplicateMemory (400), never an unhandled IntegrityError (500)."""

    def test_edit_integrity_error_maps_to_duplicate(self, workspace, owner, monkeypatch):
        from memory import services

        approve_memory(memory=suggestion(workspace, content="first"), actor=owner)
        row = approve_memory(memory=suggestion(workspace, content="second"), actor=owner)
        # Simulate the losing side of the race: pre-check passes (no-op),
        # constraint fires at save time.
        monkeypatch.setattr(services, "_check_fingerprint_free", lambda *a, **k: None)
        with pytest.raises(DuplicateMemory):
            edit_memory(memory=row, actor=owner, content="FIRST")

    def test_approve_integrity_error_maps_to_duplicate(self, workspace, owner, monkeypatch):
        from memory import services

        approve_memory(memory=suggestion(workspace, content="first"), actor=owner)
        row = suggestion(workspace, content="second")
        monkeypatch.setattr(services, "_check_fingerprint_free", lambda *a, **k: None)
        with pytest.raises(DuplicateMemory):
            approve_memory(memory=row, actor=owner, content="FIRST")
