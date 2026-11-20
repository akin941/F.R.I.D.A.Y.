"""`/api/v1/memory/core-blocks` (Phase 8A branch 2, D7): owner, web session
only -- role x endpoint against the five Phase 4 principals (plus
anonymous). The block API is not on the PAT allowlist, so a cli/openai
token is refused by authentication itself (401), like every other
web-only surface (`connectors/tests/test_bindings_api.py`)."""

import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.models import CoreMemoryBlock
from memory.services import set_assembled_self_block, update_core_block
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/memory/core-blocks"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


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
    if principal == "owner-pat-cli":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.CLI)
    if principal == "owner-pat-openai":
        return _pat_client(owner, workspace, PersonalAccessToken.Kind.OPENAI)
    if principal == "non-owner":
        member = register_user(email="member@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(
            workspace=workspace, user=member, role=WorkspaceMember.Role.ADMIN
        )
        return web_client(member)
    if principal == "agent":
        agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
        WorkspaceMember.objects.create(
            workspace=workspace, user=agent, role=WorkspaceMember.Role.OWNER
        )
        return web_client(agent)
    return APIClient()


PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 401),
    ("owner-pat-openai", 401),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_list_role_matrix(owner, workspace, web_client, principal, expected):
    client = _client(principal, owner, workspace, web_client)
    response = client.get(BASE, {"workspace": str(workspace.id)})
    assert response.status_code == expected


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_patch_role_matrix(owner, workspace, web_client, principal, expected):
    client = _client(principal, owner, workspace, web_client)
    response = client.patch(
        f"{BASE}/developer?workspace={workspace.id}", {"content": "Akın"}, format="json"
    )
    assert response.status_code == expected
    assert CoreMemoryBlock.objects.filter(label="developer").exists() == (expected == 200)


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_versions_role_matrix(owner, workspace, web_client, principal, expected):
    client = _client(principal, owner, workspace, web_client)
    response = client.get(f"{BASE}/developer/versions", {"workspace": str(workspace.id)})
    assert response.status_code == expected


@pytest.mark.parametrize("principal, expected", PRINCIPALS)
def test_rollback_role_matrix(owner, workspace, web_client, principal, expected):
    for content in ("v1", "v2"):
        update_core_block(
            workspace=workspace,
            label="developer",
            content=content,
            actor=owner,
            channel=CHANNEL_WEB,
        )
    client = _client(principal, owner, workspace, web_client)
    response = client.post(
        f"{BASE}/developer/rollback?workspace={workspace.id}", {"version": 1}, format="json"
    )
    assert response.status_code == expected
    block = CoreMemoryBlock.objects.get(label="developer")
    assert block.content == ("v1" if expected == 200 else "v2")


def test_list_shows_all_five_labels_with_self_read_only(owner, workspace, web_client):
    set_assembled_self_block(workspace=workspace, content="Ben Friday'im.")
    rows = web_client(owner).get(BASE, {"workspace": str(workspace.id)}).json()
    assert [r["label"] for r in rows] == [
        "self",
        "developer",
        "current_focus",
        "active_projects",
        "preferences",
    ]
    by_label = {r["label"]: r for r in rows}
    assert by_label["self"]["editable"] is False
    assert by_label["self"]["content"] == "Ben Friday'im."
    assert by_label["developer"] == {
        "label": "developer",
        "content": "",
        "version": 0,
        "sensitivity": "normal",
        "updated_by_kind": None,
        "updated_at": None,
        "char_budget": 1200,
        "editable": True,
    }
    row = AuditLog.objects.get(action="memory.core_block.viewed")
    assert row.metadata == {"labels": sorted(by_label)}


