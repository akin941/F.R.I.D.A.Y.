"""Home Assistant over its native REST API (ADR-010, brief D8, R5, Q7).

Not HA's MCP server: its intent tools (`HassTurnOn` by name/area) are
resolved inside HA after the decision, so the dialog could not say which
entity changes. The REST API is exact -- `GET /api/states[/<entity_id>]`
and `POST /api/services/<domain>/<service>` with an explicit `entity_id` --
so what the owner approves is byte-for-byte what is sent.

**The code maximum.** `SERVICES` is the only set of domain/service pairs
Friday can ever call, each with a strict per-service DRF serializer for its
`data` (unknown keys refused, values bounded). `HA_ALLOWED_DOMAINS` can only
narrow it (intersection; names outside the maximum are ignored);
`NEVER_DOMAINS` (scripts, automations, shell/rest commands, `homeassistant`,
`hassio`, locks, alarms) can never appear in it -- a test pins both. Wake-on-
LAN additionally needs the MAC in `HA_WOL_ALLOWED_MACS` (empty: refused).
No domain is confirm-class or ungated (Q7): every call is the gated
`ha_call_service` tool.

**The pin.** There is no remote `inputSchema` to pin: the "schema" of each
tool is this module's own request contract (`tool_schemas()`, derived from
the serializers), and the manifest pins its sha256. Widening the allowlist
without updating the manifest pin (and version) in the same reviewed commit
disables the tool on the next pin check -- fail closed.

**Credentials.** `HA_TOKEN` and `HA_BASE_URL` are read from the process
environment at call time, never from settings, the database, a payload, an
audit row or a log line (R5: the token is admin-equivalent; use a dedicated
non-admin HA user, HA reachable on the LAN/Tailscale only).

**Calls.** A sync `httpx` client per operation: no redirects, no proxies
from the environment, response bodies capped at
`CONNECTOR_RESPONSE_MAX_BYTES` while streaming. Errors are the framework's
closed vocabulary (`ConnectorError`); logs carry the operation and the
exception class, never text, URLs, arguments or results. Everything HA
returns is low-trust: only whitelisted attributes of allowed-domain entities
survive, strings are stripped of invisible formatting and bounded.

**A write** (`call_service`): re-validated against the allowlist as it is
*now* (`not_allowed` -> `connector_changed`), the entity read first (gone ->
`target_missing`, failed closed, nothing sent), one POST, then the state is
read back into the execution result. A timeout or 5xx after the POST was
handed to the transport is `sent`: the execution ends `unknown_outcome` and
is never re-sent.
"""

import json
import logging
import math
import os
import re
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from rest_framework import serializers

from connectors.bridges import NativeHealth
from connectors.manifest import schema_hash
from connectors.mcp_client import CallResult, ConnectorError, Listing
from memory.context import strip_invisible_formatting
from tools.registry import ToolValidationError

logger = logging.getLogger(__name__)

BASE_URL_ENV = "HA_BASE_URL"
TOKEN_ENV = "HA_TOKEN"

SERVER_NAME = "Home Assistant"
SERVER_TOOL_STATES = "get_states"
SERVER_TOOL_CALL = "call_service"

WAKE_ON_LAN = "wake_on_lan"
# Domains no Friday code path may ever call, whatever the allowlist says.
NEVER_DOMAINS = frozenset(
    {
        "script",
        "automation",
        "shell_command",
        "rest_command",
        "homeassistant",
        "hassio",
        "lock",
        "alarm_control_panel",
    }
)
# Domains that are read (`ha_states`) but never called.
READ_ONLY_DOMAINS = frozenset({"sensor", "binary_sensor"})
# Attributes that survive a state read; everything else (notably
# `entity_picture`, which can carry an access token) is dropped.
ATTRIBUTE_WHITELIST = frozenset(
    {
        "friendly_name",
        "unit_of_measurement",
        "device_class",
        "brightness",
        "color_temp_kelvin",
        "current_temperature",
        "temperature",
        "hvac_action",
        "hvac_mode",
        "percentage",
        "current_position",
        "volume_level",
        "is_volume_muted",
    }
)
MAX_STATES = 200
TEXT_MAX_CHARS = 200
HVAC_MODES = ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")

