"""Phase 13 D13 (branch 6): `connectors.home_phrases` -- the home command
parser (the shared vectors `docs/voice/home-route-vectors.json`), the
resolver against the owner's allowlist (draft / ask / refusal; no Home
Assistant request, no row written, no model call), the typed-turn draft
check (`target_mismatch`) and the port registration."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps

from accounts.services import register_user
from ai_core.models import ModelUsage
from connectors import home_phrases
from connectors.manifest import get_manifest
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.speech import normalize_speech, phrase_key
from tools import home_ports
from workspaces.models import Room, Workspace

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "docs" / "voice" / "home-route-vectors.json").read_text(
        encoding="utf-8"
    )
)
MANIFEST = get_manifest("home_assistant")
TV = "media_player.salon_tv"


# --- the shared vectors (pure) ------------------------------------------------


def _vector_vocabulary():
    rooms = {
        row["name"]: SimpleNamespace(name=row["name"], aliases=row["aliases"], pk=index)
        for index, row in enumerate(VECTORS["vocabulary"]["rooms"], start=1)
    }
    entities = []
    for row in VECTORS["vocabulary"]["entities"]:
        room = rooms.get(row["room"])
        entities.append(
            SimpleNamespace(
                entity_id=row["entity_id"],
                label=row["label"],
                aliases=row["aliases"],
                room=room,
                room_id=room.pk if room else None,
                domain=row["entity_id"].split(".", 1)[0],
            )
        )
    return (
        home_phrases.entity_vocabulary(entities),
        home_phrases.room_vocabulary(rooms.values()),
    )


@pytest.mark.parametrize("row", VECTORS["normalize"], ids=lambda row: row["input"])
def test_normalize_vectors(row):
    assert normalize_speech(row["input"]) == row["speech"]


@pytest.mark.parametrize("row", VECTORS["parse"], ids=lambda row: row["input"] or "<empty>")
def test_parse_vectors(row):
    entities, rooms = _vector_vocabulary()
    command = home_phrases.parse_home_command(row["input"], entities=entities, rooms=rooms)
    if "match" in row and row["match"] is None:
        assert command is None
        return
    assert command is not None, row["input"]
    assert command.action.kind == row["action"]
    if "value" in row:
        assert command.action.value == row["value"]
    assert [entity.entity_id for entity in command.entities] == row["entities"]
    assert (command.room.name if command.room else None) == row["room"]
    assert command.unknown_room == row.get("unknown_room", "")
    domain = row["entities"][0].split(".", 1)[0] if row["entities"] else "media_player"
    assert home_phrases.service_for(command.action, domain) == (row["service"], row["data"])


def test_the_vectors_cover_the_briefs_examples():
    inputs = {row["input"] for row in VECTORS["parse"]}
    for example in (
        "Hey Friday, salondaki TV'yi aç",
        "salon tv'yi kapat",
        "TV'nin sesini kıs",
        "televizyonu HDMI 2'ye geç",
        "sesi yüzde 30'a ayarla",
        "film gecesi modunu aç",
        "turn off the living room lights",
    ):
        assert example in inputs


def test_the_wake_strip_tables_equal_the_assistants():
    from assistant import wake_routes

    assert home_phrases.WAKE_PHRASES == wake_routes.WAKE_PHRASES
    assert home_phrases.TRAILING_FILLERS == wake_routes.TRAILING_FILLERS


def test_a_source_keeps_the_owners_spelling_without_its_ending():
    entities, rooms = _vector_vocabulary()
    for text, source in (
        ("televizyonu HDMI 2'ye geç", "HDMI 2"),
        ("televizyonu Netflix'e geç", "Netflix"),
        ("televizyonu Apple TV'ye geç", "Apple TV"),
        ("televizyonu Netflixe geç", "Netflix"),
    ):
        command = home_phrases.parse_home_command(text, entities=entities, rooms=rooms)
        assert command.action.value == source, text


@pytest.mark.parametrize(
    ("kind", "value", "domain", "expected"),
    [
        ("on", None, "cover", ("open_cover", {})),
        ("off", None, "cover", ("close_cover", {})),
        ("on", None, "climate", None),
        ("mute", None, "light", None),
        ("level", 0, "light", None),
        ("level", 55, "fan", ("set_percentage", {"percentage": 55})),
        ("level", 70, "cover", ("set_cover_position", {"position": 70})),
        ("volume", 0, "media_player", ("volume_set", {"volume_level": 0.0})),
        ("volume", 100, "media_player", ("volume_set", {"volume_level": 1.0})),
        ("source", "HDMI 1", "light", None),
    ],
)
def test_service_for_maps_each_action_to_one_service(kind, value, domain, expected):
    action = home_phrases.Action(kind, "x", value)
    assert home_phrases.service_for(action, domain) == expected


def test_every_mapped_service_is_inside_the_code_maximum():
    from connectors.bridges import home_assistant as bridge

    kinds = [
        ("on", None),
        ("off", None),
        ("mute", None),
        ("unmute", None),
        ("volume_up", None),
        ("volume_down", None),
        ("play", None),
        ("pause", None),
        ("source", "HDMI 1"),
        ("volume", 30),
        ("level", 30),
    ]
    for domain in bridge.entity_domains():
        for kind, value in kinds:
            mapped = home_phrases.service_for(home_phrases.Action(kind, "x", value), domain)
            if mapped is None:
                continue
            service, data = mapped
            assert service in bridge.SERVICES[domain], (domain, kind)
            serializer = bridge.SERVICES[domain][service](data=data)
            assert serializer.is_valid(), (domain, kind, serializer.errors)


# --- the resolver (DB) --------------------------------------------------------

@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    return settings


def _room(workspace, name, aliases=()):
    return Room.objects.create(
        workspace=workspace, name=name, key=phrase_key(name), aliases=list(aliases)
    )


@pytest.fixture
def home(ha, enable, workspace):  # noqa: F811
    """The brief's home: Salon (TV, ceiling light), a scene, the bedroom
    speaker; HA bound (its pin check ran against the fake)."""
    ha.states[TV] = salon_tv_state()
    binding = enable(MANIFEST, workspace)
    salon = _room(workspace, "Salon", ["living room"])
    bedroom = _room(workspace, "Yatak odası")
    rows = SimpleNamespace(
        binding=binding,
        salon=salon,
        bedroom=bedroom,
        tv=allow(
            workspace,
            TV,
            ["turn_on", "volume_down", "volume_set", "select_source", "volume_mute"],
            label="TV",
            room=salon,
            aliases=["televizyon"],
        ),
        light=allow(
            workspace,
            "light.salon_tavan",
            ["turn_on", "turn_off"],
            label="Tavan lambası",
            room=salon,
            aliases=["ışık", "light"],
        ),
        scene=allow(workspace, "scene.film_gecesi", label="film gecesi"),
        speaker=allow(
            workspace,
            "media_player.yatak_hoparlor",
            ["turn_on", "turn_off"],
            label="hoparlör",
            room=bedroom,
        ),
    )
    ha.requests.clear()
    return rows


def _resolve(workspace, owner, text):
    return home_phrases.resolve_home_command(workspace=workspace, actor=owner, text=text)


def _row_counts():
    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if not model._meta.proxy and model._meta.managed
    }


@pytest.mark.django_db
def test_one_match_is_a_draft_with_the_exact_arguments(home, ha, workspace, owner):  # noqa: F811
    before = _row_counts()
    result = _resolve(workspace, owner, "Hey Friday, salondaki TV'yi aç")
    assert result.outcome == "draft"
    assert result.tool_name == "ha_call_service"
    assert result.arguments == {
        "domain": "media_player",
        "service": "turn_on",
        "entity_id": TV,
        "data": {},
    }
    assert result.reply == "Onay kartı hazır: Salon TV · aç. Kararı ekranda verirsin."
    # Read-only: no approval, no execution, no HA request, no model call.
    assert _row_counts() == before
    assert ha.requests == []
    assert not ModelUsage.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("text", "service", "data"),
    [
        ("televizyonu HDMI 2'ye geç", "select_source", {"source": "HDMI 2"}),
        ("sesi yüzde 30'a ayarla", "volume_set", {"volume_level": 0.3}),
        ("TV'nin sesini kıs", "volume_down", {}),
        ("televizyonun sesini kapat", "volume_mute", {"is_volume_muted": True}),
    ],
)
def test_media_commands_draft_their_one_service(home, workspace, owner, text, service, data):
    result = _resolve(workspace, owner, text)
    assert result.outcome == "draft", result.reply
    assert result.arguments["entity_id"] == TV
    assert (result.arguments["service"], result.arguments["data"]) == (service, data)


@pytest.mark.django_db
def test_scene_and_english_lights(home, workspace, owner):
    scene = _resolve(workspace, owner, "film gecesi modunu aç")
    assert scene.outcome == "draft"
    assert scene.arguments == {
        "domain": "scene",
        "service": "turn_on",
        "entity_id": "scene.film_gecesi",
        "data": {},
    }
    lights = _resolve(workspace, owner, "turn off the living room lights")
    assert lights.outcome == "draft"
    assert lights.arguments["entity_id"] == "light.salon_tavan"
    assert lights.arguments["service"] == "turn_off"
    assert lights.reply.startswith("The card is ready: Salon Tavan lambası · turn off")


@pytest.mark.django_db
def test_two_tvs_in_the_salon_ask(home, workspace, owner):
    allow(
        workspace,
        "media_player.projeksiyon",
        ["turn_on"],
        label="Projeksiyon",
        room=home.salon,
        aliases=["tv"],
    )
    result = _resolve(workspace, owner, "salondaki TV'yi aç")
    assert result.outcome == "ask"
    assert result.reply == "2 cihaz eşleşti: Salon Projeksiyon, Salon TV. Hangisi?"
    assert result.candidates == ("media_player.projeksiyon", TV)
    assert result.arguments == {}


@pytest.mark.django_db
def test_two_media_players_for_a_bare_volume_command_ask(home, workspace, owner):
    home.speaker.services = ["volume_set"]
    home.speaker.save()
    result = _resolve(workspace, owner, "sesi yüzde 30'a ayarla")
    assert result.outcome == "ask"
    assert "Salon TV" in result.reply and "Yatak odası hoparlör" in result.reply
    # Narrowed by the room it names.
    in_room = _resolve(workspace, owner, "yatak odasındaki sesi yüzde 30'a ayarla")
    assert in_room.outcome == "draft"
    assert in_room.arguments["entity_id"] == "media_player.yatak_hoparlor"


@pytest.mark.django_db
def test_an_unknown_room_is_refused(home, workspace, owner):
    result = _resolve(workspace, owner, "mutfaktaki TV'yi aç")
    assert (result.outcome, result.code) == ("refuse", "unknown_room")
    assert result.reply == "Bu odayı tanımıyorum: mutfaktaki."


@pytest.mark.django_db
def test_a_device_not_in_the_named_room_is_refused(home, workspace, owner):
    result = _resolve(workspace, owner, "yatak odasındaki TV'yi aç")
    assert (result.outcome, result.code) == ("refuse", "not_found")
    assert result.reply == "Yatak odası için 'TV' adlı izinli bir cihaz yok."


@pytest.mark.django_db
def test_a_disallowed_service_is_refused(home, workspace, owner):
    result = _resolve(workspace, owner, "salondaki TV'yi kapat")
    assert (result.outcome, result.code) == ("refuse", "service_not_allowed")
    assert result.reply == "Bunu yapmaya iznim yok: Salon TV için 'kapat' açık değil."
    # A verb the domain has no service for is refused the same way.
    light = _resolve(workspace, owner, "ışığın sesini kıs")
    assert (light.outcome, light.code) == ("refuse", "service_not_allowed")


@pytest.mark.django_db
def test_ha_allowed_domains_narrows_the_route(home, workspace, owner, settings):
    settings.HA_ALLOWED_DOMAINS = ["light"]
    result = _resolve(workspace, owner, "salondaki TV'yi aç")
    assert (result.outcome, result.code) == ("refuse", "invalid")


@pytest.mark.django_db
def test_a_disabled_entity_is_not_vocabulary(home, workspace, owner):
    home.tv.enabled = False
    home.tv.save()
    assert _resolve(workspace, owner, "salondaki TV'yi aç") is None


@pytest.mark.django_db
def test_an_empty_allowlist_is_never_a_home_command(ha, enable, workspace, owner):  # noqa: F811
    enable(MANIFEST, workspace)
    assert _resolve(workspace, owner, "salondaki TV'yi aç") is None


@pytest.mark.django_db
def test_not_connected_refuses(home, workspace, owner):
    home.binding.enabled = False
    home.binding.save()
    result = _resolve(workspace, owner, "salondaki TV'yi aç")
    assert (result.outcome, result.code) == ("refuse", "not_connected")


@pytest.mark.django_db
def test_non_commands_are_misses(home, workspace, owner):
    for text in ("bugün hava nasıl", "salonu kapat", "günaydın", "çalışma odasındaki Mac'i aç"):
        assert _resolve(workspace, owner, text) is None, text


@pytest.mark.django_db
def test_a_firm_workspace_and_another_users_rows_never_resolve(home, workspace, owner, firm):
    assert _resolve(firm, owner, "salondaki TV'yi aç") is None
    stranger = register_user(email="stranger@test.dev", password="Str0ngDevPass!42")
    # for_actor(): a non-member sees no allowlist row of this workspace.
    assert _resolve(workspace, stranger, "salondaki TV'yi aç") is None
    other_ws = Workspace.objects.get(owner=stranger)
    assert _resolve(other_ws, stranger, "salondaki TV'yi aç") is None


# --- the typed-turn draft check -----------------------------------------------


def _args(entity_id, service="turn_on", data=None):
    return {
        "domain": entity_id.split(".", 1)[0],
        "service": service,
        "entity_id": entity_id,
        "data": data or {},
    }


@pytest.mark.django_db
def test_typed_draft_for_another_entity_than_the_one_named_is_refused(home, workspace, owner):
    refusal = home_phrases.check_call_draft(
        workspace=workspace,
        actor=owner,
        arguments=_args("media_player.yatak_hoparlor"),
        source_text="salondaki TV'yi açar mısın?",
    )
    assert refusal is not None and "Salon TV" in refusal
    assert "media_player.yatak_hoparlor" in refusal
    # A room alone: a device outside it is a mismatch too.
    refusal = home_phrases.check_call_draft(
        workspace=workspace,
        actor=owner,
        arguments=_args("media_player.yatak_hoparlor"),
        source_text="salondaki şeyi aç",
    )
    assert refusal is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    ["salondaki TV'yi açar mısın?", "salon tv'yi aç", "televizyonu aç", "aç şunu"],
)
def test_typed_draft_for_the_named_entity_passes(home, workspace, owner, text):
    assert (
        home_phrases.check_call_draft(
            workspace=workspace, actor=owner, arguments=_args(TV), source_text=text
        )
        is None
    )


@pytest.mark.django_db
def test_typed_draft_outside_the_allowlist_is_refused(home, workspace, owner):
    assert home_phrases.check_call_draft(
        workspace=workspace,
        actor=owner,
        arguments=_args("media_player.komsu"),
        source_text="komşunun TV'sini aç",
    ).startswith("Bu cihaz Home Assistant izin listende yok")
    assert home_phrases.check_call_draft(
        workspace=workspace, actor=owner, arguments=_args(TV, "turn_off"), source_text="tv kapat"
    ).startswith("Bu işlem bu cihaz için")


@pytest.mark.django_db
def test_wake_on_lan_skips_the_mention_check(home, workspace, owner, settings):
    settings.HA_WOL_ALLOWED_MACS = ["AA:BB:CC:DD:EE:FF"]
    arguments = {
        "domain": "wake_on_lan",
        "service": "send_magic_packet",
        "data": {"mac": "AA:BB:CC:DD:EE:FF"},
    }
    assert (
        home_phrases.check_call_draft(
            workspace=workspace, actor=owner, arguments=arguments, source_text="salondaki TV"
        )
        is None
    )


@pytest.mark.django_db
def test_the_vocabulary_is_owner_rows_as_data(home, workspace, owner, firm):
    rows = home_phrases.call_vocabulary(workspace=workspace, actor=owner)
    assert {row["entity_id"] for row in rows} == {
        TV,
        "light.salon_tavan",
        "scene.film_gecesi",
        "media_player.yatak_hoparlor",
    }
    tv = next(row for row in rows if row["entity_id"] == TV)
    assert tv == {
        "entity_id": TV,
        "room": "Salon",
        "label": "TV",
        "aliases": ["televizyon"],
        "services": ["turn_on", "volume_down", "volume_set", "select_source", "volume_mute"],
    }
    assert home_phrases.call_vocabulary(workspace=firm, actor=owner) == []


# --- the ports -----------------------------------------------------------------


def test_connectors_registers_the_home_ports():
    assert home_ports.home_resolver() is home_phrases.resolve_home_command
    assert home_ports._DRAFT_CHECKS["ha_call_service"] is home_phrases.check_call_draft
    assert home_ports._DRAFT_VOCABULARY["ha_call_service"] is home_phrases.call_vocabulary


def test_a_failing_port_is_a_miss_or_a_refusal(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(home_ports, "_RESOLVER", [boom])
    monkeypatch.setattr(home_ports, "_DRAFT_CHECKS", {"t": boom})
    monkeypatch.setattr(home_ports, "_DRAFT_VOCABULARY", {"t": boom})
    assert home_ports.resolve_home_command(workspace=None, actor=None, text="x") is None
    assert (
        home_ports.check_draft("t", workspace=None, actor=None, arguments={}, source_text="")
        == home_ports.CHECK_FAILED_REPLY
    )
    assert home_ports.draft_vocabulary("t", workspace=None, actor=None) == []
    assert (
        home_ports.check_draft("other", workspace=None, actor=None, arguments={}, source_text="")
        is None
    )


def test_a_draft_resolution_must_name_its_tool_and_arguments():
    with pytest.raises(ValueError):
        home_ports.HomeResolution(outcome="draft", reply="x")
    with pytest.raises(ValueError):
        home_ports.HomeResolution(outcome="execute", reply="x")
