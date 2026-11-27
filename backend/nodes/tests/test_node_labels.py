"""Phase 13 D3: node labels -- `relabel_node` and `PATCH /api/v1/nodes/<id>`
(name, room, role, aliases; `platform` never editable), uniqueness of
`name_key` and alias keys per owner among label-holding nodes, a room in
use is not deletable, no protocol effect (nothing on the bus, the next
`hello` unaffected), role x endpoint, and the 0005 data step on two
same-named nodes."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import bus, services
from nodes.models import Node
from nodes.services import pair_node, relabel_node
from nodes.tests.conftest import node_code
from workspaces import rooms
from workspaces.models import Room, Workspace

pytestmark = pytest.mark.django_db


def detail(node):
    return f"/api/v1/nodes/{node.pk}"


@pytest.fixture
def two(owner, personal):
    mac = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    desk = pair_node(
        raw_code=node_code(owner), node_name="Masaüstü", protocol_versions=[1], platform="linux"
    ).node
    return mac, desk


@pytest.fixture
def salon(owner, personal):
    return rooms.create_room(actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Salon")


# --- the service ---------------------------------------------------------


def test_relabel_sets_every_label_and_audits_keys_only(owner, two, salon):
    mac, _ = two
    node = relabel_node(
        actor=owner,
        node=mac,
        channel=CHANNEL_WEB,
        name="Dizüstü",
        room_id=salon.pk,
        role="laptop",
        aliases=["laptop", "Mac"],
    )
    assert (node.name, node.name_key, node.room, node.role, node.aliases) == (
        "Dizüstü",
        "dizüstü",
        salon,
        "laptop",
        ["laptop", "Mac"],
    )
    assert node.platform == "macos"
    node.device.refresh_from_db()
    assert node.device.name == "Dizüstü"
    row = AuditLog.objects.get(action="nodes.relabelled")
    assert row.metadata == {
        "node_id": str(mac.pk),
        "old_key": "macbook",
        "new_key": "dizüstü",
        "alias_keys": ["laptop", "mac"],
        "old_room_id": "",
        "new_room_id": str(salon.pk),
        "old_role": "other",
        "new_role": "laptop",
        "fields": ["aliases", "name", "role", "room"],
    }
    assert row.resource_id == str(mac.pk) and row.workspace_id == mac.workspace_id


def test_a_no_op_relabel_writes_nothing(owner, two):
    mac, _ = two
    relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, name="MacBook", role="other")
    assert not AuditLog.objects.filter(action="nodes.relabelled").exists()


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"name": "masaüstü"}, rooms.LabelConflict),  # another node's name
        ({"name": "MASAÜSTÜ!"}, rooms.LabelConflict),
        ({"aliases": ["Masaüstü"]}, rooms.LabelConflict),  # alias = another node's name
        ({"aliases": ["MacBook"]}, rooms.LabelConflict),  # alias = its own name
        ({"aliases": ["dev", "DEV"]}, rooms.LabelConflict),
        ({"name": ""}, rooms.LabelInvalid),
        ({"name": "!!"}, rooms.LabelInvalid),
        ({"name": "x" * 101}, rooms.LabelInvalid),
        ({"aliases": ["x"]}, rooms.LabelInvalid),
        ({"aliases": ["ab", "cd", "ef", "gh", "ij", "kl"]}, rooms.LabelLimit),
        ({"role": "router"}, rooms.LabelInvalid),
    ],
)
def test_label_rules(owner, two, fields, error):
    mac, _ = two
    with pytest.raises(error):
        relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, **fields)
    mac.refresh_from_db()
    assert (mac.name, mac.aliases, mac.role) == ("MacBook", [], "other")


def test_an_alias_may_not_collide_with_another_nodes_alias(owner, two):
    mac, desk = two
    relabel_node(actor=owner, node=desk, channel=CHANNEL_WEB, aliases=["masa"])
    with pytest.raises(rooms.LabelConflict):
        relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, aliases=["Masa"])
    with pytest.raises(rooms.LabelConflict):
        relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, name="masa")


def test_a_revoked_nodes_name_may_be_reused(owner, two, fake_revocation_redis):
    mac, desk = two
    devices.revoke_device(device=desk.device, actor=owner)
    node = relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, name="Masaüstü")
    assert node.name_key == "masaüstü"


def test_a_kill_switched_node_keeps_its_name_reserved(owner, two):
    mac, _ = two
    devices.engage_node_kill_switch(owner=owner, via="test")
    with pytest.raises(rooms.LabelConflict):
        relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, name="Masaüstü")


def test_a_room_of_another_workspace_is_unknown(owner, two):
    from accounts.services import register_user

    other = register_user(email="other@test.dev", password="Str0ngDevPass!42")
    theirs = Room.objects.create(
        workspace=Workspace.objects.get(owner=other), name="Salon", key="salon"
    )
    mac, _ = two
    with pytest.raises(rooms.LabelUnknownRoom):
        relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, room_id=theirs.pk)


def test_a_room_in_use_by_a_node_is_not_deletable(owner, two, salon, web_client):
    mac, _ = two
    relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, room_id=salon.pk)
    with pytest.raises(rooms.RoomInUse):
        rooms.delete_room(actor=owner, room=salon, channel=CHANNEL_WEB)
    url = f"/api/v1/workspaces/{salon.workspace_id}/rooms/{salon.pk}"
    response = web_client(owner).delete(url)
    assert response.status_code == 409 and response.json()["code"] == "room.in_use"
    assert Room.objects.filter(pk=salon.pk).exists()
    relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, room_id=None)
    assert web_client(owner).delete(url).status_code == 204


@pytest.mark.parametrize("channel", ["cli", "node", "node_voice", "hook", "openai", "telegram"])
def test_relabel_is_web_only(owner, two, channel):
    mac, _ = two
    with pytest.raises(services.NodeLabelPermissionDenied) as caught:
        relabel_node(actor=owner, node=mac, channel=channel, name="X")
    assert caught.value.code == "channel_forbidden"


def test_relabel_refuses_an_agent_and_a_non_owner(owner, two):
    from accounts.services import register_user

    mac, _ = two
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    member = register_user(email="member@test.dev", password="Str0ngDevPass!42")
    for actor in (agent, member):
        with pytest.raises(services.NodeLabelPermissionDenied):
            relabel_node(actor=actor, node=mac, channel=CHANNEL_WEB, name="X")


# --- no protocol effect --------------------------------------------------


def test_relabelling_sends_nothing_on_the_bus(owner, two, salon, monkeypatch):
    mac, _ = two
    calls = []
    for name in (
        "push_invoke",
        "push_result",
        "mark_present",
        "clear_presence",
        "clear_inbox",
        "acquire_inflight",
    ):
        monkeypatch.setattr(bus, name, lambda *a, _n=name, **k: calls.append(_n))
    relabel_node(
        actor=owner,
        node=mac,
        channel=CHANNEL_WEB,
        name="Laptop",
        room_id=salon.pk,
        role="laptop",
        aliases=["dizüstü"],
    )
    assert calls == []


# --- the API -------------------------------------------------------------


def test_patch_round_trip_and_the_detail_shows_labels(owner, two, salon, web_client):
    mac, _ = two
    client = web_client(owner)
    response = client.patch(
        detail(mac),
        {"name": "Dizüstü", "room_id": str(salon.pk), "role": "laptop", "aliases": ["laptop"]},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["name"] == "Dizüstü" and body["name_key"] == "dizüstü"
    assert body["room"] == {"id": str(salon.pk), "name": "Salon", "key": "salon"}
    assert (body["role"], body["platform"], body["aliases"]) == ("laptop", "macos", ["laptop"])
    assert body["last_active_at"] is None
    listed = {item["id"]: item for item in client.get("/api/v1/nodes").json()}
    assert listed[str(mac.pk)]["room"]["name"] == "Salon"
    cleared = client.patch(detail(mac), {"room_id": None}, format="json")
    assert cleared.json()["room"] is None


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": "Masaüstü"}, 409, "label.conflict"),
        ({"name": " "}, 400, "label.invalid"),
        ({"aliases": ["ab", "cd", "ef", "gh", "ij", "kl"]}, 400, "label.limit"),
        ({"room_id": "00000000-0000-4000-8000-000000000000"}, 400, "label.unknown_room"),
        ({"role": "router"}, 400, "label.invalid"),
        ({"platform": "linux"}, 400, "invalid"),
        ({"name": "X", "platform": "linux"}, 400, "invalid"),
        ({}, 400, "invalid"),
    ],
)
def test_patch_refusals(owner, two, web_client, body, status, code):
    mac, _ = two
    response = web_client(owner).patch(detail(mac), body, format="json")
    assert response.status_code == status, response.content
    assert response.json()["code"] == code
    mac.refresh_from_db()
    assert (mac.name, mac.platform) == ("MacBook", "macos")


@pytest.fixture
def principals(owner, personal, two, web_client):
    mac, _ = two
    secret = pair_node(raw_code=node_code(owner), node_name="Voice", protocol_versions=[1])
    voice = mint_token(secret.node_secret)

    def make(principal):
        if principal == "owner-web":
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            return bearer(make_pat(owner, personal, principal.removeprefix("owner-pat-"))[0])
        if principal == "node-secret":
            return bearer(secret.node_secret)
        if principal == "node-voice":
            return bearer(voice)
        if principal == "non-owner":
            from accounts.services import register_user

            return web_client(register_user(email="member@test.dev", password="Str0ngDevPass!42"))
        if principal == "firm-member":
            from accounts.services import register_user
            from workspaces.models import WorkspaceMember

            member = register_user(email="firm@test.dev", password="Str0ngDevPass!42")
            firm = Workspace.objects.create(name="Acme", type="company", owner=owner)
            WorkspaceMember.objects.create(workspace=firm, user=owner, role="owner")
            WorkspaceMember.objects.create(workspace=firm, user=member, role="member")
            return web_client(member)
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        from rest_framework.test import APIClient

        return APIClient()

    return make


LABEL_MATRIX = [
    ("owner-web", 200),
    ("kill-switched-owner", 200),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, (401, 403)),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, (401, 403)),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, (401, 403)),
    ("node-secret", (401, 403)),
    ("node-voice", (401, 403)),
    ("non-owner", 403),
    ("firm-member", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize(("principal", "expected"), LABEL_MATRIX)
def test_patch_role_matrix(principals, two, principal, expected):
    mac, _ = two
    client = principals(principal)
    response = client.patch(detail(mac), {"name": "Laptop"}, format="json")
    allowed = expected if isinstance(expected, tuple) else (expected,)
    assert response.status_code in allowed, (principal, response.status_code, response.content)
    mac.refresh_from_db()
    if expected == 200:
        assert mac.name == "Laptop"
    else:
        assert mac.name == "MacBook"
        assert str(mac.pk) not in response.content.decode()


def test_another_owners_node_is_404(owner, two, web_client):
    from accounts.tests.node_voice_helpers import other_user

    other, device = other_user()
    theirs = Node.objects.create(
        id=device.pk,
        device=device,
        owner=other,
        workspace=Workspace.objects.get(owner=other),
        name="Other Mac",
        name_key="other mac",
        pinned_key_id="k",
    )
    response = web_client(owner).patch(detail(theirs), {"name": "Mine"}, format="json")
    assert response.status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "Other Mac"


# --- the 0005 data step --------------------------------------------------


def test_the_data_migration_keys_names_and_suffixes_duplicates(owner, personal):
    """The RunPython step of `0005_node_labels`, run over rows as an
    upgraded install has them (empty `name_key`, duplicate names): oldest
    first keeps the name, later non-revoked duplicates are suffixed (and
    their device rows renamed), a revoked node and another owner's node
    are left alone."""
    from accounts.models import TrustedDevice
    from accounts.tests.node_voice_helpers import other_user

    migration = importlib.import_module("nodes.migrations.0005_node_labels")
    other, other_device = other_user()
    specs = [
        (owner, "MacBook", False),
        (owner, "MACBOOK", False),
        (owner, "MacBook", True),
        (owner, "Masaüstü", False),
        (other, "MacBook", False),
    ]
    rows = []
    for index, (user, name, revoked) in enumerate(specs):
        if user == other:
            device = other_device
        else:
            device = TrustedDevice.objects.create(
                user=user,
                name=name,
                kind=TrustedDevice.Kind.NODE,
                secret_hash=f"h{index}",
                enrolled_via=TrustedDevice.EnrolledVia.STRONG_CODE,
                revoked_at=timezone.now() if revoked else None,
            )
        node = Node.objects.create(
            id=device.pk,
            device=device,
            owner=user,
            workspace=Workspace.objects.get(owner=user, type="personal"),
            name=name,
            pinned_key_id="k",
        )
        Node.objects.filter(pk=node.pk).update(created_at=timezone.now() + timedelta(seconds=index))
        rows.append(node)
    assert all(row.name_key == "" for row in rows)
    migration.key_names(django_apps, None)
    got = []
    for row in rows:
        row.refresh_from_db()
        got.append((row.name, row.name_key))
    assert got == [
        ("MacBook", "macbook"),
        ("MACBOOK 2", "macbook 2"),
        ("MacBook", "macbook"),  # revoked: not counted, not renamed
        ("Masaüstü", "masaüstü"),
        ("MacBook", "macbook"),  # another owner's
    ]
    assert TrustedDevice.objects.get(pk=rows[1].pk).name == "MACBOOK 2"
