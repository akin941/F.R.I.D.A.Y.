"""Phase 13 D2: the rooms API (`/api/v1/workspaces/<id>/rooms`) -- CRUD,
error codes, a firm workspace is 404, and role x endpoint over the owner's
web session, a firm, a kill-switched owner, the `cli`/`openai`/`hook`
PATs, a node secret, a `node_voice` token, a non-owner, a firm member, an
agent and an anonymous client."""

import pytest
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from workspaces import rooms
from workspaces.models import Room, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner(db):
    from accounts.services import register_user

    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def member(db):
    from accounts.services import register_user

    return register_user(email="member@test.dev", password=PASSWORD, name="Member")


@pytest.fixture
def firm(owner, member):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    return firm


def base(workspace):
    return f"/api/v1/workspaces/{workspace.pk}/rooms"


def test_crud_round_trip(owner, personal, web_client):
    client = web_client(owner)
    created = client.post(
        base(personal), {"name": "Salon", "aliases": ["oturma odası"]}, format="json"
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert (body["name"], body["key"], body["aliases"], body["sort_order"]) == (
        "Salon",
        "salon",
        ["oturma odası"],
        0,
    )
    listed = client.get(base(personal)).json()
    assert [room["id"] for room in listed["results"]] == [body["id"]]
    assert listed["limits"] == {
        "aliases": 5,
        "alias_min_chars": 2,
        "alias_max_chars": 40,
        "name_max_chars": 60,
    }
    patched = client.patch(f"{base(personal)}/{body['id']}", {"name": "Büyük Salon"}, format="json")
    assert patched.status_code == 200 and patched.json()["key"] == "büyük salon"
    deleted = client.delete(f"{base(personal)}/{body['id']}")
    assert deleted.status_code == 204
    assert not Room.objects.exists()
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"home.room_created", "home.room_renamed", "home.room_deleted"} <= actions


def test_error_codes(owner, personal, web_client):
    client = web_client(owner)
    client.post(base(personal), {"name": "Salon"}, format="json")
    conflict = client.post(base(personal), {"name": "SALON"}, format="json")
    assert conflict.status_code == 409 and conflict.json()["code"] == "label.conflict"
    invalid = client.post(base(personal), {"name": "   "}, format="json")
    assert invalid.status_code == 400 and invalid.json()["code"] == "label.invalid"
    limit = client.post(
        base(personal),
        {"name": "Mutfak", "aliases": ["ab", "cd", "ef", "gh", "ij", "kl"]},
        format="json",
    )
    assert limit.status_code == 400 and limit.json()["code"] == "label.limit"
    empty_patch = client.patch(f"{base(personal)}/{Room.objects.get().pk}", {}, format="json")
    assert empty_patch.status_code == 400


def test_a_room_in_use_answers_409(owner, personal, web_client, monkeypatch):
    room = rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Salon")

    def in_use(**kwargs):
        raise rooms.RoomInUse("The room is still in use.")

    monkeypatch.setattr(rooms, "delete_room", in_use)
    response = web_client(owner).delete(f"{base(personal)}/{room.pk}")
    assert response.status_code == 409 and response.json()["code"] == "room.in_use"


def test_a_firm_workspace_has_no_rooms(owner, firm, web_client):
    client = web_client(owner)
    assert client.get(base(firm)).status_code == 404
    assert client.post(base(firm), {"name": "Salon"}, format="json").status_code == 404
    assert not Room.objects.exists()


def test_another_users_room_is_404(owner, personal, web_client):
    from accounts.services import register_user

    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other, type=Workspace.Type.PERSONAL)
    theirs = Room.objects.create(workspace=other_ws, name="Salon", key="salon")
    client = web_client(owner)
    assert client.get(base(other_ws)).status_code == 404
    assert (
        client.patch(f"{base(other_ws)}/{theirs.pk}", {"name": "X"}, format="json").status_code
        == 404
    )
    assert client.delete(f"{base(personal)}/{theirs.pk}").status_code == 404
    assert Room.objects.get(pk=theirs.pk).name == "Salon"


@pytest.fixture
def principals(owner, personal, member, firm, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            return bearer(make_pat(owner, personal, principal.removeprefix("owner-pat-"))[0])
        if principal == "node-secret":
            return bearer(secret)
        if principal == "node-voice":
            return bearer(voice)
        if principal in ("non-owner", "firm-member"):
            return web_client(member)
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        return APIClient()

    return make


MATRIX = [
    ("owner-web", "ok"),
    ("owner-firm", 404),
    ("kill-switched-owner", "ok"),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, 401),
    ("node-secret", 401),
    ("node-voice", 401),
    ("non-owner", 404),
    ("firm-member", 404),
    ("agent", 404),
    ("anonymous", 401),
]

OK = {"list": 200, "create": 201, "patch": 200, "delete": 204}


@pytest.mark.parametrize(("principal", "expected"), MATRIX)
@pytest.mark.parametrize("endpoint", sorted(OK))
def test_role_matrix(principals, owner, personal, firm, endpoint, principal, expected):
    room = rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Mutfak")
    target = firm if principal in ("owner-firm", "firm-member") else personal
    client = principals(principal)
    calls = {
        "list": lambda: client.get(base(target)),
        "create": lambda: client.post(base(target), {"name": "Salon"}, format="json"),
        "patch": lambda: client.patch(
            f"{base(target)}/{room.pk}", {"name": "Kiler"}, format="json"
        ),
        "delete": lambda: client.delete(f"{base(target)}/{room.pk}"),
    }
    response = calls[endpoint]()
    if expected == "ok":
        assert response.status_code == OK[endpoint], response.content
        return
    assert response.status_code == expected, (endpoint, response.status_code, response.content)
    room.refresh_from_db()
    assert room.name == "Mutfak"
    assert list(Room.objects.values_list("name", flat=True)) == ["Mutfak"]
    if response.status_code != 401:
        assert "Mutfak" not in response.content.decode()
