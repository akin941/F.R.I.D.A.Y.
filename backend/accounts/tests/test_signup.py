import pytest
from rest_framework.test import APIClient

from accounts.models import User
from workspaces.models import Workspace, WorkspaceMember
from workspaces.services import ensure_personal_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def client(settings):
    settings.SIGNUP_ENABLED = True
    return APIClient()


def signup(client, email="bob@test.dev", name="Bob"):
    return client.post("/api/v1/auth/signup", {"email": email, "password": PASSWORD, "name": name})


def test_signup_creates_user_with_tokens(client):
    response = signup(client)
    assert response.status_code == 201
    assert response.data["user"]["email"] == "bob@test.dev"
    assert response.data["tokens"]["access"]
    assert User.objects.filter(email="bob@test.dev").exists()


def test_signup_creates_personal_workspace(client):
    signup(client)
    user = User.objects.get(email="bob@test.dev")
    workspace = Workspace.objects.get(owner=user)
    assert workspace.type == Workspace.Type.PERSONAL


def test_signup_creates_owner_membership(client):
    signup(client)
    user = User.objects.get(email="bob@test.dev")
    member = WorkspaceMember.objects.get(user=user)
    assert member.role == WorkspaceMember.Role.OWNER
    assert member.workspace.owner == user


def test_ensure_personal_workspace_is_idempotent(client):
    signup(client)
    user = User.objects.get(email="bob@test.dev")
    first = ensure_personal_workspace(user)
    second = ensure_personal_workspace(user)
    assert first.pk == second.pk
    assert Workspace.objects.filter(owner=user).count() == 1
    assert WorkspaceMember.objects.filter(user=user).count() == 1


def test_duplicate_email_rejected(client):
    assert signup(client).status_code == 201
    response = signup(client, name="Bob Again")
    assert response.status_code == 400
    assert User.objects.filter(email="bob@test.dev").count() == 1
