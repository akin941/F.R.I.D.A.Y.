"""Home Assistant native bridge (brief D8, branch 7): code-bounded allowlist,
request-time validation, exact dialog == exact send, fail closed on a
missing entity, env-only token -- against `httpx.MockTransport` only."""

import dataclasses
import json
import logging

import httpx
import pytest

from approvals.models import ApprovalRequest
from audit.models import AuditLog
from connectors import bridges
from connectors.bridges import home_assistant as bridge
from connectors.manifest import get_manifest, validate_manifest
from connectors.manifests import home_assistant as ha_manifest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import ConnectorInvalidTransition, set_binding_enabled
from connectors.tasks import run_connector_write
from connectors.tests.fake_home_assistant import TOKEN_CANARY, ha  # noqa: F401
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from memory.models import Memory
from tools.registry import ReadToolError, get_read_tool, get_tool, list_tool_names

pytestmark = pytest.mark.django_db

URL = "/api/v1/approvals/requests"
GATE = "ha_call_service"
MANIFEST = get_manifest("home_assistant")


@pytest.fixture(autouse=True)
def ha_settings(settings):
    settings.HA_ALLOWED_DOMAINS = []
    settings.HA_WOL_ALLOWED_MACS = ["aa-bb-cc-dd-ee-ff"]
    return settings


@pytest.fixture
def bound(ha, enable, workspace):  # noqa: F811
    return enable(MANIFEST, workspace)


def _request(web_client, owner, workspace, arguments):
    return web_client(owner).post(
        URL,
        {"workspace": str(workspace.id), "tool_name": GATE, "payload": {"arguments": arguments}},
        format="json",
    )


@pytest.fixture
def approve(web_client, owner, committed):
    def _approve(request_id):
        with committed():
            return web_client(owner).post(f"{URL}/{request_id}/approve")

    return _approve


SALON_40 = {
    "domain": "light",
    "service": "turn_on",
    "entity_id": "light.salon",
    "data": {"brightness_pct": 40},
}


# --- the code maximum ---


def test_the_code_maximum_never_contains_a_forbidden_domain():
    assert not bridge.NEVER_DOMAINS & set(bridge.SERVICES)
    assert not bridge.NEVER_DOMAINS & bridge.read_domains()
    for services in bridge.SERVICES.values():
        assert "toggle" not in services


def test_allowed_domains_can_only_narrow(ha_settings):
    maximum = bridge.service_domains()
    assert maximum == frozenset(bridge.SERVICES)
    ha_settings.HA_ALLOWED_DOMAINS = ["light", "lock", "script", "vacuum", "sensor"]
    assert bridge.service_domains() == {"light"}
    assert bridge.read_domains() == {"light", "sensor"}
    ha_settings.HA_ALLOWED_DOMAINS = ["nothing"]
    assert bridge.service_domains() == frozenset()


def test_manifest_shape_and_pins():
    validate_manifest(MANIFEST)
    assert MANIFEST.kind == "native"
    assert MANIFEST.data_class == "personal"
    assert MANIFEST.workspace_types == frozenset({"personal"})
    assert MANIFEST.read_channels == frozenset({CHANNEL_WEB})
    assert {p.name: p.schema_sha256 for p in MANIFEST.tools} == {
        "ha_states": bridge.tool_hashes()["get_states"],
        "ha_call_service": bridge.tool_hashes()["call_service"],
    }
    spec = get_tool(GATE)
    assert spec.gated is True and spec.user_invocable is True
    assert spec.records_work_log is False
    assert GATE in list_tool_names()
    assert get_read_tool("ha_states").data_class == "personal"


def test_widening_the_allowlist_changes_the_pin(monkeypatch):
    before = bridge.tool_hashes()["call_service"]
    monkeypatch.setitem(bridge.SERVICES, "vacuum", {"start": bridge.NoData})
    assert bridge.tool_hashes()["call_service"] != before
    assert bridge.tool_hashes()["call_service"] != ha_manifest.CALL_SERVICE_PIN