ENTITY_RE = re.compile(r"^([a-z][a-z0-9_]{0,39})\.([a-z0-9_]{1,100})$")
MAC_RE = re.compile(r"^[0-9A-F]{2}([:-]?)(?:[0-9A-F]{2}\1){4}[0-9A-F]{2}$")


# --- strict serializers ---


class StrictSerializer(serializers.Serializer):
    """Refuses keys it does not declare (DRF silently drops them)."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({key: ["Unknown field."] for key in unknown})
        return super().to_internal_value(data)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class FiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            self.fail("invalid")
        return value


class NoData(StrictSerializer):
    pass


class LightTurnOnData(StrictSerializer):
    brightness_pct = StrictIntegerField(required=False, min_value=1, max_value=100)
    color_temp_kelvin = StrictIntegerField(required=False, min_value=2000, max_value=6500)


class FanPercentageData(StrictSerializer):
    percentage = StrictIntegerField(min_value=0, max_value=100)


class CoverPositionData(StrictSerializer):
    position = StrictIntegerField(min_value=0, max_value=100)


class ClimateTemperatureData(StrictSerializer):
    temperature = FiniteFloatField(min_value=16, max_value=30)


class ClimateHvacModeData(StrictSerializer):
    hvac_mode = serializers.ChoiceField(choices=HVAC_MODES)


class VolumeSetData(StrictSerializer):
    volume_level = FiniteFloatField(min_value=0, max_value=1)


class WakeOnLanData(StrictSerializer):
    mac = serializers.CharField(max_length=17)

    def validate_mac(self, value):
        mac = normalize_mac(value)
        if mac is None:
            raise serializers.ValidationError("Not a MAC address.")
        if mac not in allowed_macs():
            raise serializers.ValidationError("MAC is not in HA_WOL_ALLOWED_MACS.")
        return mac


# The code maximum (D8). A toggle is deliberately absent: its outcome depends
# on the state at execution time, so the dialog could not say what happens.
SERVICES = {
    "light": {"turn_on": LightTurnOnData, "turn_off": NoData},
    "switch": {"turn_on": NoData, "turn_off": NoData},
    "fan": {"turn_on": NoData, "turn_off": NoData, "set_percentage": FanPercentageData},
    "cover": {
        "open_cover": NoData,
        "close_cover": NoData,
        "stop_cover": NoData,
        "set_cover_position": CoverPositionData,
    },
    "climate": {"set_temperature": ClimateTemperatureData, "set_hvac_mode": ClimateHvacModeData},
    "media_player": {"media_play": NoData, "media_pause": NoData, "volume_set": VolumeSetData},
    # A scene is an owner-configured intent HA resolves at activation time;
    # its effects cannot be previewed and may touch NEVER_DOMAINS entities
    # (residual risk, docs/mcp/home-assistant.md). Narrow it away with
    # HA_ALLOWED_DOMAINS if scenes include security devices.
    "scene": {"turn_on": NoData},
    WAKE_ON_LAN: {"send_magic_packet": WakeOnLanData},
}


def normalize_mac(value):
    candidate = str(value or "").strip().upper()
    if not MAC_RE.match(candidate):
        return None
    digits = re.sub(r"[^0-9A-F]", "", candidate)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def allowed_macs():
    return {mac for mac in map(normalize_mac, settings.HA_WOL_ALLOWED_MACS) if mac}


def service_domains():
    """The callable domains now: the code maximum, narrowed (never widened)
    by `HA_ALLOWED_DOMAINS` when it is set."""
    maximum = frozenset(SERVICES) - NEVER_DOMAINS
    configured = settings.HA_ALLOWED_DOMAINS
    if not configured:
        return maximum
    return maximum & {str(name).strip() for name in configured}


def read_domains():
    """The domains `ha_states` may show: the callable ones (minus WoL, which
    has no entities) plus the read-only sensors -- narrowed the same way."""
    maximum = (frozenset(SERVICES) - {WAKE_ON_LAN} | READ_ONLY_DOMAINS) - NEVER_DOMAINS
    configured = settings.HA_ALLOWED_DOMAINS
    if not configured:
        return maximum
    return maximum & {str(name).strip() for name in configured}


def entity_domain(entity_id):
    match = ENTITY_RE.match(entity_id or "")
    return match.group(1) if match else None


class StatesArguments(StrictSerializer):
    """`ha_states`: one entity, one domain, or every allowed entity."""

    entity_id = serializers.CharField(required=False, max_length=141)
    domain = serializers.CharField(required=False, max_length=40)

    def validate_entity_id(self, value):
        if entity_domain(value) not in read_domains():
            raise serializers.ValidationError("Not an entity of an allowed domain.")
        return value

    def validate_domain(self, value):
        if value not in read_domains():
            raise serializers.ValidationError("Not an allowed domain.")
        return value


class CallServiceArguments(StrictSerializer):
    """`ha_call_service`: one allowlisted service on one exact entity (WoL:
    no entity, an allowlisted MAC). The per-service serializer validates
    `data`; the normalized result is the snapshot the owner decides on."""

    domain = serializers.CharField(max_length=40)
    service = serializers.CharField(max_length=40)
    entity_id = serializers.CharField(required=False, max_length=141)
    data = serializers.DictField(default=dict)

    def validate(self, attrs):
        domain, service = attrs["domain"], attrs["service"]
        if domain in NEVER_DOMAINS or domain not in service_domains():
            raise serializers.ValidationError({"domain": ["Domain is not allowed."]})
        data_serializer = SERVICES[domain].get(service)
        if data_serializer is None:
            raise serializers.ValidationError({"service": ["Service is not allowed."]})
        entity_id = attrs.get("entity_id")
        if domain == WAKE_ON_LAN:
            if entity_id is not None:
                raise serializers.ValidationError({"entity_id": ["Wake-on-LAN takes no entity."]})
        elif entity_id is None or entity_domain(entity_id) != domain:
            raise serializers.ValidationError(
                {"entity_id": ["An exact entity of the service's domain is required."]}
            )
        data = data_serializer(data=attrs.get("data") or {})
        if not data.is_valid():
            raise serializers.ValidationError({"data": data.errors})
        clean = {"domain": domain, "service": service, "data": dict(data.validated_data)}
        if entity_id is not None:
            clean["entity_id"] = entity_id
        return clean


# --- the pin: this module's own request contract ---


def _describe(field):
    spec = {"type": type(field).__name__, "required": bool(field.required)}
    for attr in ("min_value", "max_value", "max_length"):
        value = getattr(field, attr, None)
        if value is not None:
            spec[attr] = value
    if isinstance(field, serializers.ChoiceField):
        spec["choices"] = sorted(field.choices)
    return spec


def tool_schemas():
    """The request contract each server tool accepts, as JSON -- what the
    manifest pins (`schema_hash`). Derived from the serializers themselves,
    so any widening changes the hash."""
    return {
        SERVER_TOOL_STATES: {
            "arguments": {name: _describe(f) for name, f in StatesArguments().fields.items()},
            "read_only_domains": sorted(READ_ONLY_DOMAINS),
            "attributes": sorted(ATTRIBUTE_WHITELIST),
        },
        SERVER_TOOL_CALL: {
            "never": sorted(NEVER_DOMAINS),
            "services": {
                domain: {
                    service: {name: _describe(f) for name, f in data().fields.items()}
                    for service, data in sorted(services.items())
                }
                for domain, services in sorted(SERVICES.items())
            },
        },
    }


def tool_hashes():
    return {name: schema_hash(schema) for name, schema in tool_schemas().items()}


def _static_listing():
    """A pin report for `record_listing` when a call itself finds the pin
    changed (the framework then disables just that tool, audited)."""
    return Listing(
        server_name=SERVER_NAME,
        server_version="",
        protocol_version="rest",
        tool_hashes=tool_hashes(),
    )


# --- HTTP ---


def _http_transport():
    """Module-attribute seam: tests return an `httpx.MockTransport`. In
    production `None`, i.e. httpx's default transport."""
    return None


