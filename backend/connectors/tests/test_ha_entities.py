"""Phase 13 D11: the owner's Home Assistant entity allowlist
(`connectors.HomeEntity`, `connectors.home_entities`) -- the code maximum on
every write path, the shared label rules, rooms, audit, scoping."""

import pytest
from django.core.exceptions import ValidationError

from accounts.services import register_user
from audit.models import AuditLog
from connectors import home_entities
from connectors.bridges import home_assistant as bridge
from connectors.models import HomeEntity
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from workspaces import rooms
from workspaces.models import Room, Workspace
from workspaces.rooms import LabelConflict, LabelInvalid, LabelLimit, LabelUnknownRoom

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
TV = "media_player.salon_tv"


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    return settings


@pytest.fixture
def salon(owner, workspace):
    return rooms.create_room(actor=owner, workspace=workspace, channel=CHANNEL_WEB, name="Salon")


def _create(owner, workspace, **overrides):
    kwargs = {
        "actor": owner,
        "workspace": workspace,
        "channel": CHANNEL_WEB,
        "entity_id": TV,
        "label": "TV",
        "services": ["turn_on", "turn_off", "select_source"],
        **overrides,
    }
    return home_entities.create_entity(**kwargs)


def test_create_stores_the_owner_set_row_and_audits_ids_only(owner, workspace, salon):
    entity = _create(owner, workspace, room_id=salon.pk, aliases=["televizyon"])
    assert (entity.entity_id, entity.label, entity.key, entity.aliases) == (
        TV,
        "TV",
        "tv",
        ["televizyon"],
    )
    assert entity.room == salon and entity.enabled is True
    # Stored in the code maximum's (sorted) order.
    assert entity.services == ["select_source", "turn_off", "turn_on"]
    log = AuditLog.objects.get(action="ha.entity_allowed")
    assert log.metadata == {
        "home_entity_id": str(entity.pk),
        "entity_id": TV,
        "domain": "media_player",
        "key": "tv",
        "services": ["select_source", "turn_off", "turn_on"],
        "room_id": str(salon.pk),
        "alias_count": 1,
        "enabled": True,
        "direct": False,
    }
    assert log.actor == owner


@pytest.mark.parametrize(
    ("entity_id", "services", "error"),
    [
        ("not an id", ["turn_on"], home_entities.HomeEntityInvalid),
        ("Media_player.tv", ["turn_on"], home_entities.HomeEntityInvalid),
        ("lock.front_door", ["unlock"], home_entities.HomeEntityNotAllowed),
        ("script.everything", ["turn_on"], home_entities.HomeEntityNotAllowed),
        ("automation.x", ["trigger"], home_entities.HomeEntityNotAllowed),
        ("sensor.salon_temp", ["turn_on"], home_entities.HomeEntityNotAllowed),
        ("wake_on_lan.pc", ["send_magic_packet"], home_entities.HomeEntityNotAllowed),
        ("vacuum.robot", ["start"], home_entities.HomeEntityNotAllowed),
        (TV, ["toggle"], home_entities.HomeEntityNotAllowed),
        (TV, ["play_media"], home_entities.HomeEntityNotAllowed),
        (TV, ["shuffle_set"], home_entities.HomeEntityNotAllowed),
        (TV, ["turn_on", "turn_on"], home_entities.HomeEntityNotAllowed),
        (TV, [], home_entities.HomeEntityNotAllowed),
        (TV, ["unlock"], home_entities.HomeEntityNotAllowed),
        ("light.salon", ["volume_set"], home_entities.HomeEntityNotAllowed),
    ],
)
def test_the_code_maximum_bounds_every_row(owner, workspace, entity_id, services, error):
    with pytest.raises(error):
        _create(owner, workspace, entity_id=entity_id, services=services)
    assert not HomeEntity.objects.exists()
    assert not AuditLog.objects.filter(action="ha.entity_allowed").exists()


def test_the_model_refuses_a_row_outside_the_code_maximum_on_any_write_path(workspace):
    """Not only the service: a direct ORM write cannot widen it either."""
    for entity_id, services in [
        ("lock.front_door", ["unlock"]),
        ("script.everything", ["turn_on"]),
        (TV, ["toggle"]),
        (TV, []),
        (TV, "turn_on"),
        ("sensor.x", ["turn_on"]),
    ]:
        with pytest.raises(ValidationError):
            HomeEntity.objects.create(
                workspace=workspace, entity_id=entity_id, label="x", key="x", services=services
            )
    row = HomeEntity.objects.create(
        workspace=workspace, entity_id=TV, label="TV", key="tv", services=["turn_on"]
    )
    row.services = ["turn_on", "shell"]
    with pytest.raises(ValidationError):
        row.save()
    row.refresh_from_db()
    assert row.services == ["turn_on"]


def test_allowed_domains_narrow_what_can_be_allowlisted(owner, workspace, ha_settings):
    ha_settings.HA_ALLOWED_DOMAINS = ["light"]
    with pytest.raises(home_entities.HomeEntityNotAllowed):
        _create(owner, workspace)
    assert home_entities.code_maximum() == {"light": ["turn_off", "turn_on"]}
    ha_settings.HA_ALLOWED_DOMAINS = ["light", "media_player"]
    assert _create(owner, workspace).entity_id == TV


