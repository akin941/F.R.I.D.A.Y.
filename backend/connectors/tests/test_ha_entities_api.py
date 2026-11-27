"""Phase 13 D11: the entity allowlist API
(`/api/v1/connectors/home-assistant/entities`, `/candidates`) -- CRUD,
error codes, the editor's pick list from a live `ha_states` read, a firm
workspace is 404, and role x endpoint over the owner's web session, a firm,
a kill-switched owner, the `cli`/`openai`/`hook` PATs, a node secret, a
`node_voice` token, a non-owner, a firm member, an agent and an anonymous
client."""

import pytest
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from audit.models import AuditLog
from connectors import home_entities
from connectors.manifest import get_manifest
from connectors.models import HomeEntity
from connectors.tests.fake_home_assistant import ha, salon_tv_state  # noqa: F401
from core.channels import CHANNEL_WEB
from workspaces import rooms
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/connectors/home-assistant/entities"
CANDIDATES = "/api/v1/connectors/home-assistant/candidates"
TV = "media_player.salon_tv"


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    return settings


@pytest.fixture
def member(db):
    return register_user(email="member@test.dev", password=PASSWORD, name="Member")


@pytest.fixture
def firm_ws(owner, member):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    return firm


@pytest.fixture
def bound(ha, enable, workspace):  # noqa: F811
    ha.states[TV] = salon_tv_state()
    return enable(get_manifest("home_assistant"), workspace)


def _body(workspace, **overrides):
    return {
        "workspace": str(workspace.pk),
        "entity_id": TV,
        "label": "TV",
        "services": ["turn_on", "turn_off"],
        **overrides,
    }


def test_crud_round_trip(owner, workspace, web_client):
    client = web_client(owner)
    salon = rooms.create_room(actor=owner, workspace=workspace, channel=CHANNEL_WEB, name="Salon")
    created = client.post(
        BASE, _body(workspace, room_id=str(salon.pk), aliases=["televizyon"]), format="json"
    )
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["entity_id"] == TV and body["domain"] == "media_player"
    assert body["room"] == {"id": str(salon.pk), "name": "Salon", "key": "salon"}
    assert body["services"] == ["turn_off", "turn_on"] and body["enabled"] is True

    listed = client.get(BASE, {"workspace": str(workspace.pk)}).json()
    assert [row["id"] for row in listed["results"]] == [body["id"]]
    assert listed["limits"] == {
        "aliases": 5,
        "alias_min_chars": 2,
        "alias_max_chars": 40,
        "label_max_chars": 60,
    }
    assert "select_source" in listed["services"]["media_player"]
    assert "lock" not in listed["services"] and "wake_on_lan" not in listed["services"]

    patched = client.patch(
        f"{BASE}/{body['id']}", {"enabled": False, "room_id": None}, format="json"
    )
    assert patched.status_code == 200, patched.content
    assert patched.json()["enabled"] is False and patched.json()["room"] is None

    assert client.delete(f"{BASE}/{body['id']}").status_code == 204
    assert not HomeEntity.objects.exists()
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"ha.entity_allowed", "ha.entity_changed", "ha.entity_removed"} <= actions


def test_error_codes(owner, workspace, web_client):
    client = web_client(owner)
    assert client.post(BASE, _body(workspace), format="json").status_code == 201
    conflict = client.post(BASE, _body(workspace), format="json")
    assert conflict.status_code == 409 and conflict.json()["code"] == "ha_entity.conflict"
    never = client.post(
        BASE, _body(workspace, entity_id="lock.front_door", services=["unlock"]), format="json"
    )
    assert never.status_code == 400 and never.json()["code"] == "ha_entity.not_allowed"
    toggle = client.post(
        BASE, _body(workspace, entity_id="light.salon", services=["toggle"]), format="json"
    )
    assert toggle.status_code == 400 and toggle.json()["code"] == "ha_entity.not_allowed"
    bad_id = client.post(BASE, _body(workspace, entity_id="no dot"), format="json")
    assert bad_id.status_code == 400 and bad_id.json()["code"] == "ha_entity.invalid"
    label = client.post(BASE, _body(workspace, entity_id="light.salon", label=" "), format="json")
    assert label.status_code == 400 and label.json()["code"] == "label.invalid"
    room = client.post(
        BASE,
        _body(workspace, entity_id="light.salon", room_id="00000000-0000-4000-8000-000000000000"),
        format="json",
    )
    assert room.status_code == 400 and room.json()["code"] == "label.unknown_room"
    unknown = client.post(BASE, _body(workspace, entity_id="light.salon", x=1), format="json")
    assert unknown.status_code == 400
    entity = HomeEntity.objects.get()
    # The entity id never changes through PATCH; an empty PATCH is refused.
    moved = client.patch(f"{BASE}/{entity.pk}", {"entity_id": "light.salon"}, format="json")
    assert moved.status_code == 400
    assert client.patch(f"{BASE}/{entity.pk}", {}, format="json").status_code == 400
    assert HomeEntity.objects.get().entity_id == TV


