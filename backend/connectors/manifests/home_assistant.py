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

STATES_PIN = "f24e86599d4763a6bf1fa3cb0093bcc51d8d3c942629106323df3288b4ad0822"
CALL_SERVICE_PIN = "230b3efed4a19051774cb937ce9303212a4183428d61632ad56d0bc3351708c7"

MANIFEST = ConnectorManifest(
    name="home_assistant",
    version=1,
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
        ),
        ToolPolicy(
            name="ha_call_service",
            server_tool=bridge.SERVER_TOOL_CALL,
            kind=TOOL_WRITE,
            description=(
                "Call one allowlisted Home Assistant service on one exact entity "
                "(lights, switches, fans, covers, climate, media players, scenes, "
                "Wake-on-LAN) after the owner's decision."
            ),
            argument_serializer=bridge.CallServiceArguments,
            schema_sha256=CALL_SERVICE_PIN,
            gate_name="ha_call_service",
            user_invocable=True,
            renderer=bridge.render_call,
            request_rows=bridge.request_rows,
            precondition=bridge.target_present,
            records_work_log=False,
        ),
    ),
)
