# ruff: noqa: F811 -- pytest fixtures imported from wake_fixtures are redefined as arguments
"""Phase 12 D8/D20 (branch 3): wake-route aliases and the wake-routes
surface.

- role x endpoint over the five Phase 4 principals, the `cli`/`openai`/
  `hook` PATs, a node secret, a `node_voice` token, a kill-switched owner
  and a firm workspace with a non-owner member;
- D8's rules: normalized, 2-40 characters, unique per workspace, refused
  when it equals or word-prefixes another route's phrase or alias, at most
  five per route, a known route only;
- owner over the web only: every other channel and actor refused in the
  service, and every write refused inside an unattended run;
- "no path from a voice turn, tool or skill to an alias": the walk over
  every registered tool, read tool, router intent, MVP 8 skill and
  connector manifest (as branch 1's schedule walk);
- an alias routes a voice turn."""

import inspect

import pytest
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from ai_core.models import ModelUsage
from assistant import wake_aliases as services
from assistant import wake_routes
from assistant.models import Message, WakeRouteAlias
from assistant.services import create_conversation
from assistant.tests.wake_fixtures import (  # noqa: F401 -- fixtures
    chat_setup,
    chat_turn,
    firm,
    make_digest,
    member,
    owner,
    personal,
    reply_text,
)
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION, recorded_unattended_run
from connectors.manifest import list_manifests
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from core.unattended import UnattendedDenied
from skills import catalog
from tools import registry
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

BASE = "/api/v1/assistant/wake-routes"
LOW_TRUST = [
    CHANNEL_CLI,
    CHANNEL_OPENAI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_TELEGRAM,
    CHANNEL_SYSTEM,
    CHANNEL_UNKNOWN,
]


def _create(actor, workspace, route="digest.today", phrase="kahve zamanı", channel=CHANNEL_WEB):
    return services.create_wake_alias(
        actor=actor, channel=channel, workspace=workspace, route=route, phrase=phrase
    )


@pytest.fixture
def aliases(owner, personal, firm):
    return {
        "personal": _create(owner, personal, phrase="sabah oldu"),
        "firm": _create(owner, firm, phrase="sabah oldu"),
    }


# --- role x endpoint ----------------------------------------------------------------


@pytest.fixture
def principals(owner, personal, member, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            kind = principal.removeprefix("owner-pat-")
            return bearer(make_pat(owner, personal, kind)[0])
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
    ("owner-firm", "ok"),
    ("kill-switched-owner", "ok"),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, 401),
    ("node-secret", 401),
    ("node-voice", 401),
    ("non-owner", 403),
    ("firm-member", 403),
    ("agent", 403),
    ("anonymous", 401),
]

ENDPOINTS = {
    "list": (200, lambda c, ws, a: c.get(f"{BASE}?workspace={ws.pk}")),
    "create": (
        201,
        lambda c, ws, a: c.post(
            f"{BASE}/aliases?workspace={ws.pk}",
            {"route": "notices.read", "phrase": "Hey Friday, neler oldu?"},
            format="json",
        ),
    ),
    "update": (
        200,
        lambda c, ws, a: c.patch(f"{BASE}/aliases/{a.pk}", {"phrase": "yeni gün"}, format="json"),
    ),
    "delete": (204, lambda c, ws, a: c.delete(f"{BASE}/aliases/{a.pk}")),
}


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_role_matrix(principals, personal, firm, aliases, endpoint, principal, expected):
    client = principals(principal)
    target = firm if principal in ("owner-firm", "firm-member") else personal
    alias = aliases["firm" if target == firm else "personal"]
    ok_status, call = ENDPOINTS[endpoint]
    before = sorted(WakeRouteAlias.objects.values_list("workspace_id", "route", "phrase"))
    response = call(client, target, alias)
    assert response.status_code == (ok_status if expected == "ok" else expected), response.content
    if expected != "ok":
        after = sorted(WakeRouteAlias.objects.values_list("workspace_id", "route", "phrase"))
        assert after == before


