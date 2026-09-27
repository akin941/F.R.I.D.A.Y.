"""Phase 5 branch 1 (brief D13, ADR-008 decision 4): a completed or
cancelled task leaves a system-generated `work_log` memory -- bookkeeping
only (outcome, date, id, title; never the description), audited as
`memory.system_recorded`, best-effort."""

from datetime import date
from unittest import mock

import pytest

from accounts.services import register_user
from assistant.models import Conversation, Message
from audit.models import AuditLog
from memory.models import Memory
from tasks.models import Task
from tasks.services import cancel_task, complete_task, create_task, reopen_task
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
TODAY = date(2026, 9, 27)


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture(autouse=True)
def _fixed_day():
    with mock.patch("tasks.services.timezone.localdate", return_value=TODAY):
        yield


def work_logs(workspace):
    return list(
        Memory.objects.filter(workspace=workspace, memory_type=Memory.Type.WORK_LOG).order_by(
            "created_at"
        )
    )


def make_task(workspace, owner, title="File the quarterly VAT return"):
    return create_task(
        workspace=workspace,
        actor=owner,
        title=title,
        description="SECRET-DESCRIPTION-TEXT",
    )


@pytest.mark.parametrize(
    "transition,outcome", [(complete_task, "completed"), (cancel_task, "cancelled")]
)
def test_outcome_records_a_bookkeeping_work_log(workspace, owner, transition, outcome):
    task = make_task(workspace, owner)
    transition(task=task, actor=owner)
    [row] = work_logs(workspace)
    assert row.source == Memory.Source.SYSTEM_GENERATED
    assert row.status == Memory.Status.APPROVED
    assert row.sensitivity == Memory.Sensitivity.NORMAL
    assert row.title == f"Task {outcome}: File the quarterly VAT return"
    assert row.content == (
        f"Task {outcome} on 2026-09-27: File the quarterly VAT return\nTask id: {task.id}."
    )
    assert "SECRET-DESCRIPTION-TEXT" not in row.content
    audit = AuditLog.objects.get(action="memory.system_recorded", resource_id=str(row.id))
    assert audit.actor is None
    assert audit.actor_type == AuditLog.ActorType.SYSTEM


def test_creating_editing_and_reopening_record_nothing(workspace, owner):
    task = make_task(workspace, owner)
    assert work_logs(workspace) == []
    complete_task(task=task, actor=owner)
    reopen_task(task=task, actor=owner)
    assert len(work_logs(workspace)) == 1


def test_idempotent_repeat_records_nothing_more(workspace, owner):
    task = make_task(workspace, owner)
    complete_task(task=task, actor=owner)
    complete_task(task=task, actor=owner)
    assert len(work_logs(workspace)) == 1


def test_same_day_repeat_of_the_same_outcome_is_one_row(workspace, owner):
    task = make_task(workspace, owner)
    complete_task(task=task, actor=owner)
    reopen_task(task=task, actor=owner)
    complete_task(task=task, actor=owner)
    assert len(work_logs(workspace)) == 1
    assert Task.objects.get(pk=task.pk).status == Task.Status.DONE


def test_the_same_outcome_on_another_day_is_a_new_row(workspace, owner):
    task = make_task(workspace, owner)
    complete_task(task=task, actor=owner)
    reopen_task(task=task, actor=owner)
    with mock.patch("tasks.services.timezone.localdate", return_value=date(2026, 9, 28)):
        complete_task(task=task, actor=owner)
    assert len(work_logs(workspace)) == 2


def test_two_tasks_with_the_same_title_are_two_rows(workspace, owner):
    first = make_task(workspace, owner, title="Call the bank")
    second = make_task(workspace, owner, title="Call the bank")
    complete_task(task=first, actor=owner)
    complete_task(task=second, actor=owner)
    assert len(work_logs(workspace)) == 2


def _task_from_draft(workspace, owner, *, private):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    message = Message.objects.create(
        workspace=workspace,
        conversation=conversation,
        role=Message.Role.USER,
        content="remind me to call the clinic",
        intent=Message.Intent.TASK_DRAFT,
        intent_payload={"title": "Call the clinic", "due_date": None},
    )
    task = create_task(
        workspace=workspace,
        actor=owner,
        title="Call the clinic",
        source=Task.Source.AI_SUGGESTED,
        source_message=message,
    )
    if private:
        Conversation.objects.filter(pk=conversation.pk).update(private=True)
    return task


@pytest.mark.parametrize(
    "private,sensitivity",
    [(True, Memory.Sensitivity.PRIVATE), (False, Memory.Sensitivity.NORMAL)],
)
def test_task_from_a_private_conversation_logs_privately(workspace, owner, private, sensitivity):
    task = _task_from_draft(workspace, owner, private=private)
    complete_task(task=task, actor=owner)
    [row] = work_logs(workspace)
    assert row.sensitivity == sensitivity


def test_a_work_log_failure_never_undoes_the_transition(workspace, owner):
    task = make_task(workspace, owner)
    with mock.patch("tasks.services.record_work_log", side_effect=RuntimeError("boom")):
        complete_task(task=task, actor=owner)
    task.refresh_from_db()
    assert task.status == Task.Status.DONE
    assert AuditLog.objects.filter(action="task.completed").count() == 1
    assert work_logs(workspace) == []


def test_work_log_lands_in_the_tasks_own_workspace(owner):
    other_owner = register_user(email="other@test.dev", password=PASSWORD, name="Other")
    other_workspace = Workspace.objects.get(owner=other_owner)
    task = make_task(other_workspace, other_owner)
    complete_task(task=task, actor=other_owner)
    assert len(work_logs(other_workspace)) == 1
    assert work_logs(Workspace.objects.get(owner=owner)) == []