def _base_url():
    raw = os.environ.get(BASE_URL_ENV, "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return None
    return raw.rstrip("/")


def _client(timeout):
    base_url = _base_url()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not base_url or not token:
        raise ConnectorError("not_configured")
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=_http_transport(),
    )


def _request(client, method, path, *, body=None, operation):
    """`(status, parsed_json_or_None)`. Raises `ConnectorError`."""
    cap = settings.CONNECTOR_RESPONSE_MAX_BYTES
    try:
        with client.stream(method, path, json=body) as response:
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > cap:
                    raise ConnectorError("protocol_error")
                chunks.append(chunk)
            status = response.status_code
    except ConnectorError:
        logger.warning("home_assistant %s: response over the size cap", operation)
        raise
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # No connection was made: nothing reached Home Assistant.
        logger.warning("home_assistant %s failed: connect (%s)", operation, type(exc).__name__)
        error = ConnectorError(
            "timeout" if isinstance(exc, httpx.TimeoutException) else "transport_error"
        )
        error.unsent = True
        raise error from None
    except httpx.TimeoutException as exc:
        logger.warning("home_assistant %s failed: timeout (%s)", operation, type(exc).__name__)
        raise ConnectorError("timeout") from None
    except httpx.HTTPError as exc:
        logger.warning("home_assistant %s failed: transport (%s)", operation, type(exc).__name__)
        raise ConnectorError("transport_error") from None
    if 300 <= status < 400:
        # Redirects are never followed; one is a misconfiguration.
        raise ConnectorError("protocol_error")
    if status in (401, 403):
        raise ConnectorError("not_configured")
    if not 200 <= status < 300:
        return status, None
    try:
        return status, json.loads(b"".join(chunks) or b"null")
    except ValueError:
        raise ConnectorError("protocol_error") from None