def test_a_widened_contract_disables_the_tool_until_the_pin_is_updated(
    bound,
    monkeypatch,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    from connectors.services import check_binding_pins

    monkeypatch.setitem(bridge.SERVICES, "vacuum", {"start": bridge.NoData})
    check_binding_pins(bound.id)
    bound.refresh_from_db()
    assert bound.tool_status("ha_call_service") == "mismatch"
    assert _request(web_client, owner, workspace, SALON_40).status_code == 400
    assert AuditLog.objects.filter(action="connector.pin_mismatch").exists()


# --- binding ---


def test_a_firm_workspace_cannot_bind(owner, firm, ha, web_client):  # noqa: F811
    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=firm,
            actor=owner,
            connector="home_assistant",
            enabled=True,
            channel=CHANNEL_WEB,
        )
    response = web_client(owner).put(
        "/api/v1/connectors/bindings/home_assistant",
        {"workspace": str(firm.id), "enabled": True},
        format="json",
    )
    assert response.status_code == 400
    assert not ConnectorBinding.objects.filter(workspace=firm).exists()
    assert ha.requests == []


def test_enable_runs_the_health_and_pin_check(bound, ha):  # noqa: F811
    assert bound.tool_status("ha_states") == "ok"
    assert bound.tool_status("ha_call_service") == "ok"
    assert bound.last_health["server_version"] == "2026.9.1"
    assert bound.last_health["details"]["api"] == "rest"
    assert bound.last_health["details"]["tool_hashes"] == bridge.tool_hashes()
    method, path, _, headers = ha.requests[0]
    assert (method, path) == ("GET", "/api/config")
    assert headers["authorization"] == f"Bearer {TOKEN_CANARY}"


def test_unconfigured_env_leaves_nothing_callable(
    ha,  # noqa: F811
    enable,
    workspace,
    owner,
    web_client,
    monkeypatch,
):
    monkeypatch.delenv(bridge.TOKEN_ENV)
    binding = enable(MANIFEST, workspace)
    assert binding.tool_status("ha_call_service") == "not_configured"
    assert binding.tool_status("ha_states") == "not_configured"
    assert binding.last_health["error"] == "not_configured"
    assert AuditLog.objects.filter(action="connector.reconnect_required").exists()
    assert _request(web_client, owner, workspace, SALON_40).status_code == 400
    assert ha.requests == []


def test_a_refused_token_disables_the_tools_until_the_next_good_check(
    bound,
    ha,  # noqa: F811
    monkeypatch,
):
    from connectors.services import check_binding_pins

    def refuse(request):
        return httpx.Response(401, json={"message": "Unauthorized"})

    monkeypatch.setattr(bridge, "_http_transport", lambda: httpx.MockTransport(refuse))
    check_binding_pins(bound.id)
    bound.refresh_from_db()
    assert bound.tool_status("ha_call_service") == "not_configured"
    transport = httpx.MockTransport(ha.handler)
    monkeypatch.setattr(bridge, "_http_transport", lambda: transport)
    check_binding_pins(bound.id)
    bound.refresh_from_db()
    assert bound.tool_status("ha_call_service") == "ok"


# --- the done-when: "salonun ışığını %40 yap" ---


