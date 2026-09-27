"""`GET /api/v1/activity/summary` (Phase 4 branch 3): role x endpoint,
channel refusal, and workspace isolation of every count."""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from agents.models import Agent
from approvals.models import ApprovalRequest
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

URL = "/api/v1/activity/summary"
PASSWORD = "Str0ngDevPass!42"
ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def stranger():
    return register_user(email="stranger@test.dev", password=PASSWORD, name="Stranger")


@pytest.fixture
def stranger_workspace(stranger):
    return Workspace.objects.get(owner=stranger)


def seed(workspace, user):
    """2 fresh pending approvals (+1 stale, +1 decided), 1 live agent
    (+1 finished), 1 live discovery job + 1 live implementation job (+1 of
    each terminal)."""
    for _ in range(2):
        ApprovalRequest.objects.create(workspace=workspace, tool_name="t", requested_by=user)
    stale = ApprovalRequest.objects.create(workspace=workspace, tool_name="t", requested_by=user)
    ApprovalRequest.objects.filter(id=stale.id).update(
        created_at=stale.created_at - timedelta(days=365)
    )
    ApprovalRequest.objects.create(
        workspace=workspace, tool_name="t", requested_by=user, status="approved"
    )
    Agent.objects.create(workspace=workspace, goal="live", created_by=user, status="running")
    Agent.objects.create(workspace=workspace, goal="done", created_by=user, status="completed")
    repo = HarnessRepo.objects.create(
        workspace=workspace, name=f"repo-{workspace.id}", git_url="git@example.com:x/y.git"
    )
    HarnessJob.objects.create(workspace=workspace, repo=repo, goal="live", status="running")
    HarnessJob.objects.create(workspace=workspace, repo=repo, goal="done", status="succeeded")
    HarnessImplementationJob.objects.create(
        workspace=workspace, repo=repo, goal="live", status="pending_gate_a"
    )
    HarnessImplementationJob.objects.create(
        workspace=workspace, repo=repo, goal="done", status="failed"
    )


def row_for(body, workspace):
    return next(r for r in body["workspaces"] if r["workspace"] == str(workspace.id))


def test_owner_sees_counts_for_every_membership(
    owner, workspace, stranger_workspace, stranger, web_client
):
    WorkspaceMember.objects.create(
        workspace=stranger_workspace, user=owner, role=WorkspaceMember.Role.ADMIN
    )
    seed(workspace, owner)
    ApprovalRequest.objects.create(
        workspace=stranger_workspace, tool_name="t", requested_by=stranger
    )

    response = web_client(owner).get(URL)

    assert response.status_code == 200
    body = response.json()
    assert row_for(body, workspace) == {
        "workspace": str(workspace.id),
        "pending_approvals": 2,
        "live_harness_jobs": 2,
        "live_agents": 1,
    }
    assert row_for(body, stranger_workspace)["pending_approvals"] == 1
    assert body["totals"] == {"pending_approvals": 3, "live_harness_jobs": 2, "live_agents": 1}


@pytest.mark.parametrize("role", ROLES)
def test_every_member_role_can_read_its_workspace(workspace, owner, role, web_client):
    seed(workspace, owner)
    member = register_user(email=f"role-{role}@test.dev", password=PASSWORD, name=role)
    WorkspaceMember.objects.filter(workspace__owner=member).delete()
    WorkspaceMember.objects.update_or_create(
        workspace=workspace, user=member, defaults={"role": role}
    )

    response = web_client(member).get(URL, {"workspace": str(workspace.id)})

    assert response.status_code == 200
    assert response.json()["workspaces"] == [
        {
            "workspace": str(workspace.id),
            "pending_approvals": 2,
            "live_harness_jobs": 2,
            "live_agents": 1,
        }
    ]


def test_non_member_never_sees_another_workspace(
    owner, workspace, stranger, stranger_workspace, web_client
):
    seed(workspace, owner)

    response = web_client(stranger).get(URL)

    assert response.status_code == 200
    body = response.json()
    assert [r["workspace"] for r in body["workspaces"]] == [str(stranger_workspace.id)]
    assert body["totals"] == {"pending_approvals": 0, "live_harness_jobs": 0, "live_agents": 0}


def test_workspace_filter_for_a_non_member_is_404(owner, workspace, stranger, web_client):
    response = web_client(stranger).get(URL, {"workspace": str(workspace.id)})
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}


def test_workspace_filter_must_be_a_uuid(owner, web_client):
    response = web_client(owner).get(URL, {"workspace": "nope"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"


def test_anonymous_is_401():
    response = APIClient().get(URL)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_pat_channel_is_refused(owner, workspace):
    raw = "jpat_activity-raw-token-000000000000"
    PersonalAccessToken.objects.create(
        user=owner, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")

    response = client.get(URL)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_agent_account_is_refused(workspace, web_client):
    agent = register_user(email="agent@test.dev", password=PASSWORD, name="Agent")
    agent.is_agent = True
    agent.save(update_fields=["is_agent"])
    WorkspaceMember.objects.create(workspace=workspace, user=agent, role="admin")

    response = web_client(agent).get(URL)

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_read_only(owner, method, web_client):
    response = getattr(web_client(owner), method)(URL)
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


def test_query_count_is_constant(owner, workspace, django_assert_max_num_queries, web_client):
    seed(workspace, owner)
    for i in range(3):
        extra = Workspace.objects.create(name=f"firm {i}", owner=owner, type="company")
        WorkspaceMember.objects.create(workspace=extra, user=owner, role="owner")
        seed(extra, owner)
    with django_assert_max_num_queries(8):
        response = web_client(owner).get(URL)
    assert response.status_code == 200
    assert response.json()["totals"]["pending_approvals"] == 8
