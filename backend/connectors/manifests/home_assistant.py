"""Home Assistant -- native REST bridge (brief D8, R5, Q7).

`personal` data, personal workspace only (a firm workspace cannot bind),
reads over the web only. One read (`ha_states`: allowed domains, whitelisted
attributes) and one gated, user-invocable write (`ha_call_service`: one
allowlisted service on one exact entity, per-service serializers, the
entity's name and current state read into the dialog at request time, the
entity's existence and the allowlist re-checked as the execution's
precondition, the state read back after execution).
`records_work_log=False`: a light toggle is audit-worthy, not memory-worthy
(brief D13). No domain is ungated or confirm-class (Q7).

Version 2 (Phase 13 D11/D12): every entity call needs an enabled,
owner-set `connectors.HomeEntity` whose `services` include the service
(`scope_check` at request time, the precondition before the send; an empty
allowlist allows no entity call -- Wake-on-LAN stays on the MAC allowlist);
`media_player` gains `turn_on` / `turn_off` / `select_source` (a live
`source_list` value) / `volume_mute` / `volume_up` / `volume_down`;
`ha_states` keeps `source` / `source_list` (bounded) and annotates
allowlisted entities with their room and label. Every call stays gate-class.

The pins are the sha256 of the bridge's own request contract
(`connectors.bridges.home_assistant.tool_schemas()`); widening the
allowlist means updating them and bumping `version` in the same reviewed
commit (`docs/mcp/home-assistant.md`), else the tools disable themselves.
"""

from connectors.bridges import home_assistant as bridge
from connectors.manifest import (
    KIND_NATIVE,
    STATUS_ACTIVE,
    TOOL_READ,
    TOOL_WRITE,
    WORKSPACE_PERSONAL,
    ConnectorManifest,
    ToolPolicy,
)
from core.channels import CHANNEL_WEB

# The native bridge `connectors.bridges.get_bridge` dispatches to.
BRIDGE = bridge.BRIDGE

STATES_PIN = "772b49f1813b8f1bbd3e9357a8a03d063ce5b3fb3924c28e317eade8bfdaf2d6"
CALL_SERVICE_PIN = "a3487095bf60e4b8cf67592b24f3e0bf529187e30c792a6f8dd047ee8e20ba79"

MANIFEST = ConnectorManifest(
    name="home_assistant",
    version=2,
    display_name="Home Assistant",
    kind=KIND_NATIVE,
    status=STATUS_ACTIVE,
    data_class="personal",
    workspace_types=frozenset({WORKSPACE_PERSONAL}),
    read_channels=frozenset({CHANNEL_WEB}),
    url_env=bridge.BASE_URL_ENV,
    credential_envs=(bridge.TOKEN_ENV,),
    tools=(
        ToolPolicy(
            name="ha_states",
            server_tool=bridge.SERVER_TOOL_STATES,
            kind=TOOL_READ,
            description=(
                "Read the current state of the owner's Home Assistant devices: one exact "
                "entity (entity_id, e.g. light.salon), one domain (e.g. light, climate, "
                "sensor) or every allowed entity."
            ),
            argument_serializer=bridge.StatesArguments,
            schema_sha256=STATES_PIN,
            annotate=bridge.annotate_states,
        ),
        ToolPolicy(
            name="ha_call_service",
            server_tool=bridge.SERVER_TOOL_CALL,
            kind=TOOL_WRITE,
            description=(
                "Call one Home Assistant service the owner allowlisted for one exact "
                "entity (lights, switches, fans, covers, climate, media players: power, "
                "input, volume, mute, play/pause; scenes; Wake-on-LAN) after the owner's "
                "decision."
            ),
            argument_serializer=bridge.CallServiceArguments,
            schema_sha256=CALL_SERVICE_PIN,
            scope_check=bridge.check_allowlist,
            gate_name="ha_call_service",
            user_invocable=True,
            renderer=bridge.render_call,
            request_rows=bridge.request_rows,
            precondition=bridge.target_present,
            records_work_log=False,
        ),
    ),
)