def test_a_firm_workspace_has_no_allowlist(owner, firm_ws, web_client):
    client = web_client(owner)
    assert client.get(BASE, {"workspace": str(firm_ws.pk)}).status_code == 404
    assert client.post(BASE, _body(firm_ws), format="json").status_code == 404
    assert client.get(CANDIDATES, {"workspace": str(firm_ws.pk)}).status_code == 404
    assert not HomeEntity.objects.exists()


def test_another_users_entity_is_404(owner, workspace, web_client):
    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other)
    theirs = HomeEntity.objects.create(
        workspace=other_ws, entity_id=TV, label="TV", key="tv", services=["turn_on"]
    )
    client = web_client(owner)
    assert client.get(BASE, {"workspace": str(other_ws.pk)}).status_code == 404
    assert client.patch(f"{BASE}/{theirs.pk}", {"enabled": False}, format="json").status_code == 404
    assert client.delete(f"{BASE}/{theirs.pk}").status_code == 404
    assert HomeEntity.objects.get(pk=theirs.pk).enabled is True


def test_candidates_come_from_one_live_read_and_never_write(
    bound,
    ha,  # noqa: F811
    owner,
    workspace,
    web_client,
):
    home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id=TV,
        label="TV",
        services=["turn_on"],
    )
    response = web_client(owner).get(CANDIDATES, {"workspace": str(workspace.pk)})
    assert response.status_code == 200, response.content
    rows = {row["entity_id"]: row for row in response.json()["results"]}
    # Callable domains only: no sensor, lock or script is offered.
    assert set(rows) == {"climate.salon", "light.salon", "media_player.tv", TV}
    tv = rows[TV]
    assert tv["allowlisted"] is True and rows["light.salon"]["allowlisted"] is False
    assert tv["source_list"] == ["TV", "HDMI 1", "HDMI 2"]
    assert tv["friendly_name"] == "Samsung QE55"
    assert "select_source" in tv["services"] and "toggle" not in tv["services"]
    assert "SECRET-TITLE" not in response.content.decode()
    assert "PICTURE-TOKEN" not in response.content.decode()
    assert ha.posts == []
    assert HomeEntity.objects.count() == 1
    read = AuditLog.objects.get(action="connector.read")
    assert read.metadata["tool"] == "ha_states" and read.metadata["outcome"] == "ok"


def test_candidates_need_an_enabled_binding(owner, workspace, web_client):
    response = web_client(owner).get(CANDIDATES, {"workspace": str(workspace.pk)})
    assert response.status_code == 502
    assert response.json()["code"] == "ha_entity.read_failed"


@pytest.fixture
def principals(owner, workspace, member, firm_ws, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            return bearer(make_pat(owner, workspace, principal.removeprefix("owner-pat-"))[0])
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

OK = {"list": 200, "create": 201, "patch": 200, "delete": 204, "candidates": 200}


@pytest.mark.parametrize(("principal", "expected"), MATRIX)
@pytest.mark.parametrize("endpoint", sorted(OK))
def test_role_matrix(bound, principals, owner, workspace, firm_ws, endpoint, principal, expected):
    entity = home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id="light.salon",
        label="Tavan",
        services=["turn_on"],
    )
    # A row planted in the firm (only the ORM could): the firm principals
    # address it; the detail view answers 404 for any non-personal row.
    firm_entity = HomeEntity.objects.create(
        workspace=firm_ws, entity_id="light.salon", label="Tavan", key="tavan", services=["turn_on"]
    )
    firm_side = principal in ("owner-firm", "firm-member")
    target = firm_ws if firm_side else workspace
    row = firm_entity if firm_side else entity
    client = principals(principal)
    calls = {
        "list": lambda: client.get(BASE, {"workspace": str(target.pk)}),
        "create": lambda: client.post(BASE, _body(target), format="json"),
        "patch": lambda: client.patch(f"{BASE}/{row.pk}", {"label": "Avize"}, format="json"),
        "delete": lambda: client.delete(f"{BASE}/{row.pk}"),
        "candidates": lambda: client.get(CANDIDATES, {"workspace": str(target.pk)}),
    }
    response = calls[endpoint]()
    if expected == "ok":
        assert response.status_code == OK[endpoint], response.content
        return
    assert response.status_code == expected, (endpoint, response.status_code, response.content)
    for unchanged in (entity, firm_entity):
        unchanged.refresh_from_db()
        assert unchanged.label == "Tavan"
    assert HomeEntity.objects.count() == 2
    if response.status_code != 401:
        assert "Tavan" not in response.content.decode()