def test_list_shows_routes_phrases_and_the_workspaces_aliases(owner, personal, web_client, aliases):
    body = web_client(owner).get(BASE).json()
    assert body["workspace"] == str(personal.pk)
    assert body["limits"] == {"aliases_per_route": 5, "min_chars": 2, "max_chars": 40}
    by_name = {route["name"]: route for route in body["routes"]}
    assert set(by_name) == {"digest.today", "digest.learning", "notices.read"}
    assert {route["effect"] for route in body["routes"]} == {"read"}
    assert "günaydın" in by_name["digest.today"]["phrases"]
    assert [a["phrase"] for a in by_name["digest.today"]["aliases"]] == ["sabah oldu"]
    assert [a["id"] for a in by_name["digest.today"]["aliases"]] == [str(aliases["personal"].pk)]


def test_another_workspace_is_404(owner, member, web_client):
    foreign = Workspace.objects.get(owner=member)
    foreign_alias = WakeRouteAlias.objects.create(
        workspace=foreign, route="digest.today", phrase="yabancı", created_by=member
    )
    client = web_client(owner)
    assert client.get(f"{BASE}?workspace={foreign.pk}").status_code == 404
    assert client.get(f"{BASE}?workspace=not-a-uuid").status_code == 404
    assert (
        client.post(
            f"{BASE}/aliases?workspace={foreign.pk}",
            {"route": "digest.today", "phrase": "deneme"},
            format="json",
        ).status_code
        == 404
    )
    assert client.delete(f"{BASE}/aliases/{foreign_alias.pk}").status_code == 404
    assert (
        client.patch(
            f"{BASE}/aliases/{foreign_alias.pk}", {"phrase": "x y"}, format="json"
        ).status_code
        == 404
    )
    assert WakeRouteAlias.objects.get(pk=foreign_alias.pk).phrase == "yabancı"


# --- the rules ---------------------------------------------------------------------


