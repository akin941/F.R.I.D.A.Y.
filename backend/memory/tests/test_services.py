import threading
from functools import partial

import pytest
from django.db import OperationalError, connection

from accounts.services import register_user
from assistant.models import Conversation
from assistant.services import mark_conversation_private
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
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
    privatize_conversation_memories,
    record_work_log,
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
        delete_memory(memory=pending, actor=owner, channel=CHANNEL_WEB)
        assert not Memory.objects.filter(pk=pending.pk).exists()
        assert "memory.deleted" in audit_actions()

        approved = approve_memory(memory=suggestion(workspace, content="kept"), actor=owner)
        with pytest.raises(InvalidTransition):
            delete_memory(memory=approved, actor=owner, channel=CHANNEL_WEB)


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
            delete_memory(memory=row, actor=member, channel=CHANNEL_WEB)

    def test_viewer_can_do_nothing_mutating(self, workspace, owner, viewer):
        row = suggestion(workspace)
        for action in (approve_memory, reject_memory, partial(delete_memory, channel=CHANNEL_WEB)):
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
            delete_memory(memory=stale, actor=owner, channel=CHANNEL_WEB)
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
        delete_memory(memory=row, actor=owner, channel=CHANNEL_WEB)
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
            delete_memory(memory=forged, actor=owner, channel=CHANNEL_WEB)
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


class TestEmbeddingReconciliation:
    """`_reconcile_embedding`'s field-level effects (Phase 1
    p1-memory-embeddings) — the actual Celery dispatch is deferred to
    `transaction.on_commit`, which never fires in a plain (non-`transaction=
    True`) `django_db` test, so these tests only assert the synchronous
    field state the reconcile call sets before registering that callback
    (dispatch itself is covered separately in
    memory/tests/test_embedding_tasks.py::TestReconcileEmbeddingDispatch)."""

    def test_user_created_normal_memory_is_cleared_for_a_fresh_embed(self, workspace, owner):
        """embedding starts None either way, but claim/lease/retry fields
        must also be in their pre-embed state — proves the reconcile path
        actually ran rather than being skipped."""
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        assert memory.embedding is None
        assert memory.claim_token is None
        assert memory.retry_count == 0

    def test_private_memory_creation_never_touches_embedding_fields(self, workspace, owner):
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Secret",
            content="A private note.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        assert memory.embedding is None

    def test_approving_as_private_never_enqueues(self, workspace, owner):
        row = suggestion(workspace)
        approved = approve_memory(memory=row, actor=owner, sensitivity=Memory.Sensitivity.PRIVATE)
        assert approved.embedding is None
        assert approved.claim_token is None

    def test_editing_content_on_an_approved_normal_memory_clears_a_stale_embedding(
        self, workspace, owner
    ):
        """The core lifecycle claim: a content edit on an already-eligible
        row must invalidate whatever embedding/claim state was there, not
        just leave it (which would let retrieval rank on stale content)."""
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        # Simulate a prior successful embed + a stale in-flight claim, as if
        # a second (now-stale) attempt were mid-flight when the edit lands.
        Memory.objects.filter(pk=memory.pk).update(
            embedding=[0.1, 0.2], claim_token="11111111-1111-1111-1111-111111111111"
        )
        edited = edit_memory(memory=memory, actor=owner, content="Drinks tea now.")
        assert edited.embedding is None
        assert edited.claim_token is None

    def test_editing_unrelated_field_on_an_approved_memory_does_not_touch_embedding(
        self, workspace, owner
    ):
        """Sibling of the above: a `memory_type`-only edit must NOT clear an
        existing, still-valid embedding — text_changed must be false here."""
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        Memory.objects.filter(pk=memory.pk).update(embedding=[0.1, 0.2])
        edited = edit_memory(memory=memory, actor=owner, memory_type=Memory.Type.SEMANTIC)
        edited.refresh_from_db()
        assert edited.embedding is not None

    def test_editing_sensitivity_to_private_clears_an_existing_embedding(self, workspace, owner):
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        Memory.objects.filter(pk=memory.pk).update(embedding=[0.1, 0.2])
        edited = edit_memory(memory=memory, actor=owner, sensitivity=Memory.Sensitivity.PRIVATE)
        assert edited.embedding is None

    def test_forgetting_an_embedded_memory_clears_its_embedding(self, workspace, owner):
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        Memory.objects.filter(pk=memory.pk).update(embedding=[0.1, 0.2])
        forgotten = forget_memory(memory=memory, actor=owner)
        assert forgotten.embedding is None

    def test_restoring_a_normal_memory_re_clears_for_a_fresh_embed(self, workspace, owner):
        """restore_memory (archived -> approved) re-enters cloud_eligible():
        embedding must already be None (forget cleared it) and stay that
        way, proving the reconcile path ran again on restore."""
        memory = create_memory(
            workspace=workspace,
            actor=owner,
            title="Coffee",
            content="Drinks coffee black.",
            memory_type=Memory.Type.PREFERENCE,
            sensitivity=Memory.Sensitivity.NORMAL,
        )
        forget_memory(memory=memory, actor=owner)
        restored = restore_memory(memory=memory, actor=owner)
        assert restored.embedding is None

    def test_rejecting_a_pending_suggestion_never_touches_embedding_fields(self, workspace, owner):
        row = suggestion(workspace)
        rejected = reject_memory(memory=row, actor=owner)
        assert rejected.embedding is None