def _text(value):
    return strip_invisible_formatting(str(value))[:TEXT_MAX_CHARS]


def public_state(raw, domains):
    """An HA state object reduced to what may leave the bridge, or `None`
    when it is malformed or outside `domains`."""
    if not isinstance(raw, dict):
        return None
    entity_id = raw.get("entity_id")
    if not isinstance(entity_id, str) or entity_domain(entity_id) not in domains:
        return None
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    kept = {}
    for key in sorted(ATTRIBUTE_WHITELIST & set(attributes)):
        value = attributes[key]
        if isinstance(value, bool) or value is None:
            kept[key] = value
        elif isinstance(value, int | float):
            kept[key] = value if math.isfinite(value) else None
        else:
            kept[key] = _text(value)
    return {
        "entity_id": entity_id,
        "state": _text(raw.get("state", "")),
        "attributes": kept,
        "last_changed": _text(raw.get("last_changed", "")),
    }


def _read_entity(client, entity_id, *, operation):
    status, body = _request(client, "GET", f"/api/states/{entity_id}", operation=operation)
    if status == 404:
        raise ConnectorError("target_missing")
    if body is None:
        raise ConnectorError("protocol_error")
    state = public_state(body, {entity_domain(entity_id)})
    if state is None or state["entity_id"] != entity_id:
        raise ConnectorError("protocol_error")
    return state


# --- the native-bridge contract (`connectors.bridges`) ---