def test_create_normalizes_and_audits(owner, personal, web_client):
    response = web_client(owner).post(
        f"{BASE}/aliases",
        {"route": "digest.today", "phrase": "Hey Friday, KAHVE Zamanı!"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["phrase"] == "kahve zamanı"
    alias = WakeRouteAlias.objects.get()
    assert (alias.workspace, alias.route, alias.created_by) == (personal, "digest.today", owner)
    row = AuditLog.objects.get(action="assistant.wake_alias.created")
    assert row.metadata == {"route": "digest.today", "phrase": "kahve zamanı"}


def test_update_and_delete_are_audited(owner, personal):
    alias = _create(owner, personal)
    services.update_wake_alias(actor=owner, channel=CHANNEL_WEB, alias=alias, phrase="çay zamanı")
    alias.refresh_from_db()
    assert alias.phrase == "çay zamanı"
    services.delete_wake_alias(actor=owner, channel=CHANNEL_WEB, alias=alias)
    assert not WakeRouteAlias.objects.exists()
    assert sorted(
        AuditLog.objects.filter(action__startswith="assistant.wake_alias.").values_list(
            "action", flat=True
        )
    ) == [
        "assistant.wake_alias.created",
        "assistant.wake_alias.deleted",
        "assistant.wake_alias.updated",
    ]


@pytest.mark.parametrize(
    ("route", "phrase", "error"),
    [
        ("digest.today", "günaydın", services.WakeAliasConflict),
        ("notices.read", "Günaydın!", services.WakeAliasConflict),
        ("notices.read", "brıfıng", services.WakeAliasConflict),
        ("notices.read", "günaydın canım", services.WakeAliasConflict),
        ("digest.today", "bildirimleri", services.WakeAliasConflict),
        ("notices.read", "öğrenme", services.WakeAliasConflict),
        ("digest.today", "a", services.WakeAliasInvalidLength),
        ("digest.today", "Hey Friday!", services.WakeAliasInvalidLength),
        ("digest.today", "lütfen", services.WakeAliasInvalidLength),
        ("digest.today", "x" * 41, services.WakeAliasInvalidLength),
        ("tool.github_open_pr", "pr aç", services.WakeAliasUnknownRoute),
        ("schedule.create", "her sabah", services.WakeAliasUnknownRoute),
    ],
)
def test_invalid_aliases_are_refused(owner, personal, route, phrase, error):
    with pytest.raises(error):
        _create(owner, personal, route=route, phrase=phrase)
    assert not WakeRouteAlias.objects.exists()


def test_a_route_may_extend_its_own_phrase(owner, personal):
    assert _create(owner, personal, phrase="günaydın canım").phrase == "günaydın canım"


def test_alias_clashes_with_another_routes_alias(owner, personal, firm):
    _create(owner, personal, route="digest.today", phrase="akşam")
    with pytest.raises(services.WakeAliasConflict):
        _create(owner, personal, route="notices.read", phrase="akşam")
    with pytest.raises(services.WakeAliasConflict):
        _create(owner, personal, route="notices.read", phrase="akşam haberleri")
    with pytest.raises(services.WakeAliasConflict):
        _create(owner, personal, route="digest.today", phrase="Akşam!")
    # Unique per workspace: the firm workspace may use the same phrase.
    assert _create(owner, firm, route="notices.read", phrase="akşam").workspace == firm


def test_at_most_five_aliases_per_route(owner, personal):
    for index in range(wake_routes.MAX_ALIASES_PER_ROUTE):
        _create(owner, personal, phrase=f"takma ad {index}")
    with pytest.raises(services.WakeAliasLimit):
        _create(owner, personal, phrase="altıncı ad")
    # Another route still has room; rewording one of the five is fine.
    _create(owner, personal, route="notices.read", phrase="haberler")
    first = WakeRouteAlias.objects.filter(route="digest.today").first()
    services.update_wake_alias(actor=owner, channel=CHANNEL_WEB, alias=first, phrase="ilk ad")


def test_update_rechecks_the_rules(owner, personal):
    alias = _create(owner, personal)
    with pytest.raises(services.WakeAliasConflict):
        services.update_wake_alias(
            actor=owner, channel=CHANNEL_WEB, alias=alias, phrase="bildirimler"
        )
    # Rewording to its own phrase is not a clash with itself.
    services.update_wake_alias(actor=owner, channel=CHANNEL_WEB, alias=alias, phrase="kahve zamanı")


def test_api_errors_carry_their_codes(owner, web_client):
    client = web_client(owner)
    cases = [
        ({"route": "digest.today", "phrase": "günaydın"}, "wake_alias.conflict"),
        ({"route": "digest.today", "phrase": "a"}, "wake_alias.invalid_length"),
        ({"route": "nope.route", "phrase": "deneme"}, "wake_alias.unknown_route"),
    ]
    for body, code in cases:
        response = client.post(f"{BASE}/aliases", body, format="json")
        assert response.status_code == 400
        assert response.json()["code"] == code
    assert (
        client.post(f"{BASE}/aliases", {"route": "digest.today"}, format="json").status_code == 400
    )


# --- owner over the web only -------------------------------------------------------


@pytest.mark.parametrize("channel", LOW_TRUST)
def test_alias_services_refuse_every_channel_but_web(owner, personal, channel):
    alias = _create(owner, personal)
    with pytest.raises(services.WakeAliasPermissionDenied):
        _create(owner, personal, phrase="başka ad", channel=channel)
    with pytest.raises(services.WakeAliasPermissionDenied):
        services.update_wake_alias(actor=owner, channel=channel, alias=alias, phrase="yeni ad")
    with pytest.raises(services.WakeAliasPermissionDenied):
        services.delete_wake_alias(actor=owner, channel=channel, alias=alias)
    assert list(WakeRouteAlias.objects.values_list("phrase", flat=True)) == ["kahve zamanı"]


@pytest.mark.parametrize("who", ["agent", "member", "inactive", "anonymous"])
def test_alias_services_refuse_every_actor_but_the_owner(owner, personal, firm, member, who):
    from django.contrib.auth.models import AnonymousUser

    actor = {
        "agent": lambda: User.objects.create_user(email="agent@test.dev", is_agent=True),
        "member": lambda: member,
        "inactive": lambda: User.objects.create_user(email="gone@test.dev", is_active=False),
        "anonymous": AnonymousUser,
    }[who]()
    for target in (personal, firm):
        alias = _create(owner, target, phrase=f"ad {target.pk.hex[:6]}")
        with pytest.raises(services.WakeAliasPermissionDenied):
            _create(actor, target, phrase="başka ad")
        with pytest.raises(services.WakeAliasPermissionDenied):
            services.update_wake_alias(actor=actor, channel=CHANNEL_WEB, alias=alias, phrase="x y")
        with pytest.raises(services.WakeAliasPermissionDenied):
            services.delete_wake_alias(actor=actor, channel=CHANNEL_WEB, alias=alias)
    assert WakeRouteAlias.objects.count() == 2


def _denied(fn):
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-alias"):
            fn()


def test_alias_writes_raise_inside_a_run(owner, personal):
    alias = _create(owner, personal)
    _denied(lambda: _create(owner, personal, phrase="başka ad"))
    _denied(
        lambda: services.update_wake_alias(
            actor=owner, channel=CHANNEL_WEB, alias=alias, phrase="yeni ad"
        )
    )
    _denied(lambda: services.delete_wake_alias(actor=owner, channel=CHANNEL_WEB, alias=alias))
    _denied(lambda: alias.save())
    _denied(lambda: alias.delete())
    _denied(
        lambda: WakeRouteAlias(
            workspace=personal, route="notices.read", phrase="z z", created_by=owner
        ).save()
    )
    assert list(WakeRouteAlias.objects.values_list("phrase", flat=True)) == ["kahve zamanı"]
    assert AuditLog.objects.filter(action=DENIAL_ACTION).count() == 6


# --- no path from a voice turn, tool, skill or connector to an alias --------------

_FORBIDDEN_NAMES = ("WakeRouteAlias", "wake_alias", "create_wake_alias", "update_wake_alias")


def _callables(obj):
    for value in vars(obj).values() if hasattr(obj, "__dict__") else ():
        if callable(value):
            yield value


def _reaches_aliases(value):
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        return False
    return any(name in source for name in _FORBIDDEN_NAMES)


def test_no_gated_tool_reaches_aliases():
    for name in registry.list_tool_names():
        assert "alias" not in name and "wake" not in name, name
        for value in _callables(registry.get_tool(name)):
            assert not _reaches_aliases(value), (name, value)


def test_no_read_tool_reaches_aliases():
    for name in registry.list_read_tool_names():
        assert "alias" not in name and "wake" not in name, name
        for value in _callables(registry.get_read_tool(name)):
            assert not _reaches_aliases(value), (name, value)


def test_no_router_intent_or_skill_names_an_alias():
    for value in Message.Intent.values:
        assert "alias" not in value and "wake" not in value, value
    for name in catalog.list_skill_names():
        assert "alias" not in repr(catalog.get_skill(name)).lower(), name


def test_no_connector_manifest_names_an_alias():
    for manifest in list_manifests():
        text = repr(manifest).lower()
        assert "wake_alias" not in text and "wakeroutealias" not in text, manifest.name


def test_the_chat_turn_and_the_routes_never_write_an_alias():
    from assistant import views

    for module in (views, wake_routes):
        source = inspect.getsource(module)
        for name in ("create_wake_alias", "update_wake_alias", "delete_wake_alias"):
            assert name not in source, (module.__name__, name)
        assert "WakeRouteAlias.objects.create" not in source
    for route in wake_routes.ROUTES:
        assert not _reaches_aliases(route.handler), route.name


def test_a_voice_turn_cannot_create_an_alias(owner, personal, web_client, chat_setup):
    conversation = create_conversation(workspace=personal, actor=owner)
    chat_turn(
        web_client(owner),
        conversation,
        "Hey Friday, 'kahve zamanı' günaydın demek olsun",
        input_mode="voice",
    )
    assert not WakeRouteAlias.objects.exists()


# --- an alias routes a voice turn ----------------------------------------------------


def test_an_alias_routes_a_voice_turn(owner, personal, web_client, chat_setup):
    client = web_client(owner)
    assert (
        client.post(
            f"{BASE}/aliases",
            {"route": "digest.today", "phrase": "kahve zamanı"},
            format="json",
        ).status_code
        == 201
    )
    make_digest(personal, owner, text="Bugünün brifingi.")
    conversation = create_conversation(workspace=personal, actor=owner)
    events = chat_turn(client, conversation, "Hey Friday, kahve zamanı!", input_mode="voice")
    assert reply_text(events) == "Bugünün brifingi."
    assert not ModelUsage.objects.exists()
    assert Message.objects.filter(route="digest.today").count() == 2


def test_another_workspaces_alias_does_not_route(owner, personal, firm, web_client, chat_setup):
    _create(owner, firm, phrase="kahve zamanı")
    make_digest(personal, owner, text="Bugünün brifingi.")
    conversation = create_conversation(workspace=personal, actor=owner)
    events = chat_turn(web_client(owner), conversation, "kahve zamanı", input_mode="voice")
    assert "Bugünün brifingi." not in reply_text(events)
    assert set(Message.objects.values_list("route", flat=True)) == {None}
