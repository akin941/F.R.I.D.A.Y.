"""Phase 13 D11/D12: the entity allowlist as a gate on every Home Assistant
call -- refused at request time, at draft-time validation
(`allowlist_refusal`, what branch 6's home routes call) and as the execution
precondition; removing or disabling an entity while its approval is pending
sends nothing (`connector_changed`); an empty allowlist allows no entity
call while Wake-on-LAN stays on `HA_WOL_ALLOWED_MACS`; the v2 pins; and the
registry walk: nothing but the owner-web service writes `HomeEntity`."""

import ast
import dataclasses
import functools
from pathlib import Path
from types import SimpleNamespace

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from connectors import home_entities
from connectors.bridges import home_assistant as bridge
from connectors.manifest import get_manifest, list_manifests
from connectors.manifests import home_assistant as ha_manifest
from connectors.models import ConnectorWriteExecution, HomeEntity
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.channels import CHANNEL_WEB
from tools.registry import get_read_tool, get_tool, list_read_tool_names, list_tool_names
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

URL = "/api/v1/approvals/requests"
GATE = "ha_call_service"
MANIFEST = get_manifest("home_assistant")
TV = "media_player.salon_tv"
TURN_ON = {"domain": "media_player", "service": "turn_on", "entity_id": TV, "data": {}}
WOL = {
    "domain": "wake_on_lan",
    "service": "send_magic_packet",
    "data": {"mac": "aa:bb:cc:dd:ee:ff"},
}
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_WOL_ALLOWED_MACS = ["AA:BB:CC:DD:EE:FF"]
    return settings


@pytest.fixture
def bound(ha, enable, workspace):  # noqa: F811
    ha.states[TV] = salon_tv_state()
    return enable(MANIFEST, workspace)


@pytest.fixture
def approve(web_client, owner, committed):
    def _approve(request_id):
        with committed():
            return web_client(owner).post(f"{URL}/{request_id}/approve")

    return _approve


def _request(web_client, owner, workspace, arguments):
    return web_client(owner).post(
        URL,
        {"workspace": str(workspace.id), "tool_name": GATE, "payload": {"arguments": arguments}},
        format="json",
    )


# --- the allowlist check itself (also the draft-time validator) ---


def test_allowlist_refusal_codes(workspace, owner):
    assert bridge.allowlist_refusal(workspace, TURN_ON) == "entity_not_allowed"
    entity = allow(workspace, TV, ["turn_off"])
    assert bridge.allowlist_refusal(workspace, TURN_ON) == "service_not_allowed"
    entity.services = ["turn_off", "turn_on"]
    entity.save()
    assert bridge.allowlist_refusal(workspace, TURN_ON) is None
    entity.enabled = False
    entity.save()
    assert bridge.allowlist_refusal(workspace, TURN_ON) == "entity_not_allowed"
    assert bridge.allowlist_refusal(None, TURN_ON) == "entity_not_allowed"
    # Wake-on-LAN has no entity: the MAC allowlist governs it, not this.
    assert bridge.allowlist_refusal(workspace, WOL) is None


def test_another_workspaces_allowlist_never_counts(workspace):
    other = register_user(email="other@test.dev", password="Str0ngDevPass!42")
    allow(Workspace.objects.get(owner=other), TV)
    assert bridge.allowlist_refusal(workspace, TURN_ON) == "entity_not_allowed"


# --- request time ---


