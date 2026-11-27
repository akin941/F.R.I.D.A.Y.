# ruff: noqa: F811 -- pytest fixtures imported from the helpers are redefined
"""Phase 13b D6/D11 (ADR-018, branch 1b): the owner API of the direct media
class -- `GET/PATCH /api/v1/connectors/home-assistant/direct` (kill switch,
volume maximum, the read-only env switch, state, limits, the closed list,
the flagged count; audited `ha.direct_settings_changed`), `GET
.../direct/calls` (newest first, <= 100, `for_actor()`), and role x
endpoint over the entity endpoints' `direct` field and both new endpoints:
the owner's web session, his PATs of every kind (`cli`, `openai`, `hook`),
a node secret, a `node_voice` token, an agent principal, a firm, a firm
member, another user and an anonymous client. Two users' rows never mix."""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from audit.models import AuditLog
from connectors import home_direct, home_entities
from connectors.models import HomeDirectCall, HomeDirectSettings, HomeEntity
from connectors.tests.fake_home_assistant import allow
from connectors.tests.home_direct_helpers import TV, classify, flagged, make_call
from core.channels import CHANNEL_WEB
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
ENTITIES = "/api/v1/connectors/home-assistant/entities"
DIRECT = "/api/v1/connectors/home-assistant/direct"
CALLS = "/api/v1/connectors/home-assistant/direct/calls"


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_DIRECT_MEDIA_KILL_SWITCH = False
    settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 30
    settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 10
    settings.HA_DIRECT_VOLUME_MAX = 0.6
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


# --- GET / PATCH /direct ---------------------------------------------------------


def test_the_first_get_creates_the_defaults(owner, workspace, web_client):
    response = web_client(owner).get(DIRECT, {"workspace": str(workspace.pk)})
    assert response.status_code == 200, response.content
    assert response.json() == {
        "workspace": str(workspace.pk),
        "kill_switch": False,
        "env_kill_switch": False,
        "state": "on",
        "volume_max": 0.6,
        "rate_global_per_minute": 30,
        "rate_entity_per_minute": 10,
        "services": sorted(home_direct.DIRECT_MEDIA_SERVICES),
        "digest": home_direct.ADR_018_DIGEST,
        "flagged_count": 0,
    }
    row = HomeDirectSettings.objects.get()
    assert (row.workspace_id, row.kill_switch, row.volume_max) == (
        workspace.pk,
        False,
        Decimal("0.60"),
    )
    # A read is not a change: no audit row.
    assert not AuditLog.objects.filter(action="ha.direct_settings_changed").exists()


def test_patch_changes_and_audits(owner, workspace, web_client):
    client = web_client(owner)
    response = client.patch(
        DIRECT, {"workspace": str(workspace.pk), "kill_switch": True}, format="json"
    )
    assert response.status_code == 200, response.content
    assert (response.json()["kill_switch"], response.json()["state"]) == (True, "off_owner")
    response = client.patch(
        DIRECT, {"workspace": str(workspace.pk), "volume_max": 0.35}, format="json"
    )
    assert response.json()["volume_max"] == 0.35
    # A no-op writes nothing.
    client.patch(DIRECT, {"workspace": str(workspace.pk), "volume_max": "0.35"}, format="json")
    audits = AuditLog.objects.filter(action="ha.direct_settings_changed").order_by("created_at")
    assert [a.metadata for a in audits] == [
        {"fields": ["kill_switch"], "kill_switch": True},
        {"fields": ["volume_max"], "volume_max": "0.35"},
    ]
    assert HomeDirectSettings.objects.get().volume_max == Decimal("0.35")


@pytest.mark.parametrize(
    "body",
    [
        {"volume_max": 1.2},
        {"volume_max": -0.1},
        {"volume_max": "loud"},
        {"volume_max": True},
        {"kill_switch": "true"},
        {"kill_switch": 1},
        {"env_kill_switch": False},
        {"state": "on"},
        {"services": ["turn_on"]},
        {"rate_global_per_minute": 1000},
        {},
    ],
)
def test_patch_refuses_bad_or_read_only_fields(owner, workspace, web_client, body):
    response = web_client(owner).patch(
        DIRECT, {"workspace": str(workspace.pk), **body}, format="json"
    )
    assert response.status_code == 400, body
    assert not HomeDirectSettings.objects.filter(kill_switch=True).exists()
    assert not AuditLog.objects.filter(action="ha.direct_settings_changed").exists()


def test_the_service_refuses_an_out_of_range_maximum(owner, workspace):
    for value in ("1.01", "-0.01", "nan", True):
        with pytest.raises(home_direct.HomeDirectInvalid):
            home_direct.update_settings(
                actor=owner, workspace=workspace, channel=CHANNEL_WEB, volume_max=value
            )
    with pytest.raises(home_direct.HomeDirectInvalid):
        home_direct.update_settings(
            actor=owner, workspace=workspace, channel=CHANNEL_WEB, kill_switch="yes"
        )


