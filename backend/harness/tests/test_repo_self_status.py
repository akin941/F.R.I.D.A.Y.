"""`self_repo_status` on the repo serializer (Phase 4 branch 5): the repo
registry UI's read-only view of the ADR-004 self-modification guard."""

import pytest

from accounts.services import register_user
from harness.models import HarnessRepo
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

SELF_URL = "git@github.com:akin941/F.R.I.D.A.Y..git"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def status_by_name(client, workspace):
    response = client.get("/api/v1/harness/repos", {"workspace": str(workspace.id)})
    assert response.status_code == 200
    return {row["name"]: row["self_repo_status"] for row in response.json()}


def test_self_and_other_repos_are_labeled(owner, workspace, settings, web_client):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = [SELF_URL]
    # A different spelling of the same remote still resolves to "self".
    HarnessRepo.objects.create(
        workspace=workspace, name="friday", git_url="https://github.com/akin941/F.R.I.D.A.Y."
    )
    HarnessRepo.objects.create(workspace=workspace, name="site", git_url="git@example.com:x/y.git")

    assert status_by_name(web_client(owner), workspace) == {"friday": "self", "site": "other"}


def test_malformed_legacy_url_is_reported_as_other(owner, workspace, settings, web_client):
    """A legacy row whose URL cannot be canonicalized never names this
    installation's remote: the serializer reports "other" instead of
    failing the whole list."""
    settings.HARNESS_SELF_REPO_IDENTIFIERS = [SELF_URL]
    HarnessRepo.objects.create(workspace=workspace, name="legacy", git_url="https://[::1/x")

    assert status_by_name(web_client(owner), workspace) == {"legacy": "other"}


def test_unconfigured_identifiers_are_reported(owner, workspace, settings, web_client):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = []
    HarnessRepo.objects.create(workspace=workspace, name="site", git_url="git@example.com:x/y.git")

    assert status_by_name(web_client(owner), workspace) == {"site": "unconfigured"}


def test_status_is_read_only(owner, workspace, settings, web_client):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = [SELF_URL]
    repo = HarnessRepo.objects.create(
        workspace=workspace, name="site", git_url="git@example.com:x/y.git"
    )

    response = web_client(owner).patch(
        f"/api/v1/harness/repos/{repo.id}", {"self_repo_status": "self"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["self_repo_status"] == "other"