def check_health(manifest, *, timeout):
    """Health + pin report for the 15-minute beat and on enable: the API
    answers with this token (`GET /api/config`), and every manifest tool's
    pin is compared with this module's own contract -- `ok`, `mismatch`
    (the contract changed without a manifest update) or `missing`. A failed
    check is a closed-vocabulary `error` (`not_configured` for a missing env
    or a refused token, which leaves nothing callable)."""
    try:
        with _client(timeout) as client:
            _status, body = _request(client, "GET", "/api/config", operation="health")
        if not isinstance(body, dict):
            raise ConnectorError("protocol_error")
    except ConnectorError as exc:
        return NativeHealth(SERVER_NAME, "", error=exc.code)
    version = body.get("version")
    hashes = tool_hashes()
    tools = {}
    for policy in manifest.tools:
        actual = hashes.get(policy.server_tool)
        if actual is None:
            tools[policy.name] = "missing"
        elif actual != policy.schema_sha256:
            tools[policy.name] = "mismatch"
        else:
            tools[policy.name] = "ok"
    return NativeHealth(
        SERVER_NAME,
        _text(version)[:40] if isinstance(version, str) else "",
        tools=tools,
        details={"api": "rest", "tool_hashes": hashes},
    )


def call_tool(manifest, *, server_tool, arguments, schema_sha256, timeout):
    hashes = tool_hashes()
    if server_tool not in hashes:
        raise ConnectorError("tool_missing", listing=_static_listing())
    if hashes[server_tool] != schema_sha256:
        raise ConnectorError("pin_mismatch", listing=_static_listing())
    if server_tool == SERVER_TOOL_STATES:
        return _get_states(arguments, timeout)
    return _call_service(arguments, timeout)


def _get_states(arguments, timeout):
    serializer = StatesArguments(data=arguments)
    if not serializer.is_valid():
        raise ConnectorError("not_allowed")
    args = serializer.validated_data
    domains = read_domains()
    with _client(timeout) as client:
        if args.get("entity_id"):
            entities = [_read_entity(client, args["entity_id"], operation="get_state")]
        else:
            status, body = _request(client, "GET", "/api/states", operation="get_states")
            if not isinstance(body, list):
                raise ConnectorError("protocol_error")
            if args.get("domain"):
                domains = {args["domain"]}
            entities = [s for s in (public_state(raw, domains) for raw in body) if s]
    entities.sort(key=lambda s: s["entity_id"])
    structured = {
        "entities": entities[:MAX_STATES],
        "total": len(entities),
        "omitted": max(len(entities) - MAX_STATES, 0),
    }
    return CallResult(
        texts=(), structured=structured, is_error=False, dropped_blocks=0, listing=_static_listing()
    )


def _call_service(arguments, timeout):
    serializer = CallServiceArguments(data=arguments)
    if not serializer.is_valid() or serializer.validated_data != arguments:
        # The allowlist (env or code) no longer admits exactly the approved
        # call: nothing is sent.
        raise ConnectorError("not_allowed")
    clean = serializer.validated_data
    domain, service, entity_id = clean["domain"], clean["service"], clean.get("entity_id")
    body = {"entity_id": entity_id, **clean["data"]} if entity_id else dict(clean["data"])
    with _client(timeout) as client:
        before = _read_entity(client, entity_id, operation="pre_check") if entity_id else None
        try:
            status, _ = _request(
                client,
                "POST",
                f"/api/services/{domain}/{service}",
                body=body,
                operation="call_service",
            )
        except ConnectorError as exc:
            # The request was handed to the transport: HA may have acted --
            # unless no connection was ever made, or HA answered 401/403
            # (a refusal). Only then is the write `failed` rather than
            # `unknown_outcome`.
            exc.sent = not getattr(exc, "unsent", False) and exc.code != "not_configured"
            raise
        if status >= 500:
            error = ConnectorError("protocol_error")
            error.sent = True
            raise error
        if status >= 400:
            return CallResult(
                texts=(f"Home Assistant refused {domain}.{service} (HTTP {status}).",),
                structured=None,
                is_error=True,
                dropped_blocks=0,
                listing=_static_listing(),
            )
        after, read_back = None, "none"
        if entity_id:
            try:
                after = _read_entity(client, entity_id, operation="read_back")
                read_back = "ok"
            except ConnectorError:
                read_back = "failed"
    target = entity_id or "the allowlisted MAC"
    return CallResult(
        texts=(f"Home Assistant accepted {domain}.{service} for {target}.",),
        structured={
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "state_before": before,
            "state_after": after,
            "read_back": read_back,
        },
        is_error=False,
        dropped_blocks=0,
        listing=_static_listing(),
    )