def test_salon_light_40_percent_is_exact_and_decidable(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    response = _request(web_client, owner, workspace, SALON_40)
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    assert ha.posts == []  # nothing before the decision

    summary = web_client(owner).get(f"{URL}/{request_id}").json()["display_summary"]
    rows = {row["label"]: row["value"] for row in summary["rows"]}
    assert rows["Hizmet"] == "light.turn_on"
    assert rows["Varlık (entity_id)"] == "light.salon"
    assert rows["data.brightness_pct"] == "40"
    # HA-supplied text, labelled as such, and the state read at request time.
    assert rows["Ad (Home Assistant'tan gelen metin)"] == "Salon"
    assert rows["Şu anki durum (istek anında okundu)"] == "off"
    assert "PICTURE-TOKEN" not in json.dumps(summary)
    assert summary["data_class"] == "personal"

    assert approve(request_id).status_code == 200
    # Exactly one send, exactly the dialog's arguments.
    assert ha.posts == [
        ("/api/services/light/turn_on", {"entity_id": "light.salon", "brightness_pct": 40})
    ]
    assert json.loads(summary["arguments_json"]) == SALON_40
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded"
    # State read back after execution.
    assert '"state_after"' in execution.result["text"]
    assert '"state": "on"' in execution.result["text"]
    assert AuditLog.objects.filter(action="connector.write.succeeded").count() == 1
    # records_work_log=False: audit-worthy, not memory-worthy.
    assert not Memory.objects.filter(memory_type="work_log").exists()

    run_connector_write(str(execution.id))  # a redelivery never re-sends
    assert len(ha.posts) == 1


# --- request-time refusals (400, no row, no send) ---


@pytest.mark.parametrize(
    "arguments",
    [
        {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"},
        {"domain": "script", "service": "turn_on", "entity_id": "script.everything"},
        {"domain": "automation", "service": "trigger", "entity_id": "automation.x"},
        {"domain": "homeassistant", "service": "restart"},
        {"domain": "light", "service": "toggle", "entity_id": "light.salon"},
        {"domain": "light", "service": "turn_on", "entity_id": "lock.front_door"},
        {"domain": "light", "service": "turn_on"},
        {"domain": "light", "service": "turn_on", "entity_id": "light.salon", "extra": 1},
        {**SALON_40, "data": {"flash": "long"}},
        {**SALON_40, "data": {"brightness_pct": 150}},
        {**SALON_40, "data": {"brightness_pct": 0}},
        {**SALON_40, "data": {"brightness_pct": True}},
        {**SALON_40, "data": {"brightness_pct": 40.5}},
        {**SALON_40, "data": {"color_temp_kelvin": 9000}},
        {**SALON_40, "data": "40"},
        {
            "domain": "climate",
            "service": "set_temperature",
            "entity_id": "climate.salon",
            "data": {"temperature": 35},
        },
        {
            "domain": "climate",
            "service": "set_hvac_mode",
            "entity_id": "climate.salon",
            "data": {"hvac_mode": "emergency"},
        },
        {
            "domain": "media_player",
            "service": "volume_set",
            "entity_id": "media_player.tv",
            "data": {"volume_level": 1.5},
        },
        {**SALON_40, "entity_id": "light.kitchen"},  # HA has no such entity
        {
            "domain": "wake_on_lan",
            "service": "send_magic_packet",
            "data": {"mac": "11:22:33:44:55:66"},
        },
        {
            "domain": "wake_on_lan",
            "service": "send_magic_packet",
            "entity_id": "light.salon",
            "data": {"mac": "AA:BB:CC:DD:EE:FF"},
        },
    ],
)
def test_disallowed_requests_are_refused_at_request_time(
    arguments,
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
):
    response = _request(web_client, owner, workspace, arguments)
    assert response.status_code == 400, arguments
    assert not ApprovalRequest.objects.exists()
    assert ha.posts == []


def test_allowed_domains_narrowing_refuses_at_request_time(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    ha_settings,
):
    ha_settings.HA_ALLOWED_DOMAINS = ["climate"]
    assert _request(web_client, owner, workspace, SALON_40).status_code == 400
    ha_settings.HA_ALLOWED_DOMAINS = ["climate", "light"]
    assert _request(web_client, owner, workspace, SALON_40).status_code == 201


def test_unreachable_home_assistant_refuses_the_request(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    monkeypatch,
):
    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(bridge, "_http_transport", lambda: httpx.MockTransport(down))
    assert _request(web_client, owner, workspace, SALON_40).status_code == 400
    assert not ApprovalRequest.objects.exists()


# --- execution: fail closed, at most once ---


def test_entity_gone_at_execution_fails_closed(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    del ha.states["light.salon"]
    assert approve(request_id).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "target_missing")
    assert ha.posts == []
    assert AuditLog.objects.get(action="connector.write.failed").metadata["error_type"] == (
        "target_missing"
    )


def test_allowlist_narrowed_after_the_request_is_connector_changed(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
    ha_settings,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    ha_settings.HA_ALLOWED_DOMAINS = ["climate"]
    assert approve(request_id).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "connector_changed")
    assert ha.posts == []


def test_the_execution_precondition_is_the_policy_hook():
    """The entity/allowlist re-check runs as the framework's
    `ToolPolicy.precondition` (Phase 5 branch 5), after the claim and
    before the one send -- not as a bridge-private side path."""
    assert MANIFEST.get_tool("ha_call_service").precondition is bridge.target_present
    assert MANIFEST.get_tool("ha_states").precondition is None


def test_request_rows_are_write_only():
    read = MANIFEST.get_tool("ha_states")
    bad = dataclasses.replace(read, request_rows=bridge.request_rows)
    with pytest.raises(ValueError, match="writes only"):
        validate_manifest(dataclasses.replace(MANIFEST, name="ha_bad", tools=(bad,)))


def test_home_assistant_unreachable_at_execution_is_failed_and_nothing_sent(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
    monkeypatch,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]

    def down(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(bridge, "_http_transport", lambda: httpx.MockTransport(down))
    assert approve(request_id).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "transport_error")
    assert ha.posts == []


def test_precondition_refusals_map_to_closed_error_types(ha, ha_settings):  # noqa: F811
    payload = {"arguments": dict(SALON_40)}
    assert bridge.target_present(MANIFEST, None, payload) is None
    del ha.states["light.salon"]
    assert bridge.target_present(MANIFEST, None, payload) == "target_missing"
    ha_settings.HA_ALLOWED_DOMAINS = ["switch"]
    assert bridge.target_present(MANIFEST, None, payload) == "connector_changed"
    # A payload that is not exactly the normalized snapshot is refused too.
    tampered = {"arguments": {**SALON_40, "data": {"brightness_pct": 40, "x": 1}}}
    ha_settings.HA_ALLOWED_DOMAINS = []
    assert bridge.target_present(MANIFEST, None, tampered) == "connector_changed"
    assert ha.posts == []


def test_an_entity_gone_after_the_precondition_still_sends_nothing(ha):  # noqa: F811
    """Defence in depth: the bridge re-reads the entity right before the
    POST, so a race with the precondition fails closed, unsent."""
    del ha.states["light.salon"]
    with pytest.raises(Exception) as exc:
        bridges.call_tool(
            MANIFEST,
            server_tool=bridge.SERVER_TOOL_CALL,
            arguments=SALON_40,
            schema_sha256=ha_manifest.CALL_SERVICE_PIN,
            timeout=5,
        )
    assert (exc.value.code, exc.value.sent) == ("target_missing", False)
    assert ha.posts == []


def test_a_timeout_after_the_send_is_unknown_outcome_and_never_resent(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    ha.post_error = httpx.ReadTimeout("slow")
    assert approve(request_id).status_code == 200
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("unknown_outcome", "timeout")
    assert len(ha.posts) == 1
    run_connector_write(str(execution.id))
    assert len(ha.posts) == 1


def test_home_assistant_refusal_is_a_failed_tool_error(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    ha.service_status = 400
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "tool_error")
    assert len(ha.posts) == 1


def test_a_server_error_after_the_send_is_unknown_outcome(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    ha.service_status = 500
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "unknown_outcome"


def test_wake_on_lan_uses_the_mac_allowlist(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    arguments = {
        "domain": "wake_on_lan",
        "service": "send_magic_packet",
        "data": {"mac": "aa:bb:cc:dd:ee:ff"},
    }
    response = _request(web_client, owner, workspace, arguments)
    assert response.status_code == 201, response.content
    approval = ApprovalRequest.objects.get(id=response.json()["id"])
    assert approval.payload["arguments"]["data"] == {"mac": "AA:BB:CC:DD:EE:FF"}
    approve(approval.id)
    assert ha.posts == [
        ("/api/services/wake_on_lan/send_magic_packet", {"mac": "AA:BB:CC:DD:EE:FF"})
    ]
    assert ConnectorWriteExecution.objects.get().status == "succeeded"


def test_climate_temperature_is_sent_as_a_number(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    arguments = {
        "domain": "climate",
        "service": "set_temperature",
        "entity_id": "climate.salon",
        "data": {"temperature": 21.5},
    }
    request_id = _request(web_client, owner, workspace, arguments).json()["id"]
    approve(request_id)
    assert ha.posts == [
        (
            "/api/services/climate/set_temperature",
            {"entity_id": "climate.salon", "temperature": 21.5},
        )
    ]


# --- reads ---


def _read(workspace, owner, args, channel=CHANNEL_WEB):
    return get_read_tool("ha_states").execute(
        workspace=workspace, actor=owner, args=args, channel=channel
    )


def test_states_read_filters_domains_and_attributes(bound, ha, workspace, owner):  # noqa: F811
    result = _read(workspace, owner, {})
    data = json.loads(result.text)
    ids = [e["entity_id"] for e in data["entities"]]
    assert ids == ["climate.salon", "light.salon", "media_player.tv", "sensor.salon_temp"]
    salon = next(e for e in data["entities"] if e["entity_id"] == "light.salon")
    assert salon["attributes"] == {"friendly_name": "Salon"}
    assert "PICTURE-TOKEN" not in result.text
    assert "context" not in salon
    log = AuditLog.objects.get(action="connector.read")
    assert log.metadata["outcome"] == "ok"


def test_states_read_by_domain_and_entity(bound, ha, workspace, owner):  # noqa: F811
    data = json.loads(_read(workspace, owner, {"domain": "sensor"}).text)
    assert [e["entity_id"] for e in data["entities"]] == ["sensor.salon_temp"]
    data = json.loads(_read(workspace, owner, {"entity_id": "climate.salon"}).text)
    assert data["entities"][0]["attributes"]["temperature"] == 21.0


@pytest.mark.parametrize(
    "args, code",
    [
        ({"entity_id": "lock.front_door"}, "invalid_arguments"),
        ({"domain": "script"}, "invalid_arguments"),
        ({"entity_id": "light.salon", "extra": 1}, "invalid_arguments"),
        ({"entity_id": "light.kitchen"}, "target_missing"),
    ],
)
def test_states_read_refusals(bound, ha, workspace, owner, args, code):  # noqa: F811
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, args)
    assert exc.value.code == code


def test_states_read_is_web_only(bound, ha, workspace, owner):  # noqa: F811
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, {}, channel=CHANNEL_CLI)
    assert exc.value.code == "channel_forbidden"


def test_the_native_dispatch_routes_to_the_bridge(ha):  # noqa: F811
    assert bridges.get_bridge(MANIFEST) is bridge.BRIDGE
    result = bridges.call_tool(
        MANIFEST,
        server_tool=bridge.SERVER_TOOL_STATES,
        arguments={"entity_id": "light.salon"},
        schema_sha256=ha_manifest.STATES_PIN,
        timeout=5,
    )
    assert result.structured["entities"][0]["entity_id"] == "light.salon"
    health = bridge.BRIDGE.check_health(MANIFEST, timeout=5)
    assert health.tools == {"ha_states": "ok", "ha_call_service": "ok"}
    orphan = dataclasses.replace(MANIFEST, name="orphan_native")
    with pytest.raises(Exception) as exc:
        bridges.call_tool(
            orphan,
            server_tool=bridge.SERVER_TOOL_STATES,
            arguments={},
            schema_sha256=ha_manifest.STATES_PIN,
            timeout=5,
        )
    assert getattr(exc.value, "code", None) == "not_configured"


def test_a_call_with_a_stale_pin_is_refused_before_any_request(ha):  # noqa: F811
    with pytest.raises(Exception) as exc:
        bridges.call_tool(
            MANIFEST,
            server_tool=bridge.SERVER_TOOL_CALL,
            arguments=SALON_40,
            schema_sha256="0" * 64,
            timeout=5,
        )
    assert exc.value.code == "pin_mismatch"
    assert exc.value.listing.tool_hashes == bridge.tool_hashes()
    assert ha.requests == []


def test_redirects_are_never_followed(bound, ha, workspace, owner, monkeypatch):  # noqa: F811
    def redirect(request):
        return httpx.Response(302, headers={"location": "http://evil.test/api/states"})

    monkeypatch.setattr(bridge, "_http_transport", lambda: httpx.MockTransport(redirect))
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, {})
    assert exc.value.code == "protocol_error"


def test_the_token_is_nowhere_but_the_authorization_header(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    _read(workspace, owner, {})
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    approve(request_id)
    ha.post_error = httpx.ConnectError(f"boom {TOKEN_CANARY}")
    second = _request(web_client, owner, workspace, {**SALON_40, "data": {}}).json()["id"]
    approve(second)
    dumped = json.dumps(
        [
            list(AuditLog.objects.values()),
            list(ConnectorBinding.objects.values()),
            list(ConnectorWriteExecution.objects.values()),
            list(ApprovalRequest.objects.values()),
            list(Memory.objects.values()),
        ],
        default=str,
    )
    assert TOKEN_CANARY not in dumped
    assert TOKEN_CANARY not in caplog.text
    for _method, _path, body, headers in ha.requests:
        assert TOKEN_CANARY not in json.dumps(body)
        assert headers["authorization"] == f"Bearer {TOKEN_CANARY}"


# --- role x endpoint: requesting ha_call_service ---


def _pat_client(owner, workspace, kind):
    import secrets

    from rest_framework.test import APIClient

    from accounts.authentication import TOKEN_PREFIX, hash_token
    from accounts.models import PersonalAccessToken

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _principal_client(principal, owner, workspace, web_client):
    from rest_framework.test import APIClient

    from accounts.models import User
    from accounts.services import register_user

    if principal == "owner-web":
        return web_client(owner)
    if principal in ("owner-pat-cli", "owner-pat-openai"):
        return _pat_client(owner, workspace, principal.removeprefix("owner-pat-"))
    if principal == "non-owner":
        return web_client(register_user(email="member@test.dev", password="Str0ngDevPass!42"))
    if principal == "agent":
        return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
    return APIClient()


# Requesting is the approvals app's usual surface: the owner over the web or
# the CLI PAT may ask (deciding stays web-only, below); the facade token is
# the wrong kind; another user or an agent does not even see the personal
# workspace (404, no existence leak).
REQUEST_MATRIX = [
    ("owner-web", 201),
    ("owner-pat-cli", 201),
    ("owner-pat-openai", 403),
    ("non-owner", 404),
    ("agent", 404),
    ("anonymous", 401),
]


@pytest.mark.parametrize("principal, expected", REQUEST_MATRIX)
def test_request_role_matrix(
    principal,
    expected,
    bound,
    ha,  # noqa: F811
    owner,
    workspace,
    web_client,
):
    client = _principal_client(principal, owner, workspace, web_client)
    response = client.post(
        URL,
        {"workspace": str(workspace.id), "tool_name": GATE, "payload": {"arguments": SALON_40}},
        format="json",
    )
    assert response.status_code == expected, response.content
    assert ha.posts == []


@pytest.mark.parametrize("principal, expected", REQUEST_MATRIX)
def test_approve_role_matrix(
    principal,
    expected,
    bound,
    ha,  # noqa: F811
    owner,
    workspace,
    web_client,
    committed,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    client = _principal_client(principal, owner, workspace, web_client)
    with committed():
        response = client.post(f"{URL}/{request_id}/approve")
    approved = response.status_code == 200
    assert approved == (principal == "owner-web"), (principal, response.status_code)
    assert len(ha.posts) == (1 if approved else 0)


def test_the_governance_twin_records_the_current_pins():
    from pathlib import Path

    twin = Path(__file__).resolve().parents[3] / "docs" / "mcp" / "home-assistant.md"
    text = twin.read_text()
    for policy in MANIFEST.tools:
        assert policy.schema_sha256 in text, policy.name


def test_a_connect_failure_on_the_send_is_failed_not_unknown(
    bound,
    ha,  # noqa: F811
    web_client,
    owner,
    workspace,
    approve,
):
    request_id = _request(web_client, owner, workspace, SALON_40).json()["id"]
    ha.post_error = httpx.ConnectError("refused")
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "transport_error")


@pytest.mark.parametrize(
    "base_url",
    ["http://user:pw@ha.test:8123", "ftp://ha.test", "http://ha.test:8123/?x=1", ""],
)
def test_a_malformed_base_url_is_not_configured(base_url, ha, monkeypatch):  # noqa: F811
    monkeypatch.setenv(bridge.BASE_URL_ENV, base_url)
    health = bridge.check_health(MANIFEST, timeout=5)
    assert (health.error, health.tools) == ("not_configured", {})
    assert ha.requests == []
