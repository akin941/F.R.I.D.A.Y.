import threading
from functools import partial

import pytest
from django.db import connection

from accounts.services import register_user
from assistant.models import Conversation, Message
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from tasks.models import Task
from tasks.services import (
    DraftAlreadyConfirmed,
    InvalidTransition,
    TaskPermissionDenied,
    cancel_task,
    complete_task,
    create_task,
    delete_task,
    edit_task,
    reopen_task,
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


def audit_actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


def make_task(workspace, actor, title="Buy milk"):
    return create_task(workspace=workspace, actor=actor, title=title)


class TestCreate:
    def test_born_open(self, workspace, owner):
        task = make_task(workspace, owner)
        assert task.status == Task.Status.OPEN
        assert task.created_by == owner
        assert "task.created" in audit_actions()

    def test_viewer_cannot_create(self, workspace, viewer):
        with pytest.raises(TaskPermissionDenied):
            create_task(workspace=workspace, actor=viewer, title="X")

    def test_member_can_create(self, workspace, member):
        task = create_task(workspace=workspace, actor=member, title="Member task")
        assert task.created_by == member


def make_draft_message(workspace, actor):
    conversation = Conversation.objects.create(workspace=workspace, created_by=actor)
    return Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="remind me to call mom",
        created_by=actor,
        intent=Message.Intent.TASK_DRAFT,
        intent_payload={"title": "Call mom", "due_date": None},
    )


class TestDraftConfirmation:
    """Phase 4 branch 4: `Task.source_message` is unique, so one draft
    message yields at most one task."""

    def test_links_the_source_message(self, workspace, owner):
        draft = make_draft_message(workspace, owner)
        task = create_task(
            workspace=workspace,
            actor=owner,
            title="Call mom",
            source=Task.Source.AI_SUGGESTED,
            source_message=draft,
        )
        assert task.source_message == draft
        assert draft.confirmed_task == task

    def test_second_task_for_the_same_draft_is_refused_without_audit(self, workspace, owner):
        draft = make_draft_message(workspace, owner)
        kwargs = {
            "workspace": workspace,
            "actor": owner,
            "title": "Call mom",
            "source": Task.Source.AI_SUGGESTED,
            "source_message": draft,
        }
        create_task(**kwargs)
        with pytest.raises(DraftAlreadyConfirmed):
            create_task(**kwargs)
        assert Task.objects.count() == 1
        assert audit_actions().count("task.created") == 1

    def test_tasks_without_a_source_message_never_collide(self, workspace, owner):
        make_task(workspace, owner, title="One")
        make_task(workspace, owner, title="Two")
        assert Task.objects.filter(source_message__isnull=True).count() == 2


class TestStateMachine:
    def test_complete_open(self, workspace, owner):
        task = make_task(workspace, owner)
        task = complete_task(task=task, actor=owner)
        assert task.status == Task.Status.DONE
        assert "task.completed" in audit_actions()

    def test_cancel_open(self, workspace, owner):
        task = make_task(workspace, owner)
        task = cancel_task(task=task, actor=owner)
        assert task.status == Task.Status.CANCELLED
        assert "task.cancelled" in audit_actions()

    def test_reopen_from_done(self, workspace, owner):
        task = complete_task(task=make_task(workspace, owner), actor=owner)
        task = reopen_task(task=task, actor=owner)
        assert task.status == Task.Status.OPEN
        assert "task.reopened" in audit_actions()

    def test_reopen_from_cancelled(self, workspace, owner):
        task = cancel_task(task=make_task(workspace, owner), actor=owner)
        task = reopen_task(task=task, actor=owner)
        assert task.status == Task.Status.OPEN

    def test_cancel_a_done_task_is_illegal(self, workspace, owner):
        task = complete_task(task=make_task(workspace, owner), actor=owner)
        with pytest.raises(InvalidTransition):
            cancel_task(task=task, actor=owner)

    def test_complete_a_cancelled_task_is_illegal(self, workspace, owner):
        task = cancel_task(task=make_task(workspace, owner), actor=owner)
        with pytest.raises(InvalidTransition):
            complete_task(task=task, actor=owner)

    def test_reopen_an_open_task_is_idempotent_noop(self, workspace, owner):
        task = make_task(workspace, owner)
        before = AuditLog.objects.count()
        reopen_task(task=task, actor=owner)
        assert AuditLog.objects.count() == before

    def test_complete_repeat_is_noop_without_audit(self, workspace, owner):
        task = complete_task(task=make_task(workspace, owner), actor=owner)
        before = AuditLog.objects.count()
        complete_task(task=task, actor=owner)
        assert AuditLog.objects.count() == before

    def test_cancel_repeat_is_noop_without_audit(self, workspace, owner):
        task = cancel_task(task=make_task(workspace, owner), actor=owner)
        before = AuditLog.objects.count()
        cancel_task(task=task, actor=owner)
        assert AuditLog.objects.count() == before


class TestEdit:
    def test_edit_updates_fields_regardless_of_status(self, workspace, owner):
        task = complete_task(task=make_task(workspace, owner), actor=owner)
        task = edit_task(task=task, actor=owner, title="Buy oat milk")
        assert task.title == "Buy oat milk"
        assert "task.updated" in audit_actions()

    def test_noop_edit_writes_no_audit(self, workspace, owner):
        task = make_task(workspace, owner)
        before = AuditLog.objects.count()
        edit_task(task=task, actor=owner, title=task.title)
        assert AuditLog.objects.count() == before

    def test_status_is_not_editable(self, workspace, owner):
        task = make_task(workspace, owner)
        edited = edit_task(task=task, actor=owner, title="Still open")
        assert edited.status == Task.Status.OPEN