def test_the_env_kill_switch_is_shown_and_wins(owner, workspace, web_client, ha_settings):
    ha_settings.HA_DIRECT_MEDIA_KILL_SWITCH = True
    body = web_client(owner).get(DIRECT, {"workspace": str(workspace.pk)}).json()
    assert (body["env_kill_switch"], body["kill_switch"], body["state"]) == (True, False, "off_env")


@pytest.mark.parametrize(
    "limit", ["HA_DIRECT_RATE_GLOBAL_PER_MINUTE", "HA_DIRECT_RATE_ENTITY_PER_MINUTE"]
)
def test_a_zero_limit_reads_off_env(owner, workspace, web_client, ha_settings, limit):
    # Branch 1d: a limit of 0 disables the class (D8), so the state the
    # panel and the HUD show is `off_env`, like the env kill switch.
    setattr(ha_settings, limit, 0)
    body = web_client(owner).get(DIRECT, {"workspace": str(workspace.pk)}).json()
    assert (body["env_kill_switch"], body["state"]) == (False, "off_env")


def test_env_limits_and_the_flagged_count_are_shown(owner, workspace, web_client, ha_settings):
    ha_settings.HA_DIRECT_RATE_GLOBAL_PER_MINUTE = 5
    ha_settings.HA_DIRECT_RATE_ENTITY_PER_MINUTE = 0
    flagged(workspace)
    flagged(workspace, "media_player.yatak", enabled=False)
    allow(workspace, "media_player.mutfak")
    body = web_client(owner).get(DIRECT, {"workspace": str(workspace.pk)}).json()
    assert (body["rate_global_per_minute"], body["rate_entity_per_minute"]) == (5, 0)
    assert body["flagged_count"] == 1


def test_the_settings_change_what_the_classifier_reads(owner, workspace, web_client):
    client = web_client(owner)
    client.patch(DIRECT, {"workspace": str(workspace.pk), "kill_switch": True}, format="json")
    flagged(workspace)
    assert classify(owner, workspace).reason == home_direct.KILL_SWITCH_OWNER


# --- GET /direct/calls --------------------------------------------------------------


def test_calls_are_listed_newest_first_and_bounded(owner, workspace, web_client):
    first = make_call(workspace, owner, service="media_pause")
    second = make_call(
        workspace,
        owner,
        service="volume_set",
        data={"volume_level": 0.6},
        requested_data={"volume_level": 0.9},
        origin=HomeDirectCall.Origin.NODE,
    )
    client = web_client(owner)
    body = client.get(CALLS, {"workspace": str(workspace.pk)}).json()
    assert [row["id"] for row in body["results"]] == [str(second.pk), str(first.pk)]
    row = body["results"][0]
    assert row["requested_data"] == {"volume_level": 0.9} and row["origin"] == "node"
    assert row["status"] == "succeeded" and row["entity_id"] == TV
    one = client.get(CALLS, {"workspace": str(workspace.pk), "limit": 1}).json()
    assert [row["id"] for row in one["results"]] == [str(second.pk)]
    assert client.get(CALLS, {"workspace": str(workspace.pk), "limit": 101}).status_code == 400
    assert client.get(CALLS, {"workspace": str(workspace.pk), "limit": 0}).status_code == 400
    for _ in range(105):
        make_call(workspace, owner)
    many = client.get(CALLS, {"workspace": str(workspace.pk), "limit": 100}).json()
    assert len(many["results"]) == 100
    assert len(client.get(CALLS, {"workspace": str(workspace.pk)}).json()["results"]) == 20


def test_a_removed_entity_keeps_its_calls(owner, workspace, web_client):
    entity = home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id=TV,
        label="TV",
        services=["media_pause"],
        direct=True,
    )
    make_call(workspace, owner, home_entity=entity)
    home_entities.delete_entity(actor=owner, entity=entity, channel=CHANNEL_WEB)
    (row,) = web_client(owner).get(CALLS, {"workspace": str(workspace.pk)}).json()["results"]
    assert (row["home_entity_id"], row["entity_id"]) == (None, TV)


# --- isolation -------------------------------------------------------------------------


