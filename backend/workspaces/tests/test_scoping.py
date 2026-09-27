"""Workspace scoping matrix — the architectural core of MVP 1.

Uses Workspace.for_actor and the WorkspaceScopedQuerySet via WorkspaceMember
(a real model with a workspace FK), standing in for the scoped business models
that arrive in later branches (conversations, notes, ...).
"""

import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def bob():
    return register_user(email="bob@test.dev", password=PASSWORD, name="Bob")


def test_workspace_list_returns_only_memberships(alice, bob, web_client):
    response = web_client(alice).get("/api/v1/workspaces")
    assert response.status_code == 200
    names = [w["name"] for w in response.data]
    assert len(names) == 1
    assert "Alice" in names[0]
    assert all(w["role"] == "owner" for w in response.data)


def test_user_cannot_see_other_users_workspace(alice, bob, web_client):
    bob_ws = Workspace.objects.get(owner=bob)
    ids = [w["id"] for w in web_client(alice).get("/api/v1/workspaces").data]
    assert str(bob_ws.id) not in ids


def test_for_actor_filters_by_membership(alice, bob):
    visible = Workspace.objects.for_actor(alice)
    assert visible.count() == 1
    assert visible.first().owner == alice
    # Scoped queryset on a workspace-FK model behaves the same.
    members_visible = WorkspaceMember.objects.for_actor(alice)
    assert members_visible.count() == 1
    assert members_visible.first().user == alice


def test_for_actor_narrows_with_workspace_argument(alice, bob):
    alice_ws = Workspace.objects.get(owner=alice)
    bob_ws = Workspace.objects.get(owner=bob)
    assert WorkspaceMember.objects.for_actor(alice, workspace=alice_ws).count() == 1
    # A workspace the actor is not a member of yields nothing, even if passed explicitly.
    assert WorkspaceMember.objects.for_actor(alice, workspace=bob_ws).count() == 0


def test_for_actor_rejects_anonymous(alice):
    assert Workspace.objects.for_actor(None).count() == 0
    assert WorkspaceMember.objects.for_actor(None).count() == 0


def test_workspace_list_requires_auth():
    assert APIClient().get("/api/v1/workspaces").status_code == 401


def test_shared_workspace_visible_to_both(alice, bob, web_client):
    """Forward-looking: membership (not ownership) drives visibility."""
    alice_ws = Workspace.objects.get(owner=alice)
    WorkspaceMember.objects.create(workspace=alice_ws, user=bob, role=WorkspaceMember.Role.MEMBER)
    ids = [w["id"] for w in web_client(bob).get("/api/v1/workspaces").data]
    assert str(alice_ws.id) in ids