class TestRolePolicy:
    def test_member_manages_own_task_only(self, workspace, owner, member):
        own = create_task(workspace=workspace, actor=member, title="Mine")
        edit_task(task=own, actor=member, title="Mine (edited)")
        complete_task(task=own, actor=member)

        owners_task = create_task(workspace=workspace, actor=owner, title="Owner's")
        with pytest.raises(TaskPermissionDenied):
            edit_task(task=owners_task, actor=member, title="Hijack")
        with pytest.raises(TaskPermissionDenied):
            complete_task(task=owners_task, actor=member)
        with pytest.raises(TaskPermissionDenied):
            delete_task(task=owners_task, actor=member, channel=CHANNEL_WEB)

    def test_viewer_can_do_nothing_mutating(self, workspace, owner, viewer):
        task = make_task(workspace, owner)
        for action in (complete_task, cancel_task, partial(delete_task, channel=CHANNEL_WEB)):
            with pytest.raises(TaskPermissionDenied):
                action(task=task, actor=viewer)
        with pytest.raises(TaskPermissionDenied):
            edit_task(task=task, actor=viewer, title="Nope")

    def test_owner_manages_any_task(self, workspace, owner, member):
        member_task = create_task(workspace=workspace, actor=member, title="Member's")
        complete_task(task=member_task, actor=owner)
        member_task.refresh_from_db()
        assert member_task.status == Task.Status.DONE

    def test_audit_metadata_never_contains_title_or_description(self, workspace, owner):
        task = create_task(
            workspace=workspace, actor=owner, title="Super secret plan", description="classified"
        )
        edit_task(task=task, actor=owner, description="still classified")
        complete_task(task=task, actor=owner)
        for entry in AuditLog.objects.all():
            blob = str(entry.metadata).lower()
            assert "secret" not in blob
            assert "classified" not in blob


class TestLockedScoping:
    def test_forged_instance_cannot_mutate_foreign_row(self, workspace, owner):
        stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="S")
        foreign_ws = Workspace.objects.get(owner=stranger)
        foreign = Task.objects.create(workspace=foreign_ws, title="Foreign", created_by=stranger)
        # Forge: an instance whose workspace attribute points at a workspace
        # the actor CAN access, but whose pk is the foreign row.
        forged = Task.objects.get(pk=foreign.pk)
        forged.workspace = workspace
        with pytest.raises(InvalidTransition):
            complete_task(task=forged, actor=owner)
        with pytest.raises(InvalidTransition):
            edit_task(task=forged, actor=owner, title="Hijacked")
        with pytest.raises(InvalidTransition):
            delete_task(task=forged, actor=owner, channel=CHANNEL_WEB)
        foreign.refresh_from_db()
        assert foreign.status == Task.Status.OPEN
        assert foreign.title == "Foreign"

    def test_transition_on_concurrently_deleted_row(self, workspace, owner):
        task = make_task(workspace, owner)
        stale = Task.objects.get(pk=task.pk)
        delete_task(task=task, actor=owner, channel=CHANNEL_WEB)
        with pytest.raises(InvalidTransition):
            complete_task(task=stale, actor=owner)


@pytest.mark.django_db(transaction=True)
def test_concurrent_complete_and_cancel_serialize():
    """Genuine two-connection concurrency test (MVP4 plan-review round-1 HIGH
    finding): two threads race to complete/cancel the same open task. The
    actor-scoped `select_for_update` lock in `_locked()` must serialize them
    -- whichever transaction commits first wins, and the other observes the
    post-lock committed state (no longer `open`) and raises
    InvalidTransition, never both succeeding against a stale `open` read."""
    owner = register_user(email="racer@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=owner)
    task = create_task(workspace=workspace, actor=owner, title="Race me")

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key, action):
        try:
            barrier.wait(timeout=5)
            row = Task.objects.get(pk=task.pk)
            try:
                action(task=row, actor=owner)
                results[key] = "succeeded"
            except InvalidTransition:
                results[key] = "invalid_transition"
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("complete", complete_task))
    t2 = threading.Thread(target=worker, args=("cancel", cancel_task))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"complete", "cancel"}
    assert set(results.values()) == {"succeeded", "invalid_transition"}
    task.refresh_from_db()
    assert task.status in (Task.Status.DONE, Task.Status.CANCELLED)


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirms_of_one_draft_create_exactly_one_task():
    """Two connections confirm the same draft at once: the unique
    constraint on `Task.source_message` lets exactly one INSERT commit; the
    other surfaces as DraftAlreadyConfirmed, never as a second task."""
    owner = register_user(email="draft-racer@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=owner)
    draft = make_draft_message(workspace, owner)

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            try:
                create_task(
                    workspace=workspace,
                    actor=owner,
                    title="Call mom",
                    source=Task.Source.AI_SUGGESTED,
                    source_message=draft,
                )
                results.append("created")
            except DraftAlreadyConfirmed:
                results.append("already_confirmed")
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    assert sorted(results) == ["already_confirmed", "created"]
    assert Task.objects.filter(source_message=draft).count() == 1