def test_two_users_never_see_each_others_settings_calls_or_flags(owner, workspace, web_client):
    other = register_user(email="other@test.dev", password=PASSWORD)
    other_ws = Workspace.objects.get(owner=other, type=Workspace.Type.PERSONAL)
    theirs = flagged(other_ws)
    HomeDirectSettings.objects.create(workspace=other_ws, kill_switch=True, volume_max="0.20")
    make_call(other_ws, other)
    mine = flagged(workspace)
    client = web_client(owner)
    for url in (DIRECT, CALLS):
        assert client.get(url, {"workspace": str(other_ws.pk)}).status_code == 404
    patch = client.patch(
        DIRECT, {"workspace": str(other_ws.pk), "kill_switch": False}, format="json"
    )
    assert patch.status_code == 404
    assert (
        client.patch(f"{ENTITIES}/{theirs.pk}", {"direct": False}, format="json").status_code == 404
    )
    assert HomeDirectSettings.objects.get(workspace=other_ws).kill_switch is True
    assert HomeEntity.objects.get(pk=theirs.pk).direct is True
    # The owner's own views count and list only his rows.
    assert client.get(CALLS, {"workspace": str(workspace.pk)}).json()["results"] == []
    assert client.get(DIRECT, {"workspace": str(workspace.pk)}).json()["flagged_count"] == 1
    assert mine.direct is True
    # Neither classifies the other's TV.
    assert classify(owner, other_ws).reason == home_direct.NOT_OWNER
    assert classify(other, other_ws).reason == home_direct.NOT_OWNER


def test_a_firm_workspace_has_no_direct_settings(owner, firm_ws, web_client):
    client = web_client(owner)
    assert client.get(DIRECT, {"workspace": str(firm_ws.pk)}).status_code == 404
    assert client.get(CALLS, {"workspace": str(firm_ws.pk)}).status_code == 404
    assert (
        client.patch(
            DIRECT, {"workspace": str(firm_ws.pk), "kill_switch": True}, format="json"
        ).status_code
        == 404
    )
    assert not HomeDirectSettings.objects.exists()
    with pytest.raises(home_direct.HomeDirectPermissionDenied):
        home_direct.get_settings(actor=owner, workspace=firm_ws, channel=CHANNEL_WEB)


# --- role x endpoint --------------------------------------------------------------------


@pytest.fixture
def principals(owner, workspace, member, firm_ws, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
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

OK = {
    "entity-create-direct": 201,
    "entity-patch-direct": 200,
    "entity-list": 200,
    "direct-get": 200,
    "direct-patch": 200,
    "calls-get": 200,
}


@pytest.mark.parametrize(("principal", "expected"), MATRIX)
@pytest.mark.parametrize("endpoint", sorted(OK))
def test_role_matrix(principals, owner, workspace, firm_ws, endpoint, principal, expected):
    entity = home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id=TV,
        label="TV",
        services=["media_pause"],
    )
    settings_row = home_direct.get_settings(actor=owner, workspace=workspace, channel=CHANNEL_WEB)
    make_call(workspace, owner, data={"marker": "CALL-MARKER"})
    # A row planted in the firm (only the ORM could): the firm principals
    # address it; every direct view answers 404 for a non-personal row.
    firm_entity = HomeEntity.objects.create(
        workspace=firm_ws, entity_id=TV, label="TV", key="tv", services=["media_pause"]
    )
    firm_side = principal in ("owner-firm", "firm-member")
    target = firm_ws if firm_side else workspace
    row = firm_entity if firm_side else entity
    client = principals(principal)
    ws = str(target.pk)
    calls = {
        "entity-create-direct": lambda: client.post(
            ENTITIES,
            {
                "workspace": ws,
                "entity_id": "media_player.yatak",
                "label": "Yatak",
                "services": ["media_pause"],
                "direct": True,
            },
            format="json",
        ),
        "entity-patch-direct": lambda: client.patch(
            f"{ENTITIES}/{row.pk}", {"direct": True}, format="json"
        ),
        "entity-list": lambda: client.get(ENTITIES, {"workspace": ws}),
        "direct-get": lambda: client.get(DIRECT, {"workspace": ws}),
        "direct-patch": lambda: client.patch(
            DIRECT, {"workspace": ws, "kill_switch": True, "volume_max": 1}, format="json"
        ),
        "calls-get": lambda: client.get(CALLS, {"workspace": ws}),
    }
    response = calls[endpoint]()
    if expected == "ok":
        assert response.status_code == OK[endpoint], response.content
        return
    assert response.status_code == expected, (endpoint, response.status_code, response.content)
    # Nothing changed anywhere.
    for unchanged in (entity, firm_entity):
        unchanged.refresh_from_db()
        assert unchanged.direct is False
    assert HomeEntity.objects.count() == 2
    settings_row.refresh_from_db()
    assert (settings_row.kill_switch, settings_row.volume_max) == (False, Decimal("0.60"))
    assert HomeDirectSettings.objects.count() == 1
    assert not AuditLog.objects.filter(
        action__in=["ha.entity_direct_changed", "ha.direct_settings_changed"]
    ).exists()
    assert "CALL-MARKER" not in response.content.decode()