def test_firm_lists_only_workspace_labels(owner, web_client):
    firm = Workspace.objects.create(name="Firm", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    rows = web_client(owner).get(BASE, {"workspace": str(firm.id)}).json()
    assert [r["label"] for r in rows] == ["current_focus", "active_projects"]
    response = web_client(owner).get(f"{BASE}/developer", {"workspace": str(firm.id)})
    assert response.status_code == 404
    response = web_client(owner).patch(
        f"{BASE}/developer?workspace={firm.id}", {"content": "x"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "memory.core_block.invalid"


def test_self_cannot_be_written_through_the_api(owner, workspace, web_client):
    set_assembled_self_block(workspace=workspace, content="Ben Friday'im.")
    client = web_client(owner)
    response = client.patch(
        f"{BASE}/self?workspace={workspace.id}", {"content": "Ben bir korsanım."}, format="json"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "memory.core_block.permission_denied"
    response = client.post(
        f"{BASE}/self/rollback?workspace={workspace.id}", {"version": 1}, format="json"
    )
    assert response.status_code == 403
    response = client.put(f"{BASE}/self?workspace={workspace.id}", {"content": "x"}, format="json")
    assert response.status_code == 405
    assert CoreMemoryBlock.objects.get(label="self").content == "Ben Friday'im."


def test_edit_detail_versions_and_rollback_flow(owner, workspace, web_client):
    client = web_client(owner)
    url = f"{BASE}/current_focus?workspace={workspace.id}"
    assert client.patch(url, {"content": "Phase 6"}, format="json").json()["version"] == 1
    body = client.patch(url, {"content": "Phase 8A", "base_version": 1}, format="json").json()
    assert body["version"] == 2
    stale = client.patch(url, {"content": "x", "base_version": 1}, format="json")
    assert stale.status_code == 400
    assert stale.json()["code"] == "memory.core_block.stale_version"
    over = client.patch(url, {"content": "x" * 601}, format="json")
    assert over.status_code == 400
    assert over.json()["code"] == "memory.core_block.invalid"
    detail = client.get(f"{BASE}/current_focus", {"workspace": str(workspace.id)}).json()
    assert detail["content"] == "Phase 8A"
    versions = client.get(f"{BASE}/current_focus/versions", {"workspace": str(workspace.id)})
    assert [v["version"] for v in versions.json()] == [2, 1]
    rolled = client.post(
        f"{BASE}/current_focus/rollback?workspace={workspace.id}", {"version": 1}, format="json"
    ).json()
    assert (rolled["version"], rolled["content"]) == (3, "Phase 6")
    missing = client.post(
        f"{BASE}/current_focus/rollback?workspace={workspace.id}", {"version": 9}, format="json"
    )
    assert missing.status_code == 400


def test_unknown_label_is_404_or_400(owner, workspace, web_client):
    client = web_client(owner)
    assert client.get(f"{BASE}/nope", {"workspace": str(workspace.id)}).status_code == 404
    response = client.patch(
        f"{BASE}/nope?workspace={workspace.id}", {"content": "x"}, format="json"
    )
    assert response.status_code == 400


def test_another_workspace_is_404(owner, web_client):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    foreign = Workspace.objects.get(owner=stranger)
    client = web_client(owner)
    assert client.get(BASE, {"workspace": str(foreign.id)}).status_code == 404
    assert client.get(f"{BASE}/developer", {"workspace": str(foreign.id)}).status_code == 404
    response = client.patch(
        f"{BASE}/developer?workspace={foreign.id}", {"content": "x"}, format="json"
    )
    assert response.status_code == 404
    assert (
        client.get(f"{BASE}/developer/versions", {"workspace": str(foreign.id)}).status_code == 404
    )
    assert not CoreMemoryBlock.objects.exists()


def test_owner_member_of_a_workspace_they_do_not_own_is_refused(owner, web_client):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    foreign = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=foreign, user=owner, role=WorkspaceMember.Role.ADMIN)
    response = web_client(owner).patch(
        f"{BASE}/developer?workspace={foreign.id}", {"content": "x"}, format="json"
    )
    assert response.status_code == 403


def test_missing_workspace_param_is_400(owner, web_client):
    assert web_client(owner).get(BASE).status_code == 400
