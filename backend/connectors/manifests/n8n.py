"""n8n -- allowlisted workflows behind the relay sidecar (Phase 12 D9-D12).

The owner's n8n runs one dedicated workflow, "Friday tools", whose **MCP
Server Trigger** exposes one tool per allowlisted workflow; each tool node
is a "Call n8n Workflow Tool" named `wf_<workflowId>`
(`deploy/n8n/friday-tools.workflow.json` is the reference export). The
trigger requires a bearer credential, which lives only in the `n8n-relay`
sidecar (`connectors/docker/http_relay.py`, env `RELAY_BEARER_TOKEN`);
Friday reaches the relay at `N8N_MCP_URL` on the internal `connectors`
network and never holds the credential.

`WORKFLOWS` below is the **Friday-side workflow-ID allowlist** (D10): the
only n8n workflows Friday can ever list or call. Three ceilings stack:
n8n's trigger exposes only the tools the owner wired, the relay's
`BRIDGE_ALLOWED_TOOLS` (printed by `manage.py n8n_relay_env` from this
table) lists and calls only these names, and the manifest registers only
these entries with pinned input schemas. Adding a workflow is a reviewed
commit (this table + `docs/mcp/n8n.md` + its live pin), never a database
write. n8n's instance-level MCP (workflow search/create/publish, a
`workflowId` argument) is never used (D9).

`personal` data class: the owner's automations touch home, mail and
personal services, so the connector binds to the personal workspace only,
reads run over `web` and `node_voice` only, and every read marks the
conversation `personal_data`. Writes are gate-class, decided over the web,
sent once (D12); no n8n write is confirm-class.

The two entries are the reference workflows of the export (a slow status
read and a backup write). Their pins are the sha256 of the reference input
schemas (`REFERENCE_SCHEMAS`); the owner re-pins each against the live
trigger before enabling it (owner step, `docs/mcp/n8n.md`).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rest_framework import serializers

from connectors.manifest import (
    KIND_MCP,
    STATUS_ACTIVE,
    TOOL_READ,
    TOOL_WRITE,
    WORKSPACE_PERSONAL,
    ConnectorManifest,
    ToolPolicy,
)
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from tools.registry import READ_MODE_ASYNC, READ_MODE_SYNC, READ_MODES

NAME = "n8n"
URL_ENV = "N8N_MCP_URL"
# Sidecar-side only (`n8n-relay` service); listed here as names so the
# governance twin and the no-credential test can find them. Never read by
# web or worker.
RELAY_ENVS = ("RELAY_UPSTREAM_URL", "RELAY_BEARER_TOKEN")
TIMEOUT_SETTING = "N8N_ASYNC_READ_TIMEOUT_SECONDS"

WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
SERVER_TOOL_RE = re.compile(r"^wf_[A-Za-z0-9]{8,32}$")
_TOOL_NAME_RE = re.compile(r"^n8n_[a-z][a-z0-9_]{1,34}$")


@dataclass(frozen=True)
class N8nWorkflow:
    """One allowlisted n8n workflow. Everything here is Friday-authored:
    the name, label and description are the only text about the workflow
    that ever reaches a prompt (n8n's own tool description never does)."""

    tool: str  # Friday name, `n8n_<snake_case>`
    workflow_id: str  # n8n's id; the trigger's tool is `wf_<id>`
    kind: str  # TOOL_READ | TOOL_WRITE
    description: str
    label: str
    argument_serializer: Any
    schema_sha256: str
    mode: str = READ_MODE_SYNC  # reads: "sync" (fast) | "async" (D11)
    renderer: Callable[[dict], list] | None = None  # writes: decision rows

    @property
    def server_tool(self):
        return f"wf_{self.workflow_id}"


def build_manifest(workflows, *, version=1):
    """The `ConnectorManifest` of an allowlist. Refuses (ValueError) a
    workflow id outside `^[A-Za-z0-9]{8,32}$`, a duplicate id or Friday
    name, a Friday name outside `n8n_<snake_case>`, an unknown kind or
    mode, an async write, a write without a renderer, and any server tool
    outside the `wf_<id>` convention."""
    ids, names, policies = set(), set(), []
    for wf in workflows:
        if not isinstance(wf.workflow_id, str) or not WORKFLOW_ID_RE.match(wf.workflow_id):
            raise ValueError(f"n8n: workflow id {wf.workflow_id!r} is not 8-32 letters/digits.")
        if not _TOOL_NAME_RE.match(wf.tool or ""):
            raise ValueError(f"n8n: tool name {wf.tool!r} must be n8n_<snake_case>.")
        if wf.workflow_id in ids:
            raise ValueError(f"n8n: workflow {wf.workflow_id!r} is listed twice.")
        if wf.tool in names:
            raise ValueError(f"n8n: tool {wf.tool!r} is listed twice.")
        if wf.kind not in (TOOL_READ, TOOL_WRITE):
            raise ValueError(f"n8n: {wf.tool}: unknown kind {wf.kind!r}.")
        if wf.mode not in READ_MODES or (wf.kind == TOOL_WRITE and wf.mode != READ_MODE_SYNC):
            raise ValueError(f"n8n: {wf.tool}: a write is sync; a read is sync or async.")
        if wf.kind == TOOL_WRITE and wf.renderer is None:
            raise ValueError(f"n8n: {wf.tool}: a write needs a renderer for its decision.")
        ids.add(wf.workflow_id)
        names.add(wf.tool)
        write = wf.kind == TOOL_WRITE
        policies.append(
            ToolPolicy(
                name=wf.tool,
                server_tool=wf.server_tool,
                kind=wf.kind,
                description=wf.description,
                argument_serializer=wf.argument_serializer,
                schema_sha256=wf.schema_sha256,
                mode=wf.mode,
                label=wf.label,
                timeout_setting=TIMEOUT_SETTING if wf.mode == READ_MODE_ASYNC else "",
                # D12: gate `mcp_n8n_<name>`, user-invocable, decided over
                # the web, a private personal work_log; the draft carries
                # router-extracted arguments for the owner to check.
                gate_name=f"mcp_{wf.tool}" if write else "",
                user_invocable=True,
                renderer=wf.renderer,
                records_work_log=True,
                draft_arguments=write,
            )
        )
    manifest = ConnectorManifest(
        name=NAME,
        version=version,
        display_name="n8n",
        kind=KIND_MCP,
        status=STATUS_ACTIVE,
        data_class="personal",
        workspace_types=frozenset({WORKSPACE_PERSONAL}),
        read_channels=frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE}),
        url_env=URL_ENV,
        tools=tuple(policies),
    )
    check_server_tools(manifest)
    return manifest


