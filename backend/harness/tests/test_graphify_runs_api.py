"""Phase 8B branch 9: `GET /api/v1/harness/graphify-runs` -- the Graphify run
list of the ingestion audit view. Role x endpoint against the Phase 4
principals plus a `hook` PAT (default authentication only, so a PAT is never
authenticated: 401), workspace isolation through `for_actor()`, and the
response carrying status, shas and counts only (never the error text)."""

import secrets
from datetime import UTC, datetime

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from core.channels import CHANNEL_WEB
from harness.models import GraphifyRun, HarnessRepo
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
URL = "/api/v1/harness/graphify-runs"
SHA = "a" * 40
C1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


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
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


def make_run(workspace, name="friday", **kwargs):
    repo = HarnessRepo.objects.create(
        workspace=workspace,
        name=name,
        git_url="git@example.invalid:o/friday.git",
        enabled=True,
        graphify_enabled=True,
    )
    defaults = {
        "status": GraphifyRun.Status.SUCCEEDED,
        "commit_sha": SHA,
        "committed_at": C1,
        "attempts": 1,
        "counts": {"entities": 22, "facts": 30, "closed": 3, "label": "nope", "flag": True},
    }
    defaults.update(kwargs)
    return GraphifyRun.objects.create(workspace=workspace, repo=repo, requested_sha=SHA, **defaults)


def _pat_client(owner, workspace, kind):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _client(principal, owner, workspace, web_client):
    if principal == "owner-web":
        return web_client(owner)
    if principal.startswith("owner-pat-"):
        return _pat_client(owner, workspace, principal.removeprefix("owner-pat-"))
    if principal in ("admin", "member"):
        user = register_user(email=f"{principal}@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(workspace=workspace, user=user, role=principal)
        return web_client(user)
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("owner-pat-hook", 401),
    # Not the installation owner: refused by `IsOwner` before any lookup.
    ("admin", 403),
    ("member", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_role_matrix(owner, personal, web_client, principal, expected):
    run = make_run(personal)
    response = _client(principal, owner, personal, web_client).get(f"{URL}?workspace={personal.id}")
    assert response.status_code == expected, response.content
    if expected == 200:
        assert [row["id"] for row in response.json()] == [str(run.id)]


def test_shape_is_status_shas_and_numeric_counts_only(owner, personal, web_client):
    make_run(
        personal,
        status=GraphifyRun.Status.FAILED,
        error_type="unsafe_output",
        error_message="graph.json: /home/owner/secret path",
    )
    (row,) = web_client(owner).get(f"{URL}?workspace={personal.id}").json()
    assert row["status"] == "failed"
    assert row["commit_sha"] == SHA and row["requested_sha"] == SHA
    assert row["repo"]["name"] == "friday"
    assert row["error_type"] == "unsafe_output"
    assert "error_message" not in row
    assert "secret" not in str(row)
    assert row["counts"] == {"entities": 22, "facts": 30, "closed": 3}


def test_workspace_param_is_required_and_validated(owner, web_client):
    client = web_client(owner)
    assert client.get(URL).status_code == 400
    assert client.get(f"{URL}?workspace=not-a-uuid").status_code == 400


def test_another_workspaces_runs_never_appear(owner, personal, firm, web_client):
    mine = make_run(personal)
    make_run(firm, name="firm-repo")
    rows = web_client(owner).get(f"{URL}?workspace={personal.id}").json()
    assert [r["id"] for r in rows] == [str(mine.id)]

    # A workspace the installation owner is not a member of: 404, not 403.
    stranger = register_user(email="out@test.dev", password=PASSWORD, name="Out")
    theirs = Workspace.objects.get(owner=stranger, type=Workspace.Type.PERSONAL)
    make_run(theirs, name="their-repo")
    assert web_client(owner).get(f"{URL}?workspace={theirs.id}").status_code == 404


def test_the_owner_as_a_mere_member_of_a_workspace_is_403(owner, web_client):
    stranger = register_user(email="out@test.dev", password=PASSWORD, name="Out")
    theirs = Workspace.objects.get(owner=stranger, type=Workspace.Type.PERSONAL)
    WorkspaceMember.objects.create(workspace=theirs, user=owner, role="member")
    make_run(theirs)
    assert web_client(owner).get(f"{URL}?workspace={theirs.id}").status_code == 403