# --- decision dialog ---


def _value(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render_call(arguments):
    """The dialog rows from the snapshot: exact service, entity id and every
    data value, nothing paraphrased."""
    rows = [{"label": "Hizmet", "value": f"{arguments['domain']}.{arguments['service']}"}]
    if arguments.get("entity_id"):
        rows.append({"label": "Varlık (entity_id)", "value": arguments["entity_id"]})
    data = arguments.get("data") or {}
    if not data:
        rows.append({"label": "Veri", "value": "{}"})
    for key in sorted(data):
        rows.append({"label": f"data.{key}", "value": _value(data[key])})
    return rows


def request_rows(workspace, arguments):
    """Request-time read of the target (D8): the entity's HA-supplied name
    and its current state, labelled as such. A missing entity or an
    unreachable HA refuses the request (400) -- nothing is decided blind."""
    entity_id = arguments.get("entity_id")
    if not entity_id:
        return []
    try:
        with _client(settings.CONNECTOR_READ_TIMEOUT_SECONDS) as client:
            state = _read_entity(client, entity_id, operation="request_state")
    except ConnectorError as exc:
        if exc.code == "target_missing":
            raise ToolValidationError("Home Assistant has no such entity.") from None
        raise ToolValidationError(
            "Home Assistant could not be read to show the entity's current state."
        ) from None
    attributes = state["attributes"]
    rows = []
    if "friendly_name" in attributes:
        rows.append(
            {"label": "Ad (Home Assistant'tan gelen metin)", "value": attributes["friendly_name"]}
        )
    details = {k: v for k, v in attributes.items() if k != "friendly_name"}
    current = state["state"]
    if details:
        current += " " + json.dumps(details, ensure_ascii=False, sort_keys=True)
    rows.append({"label": "Şu anki durum (istek anında okundu)", "value": current})
    return rows


# --- execution precondition (`ToolPolicy.precondition`) ---

# `ConnectorError` codes -> `ConnectorWriteExecution.ErrorType` values; every
# one ends the execution `failed` with nothing sent.
_PRECONDITION_ERRORS = {
    "target_missing": "target_missing",
    "not_allowed": "connector_changed",
    "not_configured": "not_configured",
    "timeout": "timeout",
    "transport_error": "transport_error",
    "protocol_error": "protocol_error",
}


def target_present(manifest, binding, payload):
    """`ToolPolicy.precondition` of `ha_call_service`, run by the write task
    after the claim and before the one send: the approved call must still be
    inside the allowlist as it is *now* (`HA_ALLOWED_DOMAINS` or the MAC
    allowlist narrowed -> `connector_changed`) and the entity must still
    exist (`target_missing`). Any failure to confirm either refuses the send
    -- nothing reaches Home Assistant. `_call_service` re-checks both
    (defence in depth: the entity can vanish between the two reads)."""
    arguments = (payload or {}).get("arguments") or {}
    serializer = CallServiceArguments(data=arguments)
    if not serializer.is_valid() or serializer.validated_data != arguments:
        return "connector_changed"
    entity_id = arguments.get("entity_id")
    if not entity_id:
        return None
    try:
        with _client(settings.CONNECTOR_READ_TIMEOUT_SECONDS) as client:
            _read_entity(client, entity_id, operation="precondition")
    except ConnectorError as exc:
        return _PRECONDITION_ERRORS.get(exc.code, "unknown")
    return None


class HomeAssistantBridge:
    """The `BRIDGE` of `connectors.manifests.home_assistant`
    (`connectors.bridges.get_bridge`)."""

    server_name = SERVER_NAME

    def call_tool(self, manifest, *, server_tool, arguments, schema_sha256, timeout):
        return call_tool(
            manifest,
            server_tool=server_tool,
            arguments=arguments,
            schema_sha256=schema_sha256,
            timeout=timeout,
        )

    def check_health(self, manifest, *, timeout):
        return check_health(manifest, timeout=timeout)


BRIDGE = HomeAssistantBridge()