def test_an_allowed_domain_entity_not_on_the_allowlist_is_refused_at_request(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    response = _request(web_client, owner, workspace, TURN_ON)
    assert response.status_code == 400
    assert response.json()["detail"].startswith("entity_not_allowed")
    assert not ApprovalRequest.objects.exists()
    # Refused before any network traffic (not even the state read).
    assert ha.requests == [r for r in ha.requests if r[1] == "/api/config"]


def test_a_service_outside_the_entitys_list_is_refused_at_request(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    allow(workspace, TV, ["volume_set"])
    response = _request(web_client, owner, workspace, TURN_ON)
    assert response.status_code == 400
    assert response.json()["detail"].startswith("service_not_allowed")
    assert not ApprovalRequest.objects.exists() and ha.posts == []


def test_a_disabled_entity_is_refused_at_request(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    allow(workspace, TV, enabled=False)
    assert _request(web_client, owner, workspace, TURN_ON).status_code == 400
    assert not ApprovalRequest.objects.exists()


def test_an_empty_allowlist_allows_no_entity_call(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    ha.states["scene.film_gecesi"] = salon_tv_state()  # any state object
    ha.states["scene.film_gecesi"]["entity_id"] = "scene.film_gecesi"
    entities = {
        "light": "light.salon",
        "media_player": TV,
        "climate": "climate.salon",
        "scene": "scene.film_gecesi",
    }
    refused = 0
    for domain, entity_id in entities.items():
        for service in bridge.SERVICES[domain]:
            arguments = {"domain": domain, "service": service, "entity_id": entity_id}
            response = _request(web_client, owner, workspace, arguments)
            assert response.status_code == 400, (domain, service)
            refused += 1
    assert refused > 10
    assert not ApprovalRequest.objects.exists() and ha.posts == []


def test_wake_on_lan_still_works_with_an_empty_allowlist_and_needs_the_mac(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
    ha_settings,
):
    assert not HomeEntity.objects.exists()
    other_mac = {**WOL, "data": {"mac": "11:22:33:44:55:66"}}
    assert _request(web_client, owner, workspace, other_mac).status_code == 400
    response = _request(web_client, owner, workspace, WOL)
    assert response.status_code == 201, response.content
    approve(response.json()["id"])
    assert ha.posts == [
        ("/api/services/wake_on_lan/send_magic_packet", {"mac": "AA:BB:CC:DD:EE:FF"})
    ]
    ha_settings.HA_WOL_ALLOWED_MACS = []
    assert _request(web_client, owner, workspace, WOL).status_code == 400


# --- pending approval, then the allowlist narrows ---


@pytest.mark.parametrize("change", ["delete", "disable", "untick"])
def test_narrowing_while_pending_sends_nothing(
    change,
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    entity = home_entities.create_entity(
        actor=owner,
        workspace=workspace,
        channel=CHANNEL_WEB,
        entity_id=TV,
        label="TV",
        services=["turn_on", "turn_off"],
    )
    response = _request(web_client, owner, workspace, TURN_ON)
    assert response.status_code == 201, response.content
    if change == "delete":
        home_entities.delete_entity(actor=owner, entity=entity, channel=CHANNEL_WEB)
    elif change == "disable":
        home_entities.update_entity(actor=owner, entity=entity, channel=CHANNEL_WEB, enabled=False)
    else:
        home_entities.update_entity(
            actor=owner, entity=entity, channel=CHANNEL_WEB, services=["turn_off"]
        )
    assert approve(response.json()["id"]).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "connector_changed")
    assert ha.posts == []


def test_the_precondition_checks_the_bindings_own_workspace(bound, ha, workspace):  # noqa: F811
    allow(workspace, TV, ["turn_on"])
    payload = {"arguments": dict(TURN_ON)}
    assert bridge.target_present(MANIFEST, bound, payload) is None
    other = register_user(email="other@test.dev", password="Str0ngDevPass!42")
    foreign = SimpleNamespace(workspace=Workspace.objects.get(owner=other))
    assert bridge.target_present(MANIFEST, foreign, payload) == "connector_changed"
    assert ha.posts == []


# --- pins (manifest v2) ---


def test_manifest_v2_pins_match_the_recomputed_contract():
    assert MANIFEST.version == 2
    hashes = bridge.tool_hashes()
    assert ha_manifest.STATES_PIN == hashes["get_states"]
    assert ha_manifest.CALL_SERVICE_PIN == hashes["call_service"]
    assert {p.name: p.schema_sha256 for p in MANIFEST.tools} == {
        "ha_states": hashes["get_states"],
        "ha_call_service": hashes["call_service"],
    }
    assert MANIFEST.get_tool(GATE).scope_check is bridge.check_allowlist
    assert MANIFEST.get_tool(GATE).precondition is bridge.target_present
    assert MANIFEST.get_tool("ha_states").annotate is bridge.annotate_states


class _WiderSource(bridge.SelectSourceData):
    source = bridge.serializers.CharField(max_length=200)


@pytest.mark.parametrize(
    "widen",
    [
        lambda mp: mp.setitem(bridge.SERVICES["media_player"], "media_stop", bridge.NoData),
        lambda mp: mp.setitem(bridge.SERVICES["media_player"], "select_source", _WiderSource),
        lambda mp: mp.setattr(bridge, "SOURCE_LIST_MAX_ITEMS", 500),
        lambda mp: mp.setattr(
            bridge, "ATTRIBUTE_WHITELIST", bridge.ATTRIBUTE_WHITELIST | {"media_title"}
        ),
    ],
)
def test_changing_a_serializer_or_bound_without_the_pin_fails(widen, monkeypatch):
    widen(monkeypatch)
    hashes = bridge.tool_hashes()
    assert (
        hashes["call_service"] != ha_manifest.CALL_SERVICE_PIN
        or hashes["get_states"] != ha_manifest.STATES_PIN
    )


def test_annotate_is_for_sync_native_reads_only():
    from connectors.manifest import validate_manifest

    write = MANIFEST.get_tool(GATE)
    bad = dataclasses.replace(write, annotate=bridge.annotate_states)
    with pytest.raises(ValueError, match="annotate"):
        validate_manifest(dataclasses.replace(MANIFEST, name="ha_bad", tools=(bad,)))


# --- the registry walk: nothing but the owner-web service writes HomeEntity ---

_WRITE_METHODS = frozenset(
    {
        "create",
        "bulk_create",
        "bulk_update",
        "get_or_create",
        "update_or_create",
        "update",
        "delete",
        "save",
        "raw",
    }
)
# Modules allowed to mention the model at all, and the one allowed to write.
_READERS = {
    "connectors/models.py",
    "connectors/bridges/home_assistant.py",
    "connectors/manifests/home_assistant.py",
    "connectors/views.py",
    "connectors/serializers.py",
    "connectors/home_entities.py",
}
_WRITER = "connectors/home_entities.py"


def _production_sources():
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        if "/tests/" in relative or "/migrations/" in relative or relative.startswith(".venv"):
            continue
        if relative.startswith("tests/") or "/reference/" in relative:
            continue
        yield relative, path


def test_only_the_owner_web_service_writes_home_entities():
    mentions = {}
    for relative, path in _production_sources():
        text = path.read_text(encoding="utf-8")
        if "HomeEntity" in text or "home_entities" in text:
            mentions[relative] = text
    assert set(mentions) - {"connectors/urls.py"} <= _READERS, sorted(mentions)
    for relative, text in mentions.items():
        if relative == _WRITER:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _WRITE_METHODS:
                continue
            chain = ast.unparse(node.func)
            assert "HomeEntity" not in chain and "home_entit" not in chain, (relative, chain)


def _callables(spec):
    for field in dataclasses.fields(spec):
        value = getattr(spec, field.name)
        while isinstance(value, functools.partial):
            value = value.func
        if callable(value) and not isinstance(value, type):
            yield field.name, value


def test_no_tool_route_or_connector_reaches_the_allowlist_writer():
    """Every registered gated tool and read tool (connector tools, node
    tools, wake `tool.*` routes are built from these), and every manifest
    tool hook: none is defined in the owner-web service module."""
    specs = [get_tool(name) for name in list_tool_names()]
    specs += [get_read_tool(name) for name in list_read_tool_names()]
    for manifest in list_manifests():
        specs += list(manifest.tools)
    assert specs
    for spec in specs:
        for field_name, value in _callables(spec):
            module = getattr(value, "__module__", "") or ""
            assert module != home_entities.__name__, (spec, field_name)
            assert not getattr(value, "__qualname__", "").startswith(
                ("create_entity", "update_entity", "delete_entity")
            ), (spec, field_name)
    # And the allowlist is never itself a tool.
    names = set(list_tool_names()) | set(list_read_tool_names())
    assert not {n for n in names if "entity" in n or "allowlist" in n}