def check_server_tools(manifest):
    """Every tool of an n8n manifest calls a `wf_<id>` server tool -- never
    an instance-level tool (`search_workflows`, `execute_workflow`, ...)."""
    for policy in manifest.tools:
        if not SERVER_TOOL_RE.match(policy.server_tool or ""):
            raise ValueError(f"n8n: {policy.name}: server tool must be wf_<workflowId>.")


def relay_allowed_tools(workflows=None):
    """The relay's `BRIDGE_ALLOWED_TOOLS` value: exactly the allowlist."""
    return ",".join(wf.server_tool for wf in (WORKFLOWS if workflows is None else workflows))


# --- the reference workflows --------------------------------------------------


class ServerStatusArgs(serializers.Serializer):
    scope = serializers.ChoiceField(
        choices=["summary", "full"],
        required=False,
        default="summary",
        help_text="summary (short) or full (every service)",
    )


class HomeBackupArgs(serializers.Serializer):
    target = serializers.ChoiceField(
        choices=["nas", "cloud"], help_text="nas (the home NAS) or cloud (the offsite copy)"
    )


_TARGET_LABELS = {"nas": "Ev NAS'ı", "cloud": "Bulut kopyası"}


def render_home_backup(arguments):
    target = arguments.get("target", "")
    return [
        {"label": "İş akışı", "value": "Ev yedeği (n8n)"},
        {"label": "Hedef", "value": f"{_TARGET_LABELS.get(target, target)} ({target})"},
    ]


# The input schemas the reference export's tool nodes expose (what the fake
# n8n server serves in tests). The pins below are their `schema_hash`; a
# test keeps them equal. The live trigger may differ: re-pin before enable.
REFERENCE_SCHEMAS = {
    "wf_Ab12Cd34Ef56Gh78": {
        "type": "object",
        "properties": {"scope": {"type": "string", "description": "summary or full"}},
        "required": ["scope"],
        "additionalProperties": False,
    },
    "wf_Hb34Kd56Mf78Np90": {
        "type": "object",
        "properties": {"target": {"type": "string", "description": "nas or cloud"}},
        "required": ["target"],
        "additionalProperties": False,
    },
}

SERVER_STATUS_PIN = "007afe51a2b6d458b9b2b656f7e018d33dc6c77ab91005bb1050bb630d0f0136"
HOME_BACKUP_PIN = "28fb35f96176ee3e034f64a3a4462245b63ff573923a58df90b0bb68911946a9"

WORKFLOWS = (
    N8nWorkflow(
        tool="n8n_server_status",
        workflow_id="Ab12Cd34Ef56Gh78",
        kind=TOOL_READ,
        mode=READ_MODE_ASYNC,
        label="Sunucu durumu",
        description=(
            "Check the owner's home server through their n8n automation: disk, "
            "services and the last backup (slow; the answer arrives as a follow-up)."
        ),
        argument_serializer=ServerStatusArgs,
        schema_sha256=SERVER_STATUS_PIN,
    ),
    N8nWorkflow(
        tool="n8n_home_backup",
        workflow_id="Hb34Kd56Mf78Np90",
        kind=TOOL_WRITE,
        label="Ev yedeği",
        description=(
            "Start the owner's home backup n8n workflow to the NAS or the cloud copy, "
            "after the owner's decision on screen."
        ),
        argument_serializer=HomeBackupArgs,
        schema_sha256=HOME_BACKUP_PIN,
        renderer=render_home_backup,
    ),
)

MANIFEST = build_manifest(WORKFLOWS)
