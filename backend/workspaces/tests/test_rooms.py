"""Phase 13 D2: `workspaces.rooms` -- room CRUD for the owner over the web,
key uniqueness across names and aliases, `room.in_use`, personal only,
audit rows with ids and keys only, two owners isolated."""

import pytest

from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from workspaces import rooms
from workspaces.models import Room, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


def make(owner, personal, name="Salon", **kwargs):
    return rooms.create_room(
        actor=owner, workspace=personal, channel=CHANNEL_WEB, name=name, **kwargs
    )


def test_create_keys_trims_orders_and_audits(owner, personal):
    salon = make(owner, personal, "  Salon  ", aliases=["oturma odası", " Misafir  odası "])
    study = make(owner, personal, "Çalışma Odası")
    assert (salon.name, salon.key, salon.aliases) == (
        "Salon",
        "salon",
        ["oturma odası", "Misafir odası"],
    )
    assert study.key == "çalişma odasi"
    assert (salon.sort_order, study.sort_order) == (0, 1)
    assert [room.pk for room in rooms.list_rooms(actor=owner, workspace=personal)] == [
        salon.pk,
        study.pk,
    ]
    row = AuditLog.objects.filter(action="home.room_created").order_by("created_at").first()
    assert row.metadata == {"room_id": str(salon.pk), "key": "salon", "alias_count": 2}
    assert row.workspace_id == personal.pk


@pytest.mark.parametrize(
    ("name", "aliases"),
    [
        ("SALON", []),  # the same key
        ("Oturma", ["salon"]),  # an alias repeating another room's name
        ("Oturma", ["misafir odası"]),  # an alias repeating another room's alias
        ("Misafir Odası", []),  # a name repeating another room's alias
    ],
)
def test_keys_are_unique_across_names_and_aliases(owner, personal, name, aliases):
    make(owner, personal, "Salon", aliases=["misafir odası"])
    with pytest.raises(rooms.LabelConflict):
        make(owner, personal, name, aliases=aliases)
    assert Room.objects.count() == 1


@pytest.mark.parametrize(
    ("name", "aliases", "error"),
    [
        ("", [], rooms.LabelInvalid),
        ("!!!", [], rooms.LabelInvalid),
        ("x" * 61, [], rooms.LabelInvalid),
        ("Salon", ["a"], rooms.LabelInvalid),
        ("Salon", ["x" * 41], rooms.LabelInvalid),
        ("Salon", ["ab", "cd", "ef", "gh", "ij", "kl"], rooms.LabelLimit),
        ("Salon", ["oda", "ODA"], rooms.LabelConflict),
        ("Salon", ["salon"], rooms.LabelConflict),
        ("Salon", "oda", rooms.LabelInvalid),
    ],
)
def test_label_rules(owner, personal, name, aliases, error):
    with pytest.raises(error):
        make(owner, personal, name, aliases=aliases)
    assert not Room.objects.exists()


def test_update_renames_realiases_and_audits_each_kind(owner, personal):
    room = make(owner, personal, "Salon")
    room = rooms.update_room(actor=owner, room=room, channel=CHANNEL_WEB, name="Oturma Odası")
    assert (room.name, room.key) == ("Oturma Odası", "oturma odasi")
    renamed = AuditLog.objects.get(action="home.room_renamed")
    assert renamed.metadata == {"room_id": str(room.pk), "key": "oturma odasi", "old_key": "salon"}
    room = rooms.update_room(
        actor=owner, room=room, channel=CHANNEL_WEB, aliases=["salon"], sort_order=4
    )
    assert room.aliases == ["salon"] and room.sort_order == 4
    changed = AuditLog.objects.get(action="home.room_changed")
    assert changed.metadata["fields"] == ["aliases", "sort_order"]
    # A no-op writes nothing.
    before = AuditLog.objects.count()
    rooms.update_room(actor=owner, room=room, channel=CHANNEL_WEB, name="Oturma Odası")
    assert AuditLog.objects.count() == before


def test_update_checks_other_rooms_but_not_itself(owner, personal):
    salon = make(owner, personal, "Salon")
    make(owner, personal, "Mutfak")
    rooms.update_room(actor=owner, room=salon, channel=CHANNEL_WEB, name="SALON")
    with pytest.raises(rooms.LabelConflict):
        rooms.update_room(actor=owner, room=salon, channel=CHANNEL_WEB, aliases=["mutfak"])
    with pytest.raises(rooms.LabelConflict):
        rooms.update_room(actor=owner, room=salon, channel=CHANNEL_WEB, name="Mutfak")


def test_delete_audits_ids_and_keys(owner, personal):
    # A room in use by a node (`room.in_use`) is pinned in
    # nodes/tests/test_node_labels.py: this app never imports `nodes`.
    room = make(owner, personal, "Salon")
    rooms.delete_room(actor=owner, room=room, channel=CHANNEL_WEB)
    assert not Room.objects.exists()
    row = AuditLog.objects.get(action="home.room_deleted")
    assert row.metadata == {"room_id": str(room.pk), "key": "salon"}


@pytest.mark.parametrize("channel", ["cli", "telegram", "node_voice", "openai", ""])
def test_only_the_web_channel(owner, personal, channel):
    with pytest.raises(rooms.RoomsPermissionDenied) as caught:
        rooms.create_room(actor=owner, workspace=personal, channel=channel, name="Salon")
    assert caught.value.code == "channel_forbidden"


def test_only_the_owner_a_person_and_never_a_firm(owner, personal):
    member = register_user(email="member@test.dev", password=PASSWORD)
    agent = register_user(email="agent@test.dev", password=PASSWORD)
    agent.is_agent = True
    agent.save()
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    for actor, workspace in ((member, personal), (agent, personal), (owner, firm)):
        with pytest.raises(rooms.RoomsPermissionDenied):
            rooms.create_room(actor=actor, workspace=workspace, channel=CHANNEL_WEB, name="Salon")
    assert not Room.objects.exists()


def test_two_owners_are_isolated(owner, personal):
    # The installation has one owner: the other personal workspace's room
    # is created directly (the same key in another workspace is fine).
    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other, type=Workspace.Type.PERSONAL)
    mine = make(owner, personal, "Salon")
    theirs = Room.objects.create(workspace=other_ws, name="Salon", key="salon")
    assert rooms.list_rooms(actor=owner, workspace=personal) == [mine]
    assert rooms.list_rooms(actor=other, workspace=other_ws) == [theirs]
    assert rooms.list_rooms(actor=owner, workspace=other_ws) == []
    assert list(Room.objects.for_actor(owner)) == [mine]
    with pytest.raises(rooms.RoomsPermissionDenied):
        rooms.update_room(actor=owner, room=theirs, channel=CHANNEL_WEB, name="Mutfak")