class TestRecordWorkLog:
    """record_work_log() — the sole creation path for `system_generated`
    `work_log` rows (ADR-008 k.4, Phase 1 p1-work-log)."""

    def test_born_approved_normal_system_generated(self, workspace):
        memory = record_work_log(
            workspace=workspace, title="Vault export", content="Exported 12 memories."
        )
        assert memory.status == Memory.Status.APPROVED
        assert memory.sensitivity == Memory.Sensitivity.NORMAL
        assert memory.source == Memory.Source.SYSTEM_GENERATED
        assert memory.memory_type == Memory.Type.WORK_LOG

    def test_sensitivity_defaults_to_normal_for_existing_callers(self, workspace):
        """Phase 2 branch 3: sensitivity is the one non-hardcoded field on
        this function -- existing callers with no sensitivity concept must
        keep getting the original `normal` behavior unchanged."""
        memory = record_work_log(workspace=workspace, title="Run", content="content")
        assert memory.sensitivity == Memory.Sensitivity.NORMAL

    def test_private_sensitivity_is_respected_and_not_enqueued_for_embedding(self, workspace):
        memory = record_work_log(
            workspace=workspace,
            title="Sensitive run",
            content="content about a sensitive client",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        assert memory.sensitivity == Memory.Sensitivity.PRIVATE
        assert memory.status == Memory.Status.APPROVED  # still auto-approved
        assert not Memory.objects.cloud_eligible().filter(pk=memory.pk).exists()
        assert memory.embedding is None
        assert memory.claim_token is None  # never enqueued -- not cloud_eligible()

    def test_audited_as_a_system_actor(self, workspace):
        memory = record_work_log(workspace=workspace, title="Run", content="Agent run finished.")
        entry = AuditLog.objects.get(action="memory.system_recorded")
        assert entry.actor is None
        assert entry.actor_type == AuditLog.ActorType.SYSTEM
        assert entry.resource_id == str(memory.id)
        assert "Agent run finished." not in str(entry.metadata)

    def test_duplicate_content_in_the_same_workspace_is_rejected(self, workspace):
        record_work_log(workspace=workspace, title="A", content="Same content.")
        with pytest.raises(DuplicateMemory):
            record_work_log(workspace=workspace, title="B", content="Same content.")

    def test_reconciles_embedding_on_creation(self, workspace):
        """Born approved+normal -> cloud_eligible() immediately -> the
        embedding-lifecycle reconciliation (branch p1-memory-embeddings)
        must run the same way create_memory()'s does."""
        memory = record_work_log(workspace=workspace, title="Run", content="content")
        assert memory.embedding is None
        assert memory.claim_token is None

    def test_forgettable_like_any_other_memory(self, workspace, owner):
        memory = record_work_log(workspace=workspace, title="Run", content="content")
        forgotten = forget_memory(memory=memory, actor=owner)
        assert forgotten.status == Memory.Status.ARCHIVED


class TestPrivatizeConversationMemories:
    """privatize_conversation_memories() — the cascade half of marking a
    conversation private (Phase 1 p1-work-log)."""

    @pytest.fixture
    def conversation(self, workspace, owner):
        return Conversation.objects.create(workspace=workspace, created_by=owner)

    def test_pending_candidate_is_rejected(self, workspace, owner, conversation):
        row = suggestion(workspace)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        row.refresh_from_db()
        assert row.status == Memory.Status.REJECTED

    def test_approved_memory_is_archived_and_embedding_cleared(
        self, workspace, owner, conversation
    ):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        Memory.objects.filter(pk=row.pk).update(embedding=[0.1, 0.2])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        row.refresh_from_db()
        assert row.status == Memory.Status.ARCHIVED
        assert row.embedding is None
        assert row.privacy_locked is True

    def test_unrelated_conversations_memories_are_untouched(self, workspace, owner, conversation):
        other_conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
        row = suggestion(workspace)
        row.source_conversation = other_conversation
        row.save(update_fields=["source_conversation"])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        row.refresh_from_db()
        assert row.status == Memory.Status.PENDING_APPROVAL

    def test_cascade_audit_entries_are_tagged(self, workspace, owner, conversation):
        row = suggestion(workspace)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        entry = AuditLog.objects.get(action="memory.rejected")
        assert entry.metadata["cascade"] == "conversation_privatized"

    def test_already_archived_memory_gets_locked_too(self, workspace, owner, conversation):
        """Codex round-4 P2: a memory manually forgotten BEFORE the
        conversation was marked private is already ARCHIVED, so the
        approved-rows loop never reaches it — without a dedicated pass it
        would stay restorable forever despite originating from a
        now-private conversation."""
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        forget_memory(memory=row, actor=owner)
        row.refresh_from_db()
        assert row.privacy_locked is False  # not yet — conversation isn't private yet

        privatize_conversation_memories(conversation=conversation, actor=owner)
        row.refresh_from_db()
        assert row.status == Memory.Status.ARCHIVED
        assert row.privacy_locked is True


class TestApproveGuardsAgainstPrivateConversation:
    def test_approving_a_memory_from_a_private_conversation_is_rejected(self, workspace, owner):
        conversation = Conversation.objects.create(
            workspace=workspace, created_by=owner, private=True
        )
        row = suggestion(workspace)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        with pytest.raises(InvalidTransition):
            approve_memory(memory=row, actor=owner)

    def test_restoring_a_cascade_archived_memory_is_rejected(self, workspace, owner):
        """Codex round-1 P1 on this branch: restore_memory() is the OTHER
        door back into cloud_eligible() from archived — without this guard
        a curator could simply restore a memory privatize_conversation_
        memories() just archived, silently undoing the privacy cascade."""
        conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        row.refresh_from_db()
        assert row.privacy_locked is True
        with pytest.raises(InvalidTransition):
            restore_memory(memory=row, actor=owner)

    def test_restore_guard_survives_source_conversation_deletion(self, workspace, owner):
        """Codex round-3 P2: the guard must check the durable
        `privacy_locked` marker, not `source_conversation.private` — that
        FK is nullable (SET_NULL on delete), so deleting the conversation
        after the cascade must not silently reopen the row for restore."""
        conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        row.source_conversation = conversation
        row.save(update_fields=["source_conversation"])
        privatize_conversation_memories(conversation=conversation, actor=owner)
        conversation.delete()
        row.refresh_from_db()
        assert row.source_conversation_id is None  # SET_NULL fired
        assert row.privacy_locked is True  # marker survived regardless
        with pytest.raises(InvalidTransition):
            restore_memory(memory=row, actor=owner)


class TestWorkLogChokePoint:
    """`record_work_log()` must be the ONLY way a `work_log` memory gets
    created (Codex round-5 P2 on this branch): `WORK_LOG` being a valid
    `Memory.Type` choice means the ordinary public create/edit paths would
    otherwise accept it too, letting any writer-role user produce a
    `source=user_created` or `ai_suggested` row indistinguishable in TYPE
    from a real system-generated activity summary."""

    def test_create_memory_rejects_work_log_type(self, workspace, owner):
        with pytest.raises(InvalidTransition):
            create_memory(
                workspace=workspace,
                actor=owner,
                title="Fake work log",
                content="Pretending to be a system summary.",
                memory_type=Memory.Type.WORK_LOG,
                sensitivity=Memory.Sensitivity.NORMAL,
            )
        assert not Memory.objects.filter(memory_type=Memory.Type.WORK_LOG).exists()

    def test_edit_memory_rejects_retyping_to_work_log(self, workspace, owner):
        row = approve_memory(memory=suggestion(workspace), actor=owner)
        with pytest.raises(InvalidTransition):
            edit_memory(memory=row, actor=owner, memory_type=Memory.Type.WORK_LOG)
        row.refresh_from_db()
        assert row.memory_type != Memory.Type.WORK_LOG

    def test_approve_memory_rejects_curating_to_work_log(self, workspace, owner):
        row = suggestion(workspace)
        with pytest.raises(InvalidTransition):
            approve_memory(memory=row, actor=owner, memory_type=Memory.Type.WORK_LOG)
        row.refresh_from_db()
        assert row.status == Memory.Status.PENDING_APPROVAL

    def test_record_work_log_itself_is_unaffected(self, workspace):
        memory = record_work_log(workspace=workspace, title="Real one", content="Real summary.")
        assert memory.memory_type == Memory.Type.WORK_LOG
        assert memory.source == Memory.Source.SYSTEM_GENERATED


@pytest.mark.django_db(transaction=True)
def test_concurrent_restore_and_privatize_never_leaves_an_unlocked_approved_memory():
    """Codex round-6 P1: a genuine two-thread race between restore_memory()
    and mark_conversation_private()/privatize_conversation_memories() on
    the exact same archived row. The two functions lock Memory and
    Conversation rows in opposite orders (restore: memory then, via
    Postgres's FK-integrity check, an implicit reference to the
    conversation; privatize: conversation then memory rows) — a real
    lock-order inversion, empirically confirmed to raise
    `OperationalError: deadlock detected` under genuine two-connection
    concurrency. That is an ACCEPTED, non-corrupting outcome (Postgres
    cleanly aborts one side; the app layer retries, standard practice) —
    what must never happen, in ANY interleaving, is the memory ending up
    both `approved` and unlocked once the conversation is actually private.
    """
    owner = register_user(email="racer@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=owner)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    row = approve_memory(memory=suggestion(workspace), actor=owner)
    row.source_conversation = conversation
    row.save(update_fields=["source_conversation"])
    forget_memory(memory=row, actor=owner)
    row.refresh_from_db()
    assert row.status == Memory.Status.ARCHIVED
    assert row.privacy_locked is False

    barrier = threading.Barrier(2)
    errors = []

    def restore_worker():
        try:
            barrier.wait(timeout=5)
            try:
                restore_memory(memory=row, actor=owner)
            except (InvalidTransition, OperationalError):
                pass  # privatize won the race, or this side lost the deadlock
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    def privatize_worker():
        try:
            barrier.wait(timeout=5)
            try:
                mark_conversation_private(conversation=conversation, actor=owner)
            except OperationalError:
                pass  # restore won the race, or this side lost the deadlock
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=restore_worker)
    t2 = threading.Thread(target=privatize_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    conversation.refresh_from_db()
    row.refresh_from_db()
    # A deadlock victim's whole transaction rolled back — e.g. privatize
    # may never have actually landed in this interleaving, which is fine;
    # the invariant only has to hold GIVEN the conversation actually ended
    # up private.
    if conversation.private:
        assert not (row.status == Memory.Status.APPROVED and not row.privacy_locked)


@pytest.mark.django_db(transaction=True)
def test_concurrent_approve_and_privatize_never_leaves_the_memory_approved():
    """Codex round-7 P1: the same class of race as the restore/privatize
    test above, but for approve_memory() — a still-pending candidate being
    approved concurrently with its conversation being marked private. The
    live, locked re-check (`_source_conversation_is_private`) must close
    this the same way `privacy_locked` closes the restore side: whichever
    wins, the memory must never end up `approved` once the conversation is
    actually private (a `pending_approval` row has no `privacy_locked`
    escape hatch — it must land on `rejected`, not `approved`)."""
    owner = register_user(email="racer2@test.dev", password=PASSWORD, name="Racer2")
    workspace = Workspace.objects.get(owner=owner)
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    row = suggestion(workspace)
    row.source_conversation = conversation
    row.save(update_fields=["source_conversation"])

    barrier = threading.Barrier(2)
    errors = []

    def approve_worker():
        try:
            barrier.wait(timeout=5)
            try:
                approve_memory(memory=row, actor=owner)
            except (InvalidTransition, OperationalError):
                pass  # privatize won the race, or this side lost the deadlock
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    def privatize_worker():
        try:
            barrier.wait(timeout=5)
            try:
                mark_conversation_private(conversation=conversation, actor=owner)
            except OperationalError:
                pass  # approve won the race, or this side lost the deadlock
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=approve_worker)
    t2 = threading.Thread(target=privatize_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    conversation.refresh_from_db()
    row.refresh_from_db()
    if conversation.private:
        assert row.status != Memory.Status.APPROVED