def test_code_maximum_lists_every_callable_domain_and_nothing_forbidden():
    maximum = home_entities.code_maximum()
    assert set(maximum) == set(bridge.SERVICES) - {"wake_on_lan"}
    assert not set(maximum) & (bridge.NEVER_DOMAINS | bridge.READ_ONLY_DOMAINS)
    assert "select_source" in maximum["media_player"]
    for services in maximum.values():
        assert "toggle" not in services and "play_media" not in services
    # Phase 13b D4: the two gate-class services exist, on media players only.
    assert {"media_play_pause", "media_stop"} <= set(maximum["media_player"])
    for domain, services in maximum.items():
        if domain != "media_player":
            assert not {"media_play_pause", "media_stop"} & set(services)


def test_label_rules_are_the_shared_ones(owner, workspace):
    with pytest.raises(LabelInvalid):
        _create(owner, workspace, label="   ")
    with pytest.raises(LabelInvalid):
        _create(owner, workspace, label="x" * 61)
    with pytest.raises(LabelLimit):
        _create(owner, workspace, aliases=["ab", "cd", "ef", "gh", "ij", "kl"])
    with pytest.raises(LabelConflict):
        _create(owner, workspace, aliases=["tv"])
    with pytest.raises(LabelConflict):
        _create(owner, workspace, aliases=["tele", "TELE"])
    assert not HomeEntity.objects.exists()


def test_one_row_per_entity_and_labels_may_repeat(owner, workspace):
    _create(owner, workspace)
    with pytest.raises(home_entities.HomeEntityConflict):
        _create(owner, workspace, label="Başka")
    # Two entities may share a label: the home routes ask when ambiguous.
    other = _create(owner, workspace, entity_id="media_player.projector", label="TV")
    assert other.key == "tv"


def test_room_must_be_of_the_same_workspace(owner, workspace):
    other = register_user(email="other@test.dev", password=PASSWORD)
    theirs = Room.objects.create(
        workspace=Workspace.objects.get(owner=other), name="Salon", key="salon"
    )
    with pytest.raises(LabelUnknownRoom):
        _create(owner, workspace, room_id=theirs.pk)
    assert not HomeEntity.objects.exists()


def test_a_room_with_an_entity_cannot_be_deleted(owner, workspace, salon):
    _create(owner, workspace, room_id=salon.pk)
    with pytest.raises(rooms.RoomInUse):
        rooms.delete_room(actor=owner, room=salon, channel=CHANNEL_WEB)


def test_update_changes_only_what_was_given_and_audits_field_names(owner, workspace, salon):
    entity = _create(owner, workspace)
    same = home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_WEB)
    assert same.updated_at == entity.updated_at
    assert not AuditLog.objects.filter(action="ha.entity_changed").exists()

    updated = home_entities.update_entity(
        actor=owner,
        entity=entity,
        channel=CHANNEL_WEB,
        label="Salon TV",
        room_id=salon.pk,
        services=["volume_mute", "turn_on"],
        enabled=False,
    )
    assert (updated.label, updated.key, updated.room, updated.enabled) == (
        "Salon TV",
        "salon tv",
        salon,
        False,
    )
    assert updated.services == ["turn_on", "volume_mute"]
    log = AuditLog.objects.get(action="ha.entity_changed")
    assert log.metadata["fields"] == ["enabled", "label", "room", "services"]
    assert log.metadata["entity_id"] == TV

    with pytest.raises(home_entities.HomeEntityNotAllowed):
        home_entities.update_entity(
            actor=owner, entity=entity, channel=CHANNEL_WEB, services=["unlock"]
        )
    cleared = home_entities.update_entity(
        actor=owner, entity=entity, channel=CHANNEL_WEB, room_id=None
    )
    assert cleared.room is None


def test_delete_removes_the_row_and_audits(owner, workspace):
    entity = _create(owner, workspace)
    home_entities.delete_entity(actor=owner, entity=entity, channel=CHANNEL_WEB)
    assert not HomeEntity.objects.exists()
    log = AuditLog.objects.get(action="ha.entity_removed")
    assert log.metadata["entity_id"] == TV and log.metadata["key"] == "tv"


def test_only_the_owner_over_the_web_in_the_personal_workspace(owner, workspace, firm):
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        _create(owner, workspace, channel=CHANNEL_CLI)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        _create(owner, firm)
    agent = register_user(email="agent@test.dev", password=PASSWORD)
    agent.is_agent = True
    agent.save()
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        _create(agent, workspace)
    member = register_user(email="member@test.dev", password=PASSWORD)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        _create(member, workspace)
    entity = _create(owner, workspace)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_CLI, enabled=False)
    with pytest.raises(home_entities.HomeEntityPermissionDenied):
        home_entities.delete_entity(actor=member, entity=entity, channel=CHANNEL_WEB)
    entity.refresh_from_db()
    assert entity.enabled is True


def test_list_is_scoped_by_for_actor(owner, workspace):
    mine = _create(owner, workspace)
    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other)
    HomeEntity.objects.create(
        workspace=other_ws, entity_id=TV, label="TV", key="tv", services=["turn_on"]
    )
    assert home_entities.list_entities(actor=owner, workspace=workspace) == [mine]
    assert home_entities.list_entities(actor=owner, workspace=other_ws) == []
    assert list(HomeEntity.objects.for_actor(owner)) == [mine]
