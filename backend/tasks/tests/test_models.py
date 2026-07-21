import pytest

from accounts.services import register_user
from tasks.models import Task
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def test_defaults(workspace):
    task = Task.objects.create(workspace=workspace, title="Buy milk")
    assert task.status == Task.Status.OPEN
    assert task.priority == Task.Priority.NORMAL
    assert task.description == ""
    assert task.due_date is None
    assert str(task) == f"task {task.id} (open)"


def test_workspace_scoping(owner, workspace):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")
    other_workspace = Workspace.objects.get(owner=stranger)
    Task.objects.create(workspace=workspace, title="Mine")
    Task.objects.create(workspace=other_workspace, title="Theirs")

    visible = Task.objects.for_actor(owner)
    assert visible.count() == 1
    assert visible.first().title == "Mine"
