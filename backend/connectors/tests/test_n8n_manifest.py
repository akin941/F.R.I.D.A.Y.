"""The n8n manifest and its Friday-side workflow-ID allowlist (Phase 12
D9/D10): `build_manifest` refusals, the committed reference entries, the
twin's table equal to `WORKFLOWS`, `n8n_relay_env` equal to the allowlist,
and the registrations (async read, gated write with draft arguments)."""

import dataclasses
import re
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from connectors.manifest import TOOL_READ, TOOL_WRITE, get_manifest, schema_hash
from connectors.manifests import n8n
from core.channels import CHANNEL_NODE_VOICE, CHANNEL_WEB
from tools.registry import (
    READ_MODE_ASYNC,
    get_read_tool,
    get_tool,
    list_tool_names,
)

DOCS = Path(__file__).resolve().parents[3] / "docs" / "mcp"
REPO = Path(__file__).resolve().parents[3]


def _wf(**change):
    base = n8n.WORKFLOWS[0]
    return dataclasses.replace(base, **change)


@pytest.mark.parametrize(
    "workflow_id", ["short", "has-dash-1234567", "x" * 33, "white space12", "", "wf_Ab12Cd34"]
)
def test_a_bad_workflow_id_is_refused(workflow_id):
    with pytest.raises(ValueError):
        n8n.build_manifest([_wf(workflow_id=workflow_id)])


def test_a_duplicate_workflow_id_is_refused():
    with pytest.raises(ValueError):
        n8n.build_manifest([_wf(), _wf(tool="n8n_other_status")])


def test_a_duplicate_friday_name_is_refused():
    with pytest.raises(ValueError):
        n8n.build_manifest([_wf(), _wf(workflow_id="Zz99Yy88Xx77Ww66")])


@pytest.mark.parametrize("tool", ["server_status", "N8n_status", "n8n-status", "n8n_"])
def test_a_friday_name_outside_the_convention_is_refused(tool):
    with pytest.raises(ValueError):
        n8n.build_manifest([_wf(tool=tool)])


def test_a_write_without_a_renderer_is_refused():
    write = dataclasses.replace(n8n.WORKFLOWS[1], renderer=None)
    with pytest.raises(ValueError):
        n8n.build_manifest([write])


def test_an_async_write_is_refused():
    write = dataclasses.replace(n8n.WORKFLOWS[1], mode=READ_MODE_ASYNC)
    with pytest.raises(ValueError):
        n8n.build_manifest([write])


def test_a_server_tool_outside_the_wf_convention_is_refused():
    manifest = n8n.build_manifest(n8n.WORKFLOWS)
    policy = dataclasses.replace(manifest.tools[0], server_tool="search_workflows")
    with pytest.raises(ValueError):
        n8n.check_server_tools(dataclasses.replace(manifest, tools=(policy,)))


def test_the_committed_manifest():
    manifest = get_manifest("n8n")
    assert manifest is n8n.MANIFEST
    assert manifest.data_class == "personal"
    assert manifest.workspace_types == frozenset({"personal"})
    assert manifest.read_channels == frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE})
    assert manifest.url_env == "N8N_MCP_URL"
    assert not manifest.credential_envs  # the bearer lives in the sidecar only
    assert [t.server_tool for t in manifest.tools] == [wf.server_tool for wf in n8n.WORKFLOWS]
    for policy in manifest.tools:
        assert re.match(r"^wf_[A-Za-z0-9]{8,32}$", policy.server_tool)
    # The pins are the reference schemas' hashes (the live trigger is re-pinned
    # by the owner before enabling).
    for wf in n8n.WORKFLOWS:
        assert wf.schema_sha256 == schema_hash(n8n.REFERENCE_SCHEMAS[wf.server_tool])


def test_the_async_read_is_registered_async_over_web_and_node_voice():
    spec = get_read_tool("n8n_server_status")
    assert spec.mode == READ_MODE_ASYNC
    assert spec.data_class == "personal"
    assert spec.channels == frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE})
    assert spec.label == "Sunucu durumu"
    assert callable(spec.start_async) and callable(spec.fetch_async)


def test_the_write_is_a_user_invocable_gate_with_draft_arguments():
    spec = get_tool("mcp_n8n_home_backup")
    assert spec.gated and spec.user_invocable
    assert "mcp_n8n_home_backup" in list_tool_names()
    assert spec.draft_arguments is not None
    assert spec.draft_arguments.data_class == "personal"
    assert spec.draft_arguments.argument_serializer is n8n.HomeBackupArgs
    assert spec.label == "Ev yedeği"
    # No n8n tool is ever a read *and* a gate.
    assert get_read_tool("mcp_n8n_home_backup") is None
    assert get_tool("n8n_server_status") is None


def _twin_rows():
    text = (DOCS / "n8n.md").read_text("utf-8")
    rows = []
    for line in text.splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and cells[0].startswith("n8n_"):
            rows.append(cells)
    return rows


def test_the_twin_table_equals_the_allowlist():
    rows = _twin_rows()
    expected = [
        [
            wf.tool,
            wf.workflow_id,
            wf.server_tool,
            "read" if wf.kind == TOOL_READ else "write",
            wf.mode if wf.kind == TOOL_READ else "sync",
            wf.schema_sha256,
        ]
        for wf in n8n.WORKFLOWS
    ]
    assert [row[:6] for row in rows] == expected
    assert {wf.kind for wf in n8n.WORKFLOWS} == {TOOL_READ, TOOL_WRITE}


def test_the_allowlist_has_its_row():
    allowlist = (DOCS / "allowlist.md").read_text("utf-8")
    assert "`n8n` connector" in allowlist


def test_n8n_relay_env_prints_exactly_the_allowlist():
    out = StringIO()
    call_command("n8n_relay_env", stdout=out)
    line = out.getvalue().strip()
    # The deploy/docker/.env variable compose reads for the sidecar's
    # BRIDGE_ALLOWED_TOOLS (Phase 12 closeout).
    assert line == "N8N_RELAY_ALLOWED_TOOLS=wf_Ab12Cd34Ef56Gh78,wf_Hb34Kd56Mf78Np90"
    assert line.split("=", 1)[1].split(",") == [wf.server_tool for wf in n8n.WORKFLOWS]


def test_the_reference_export_wires_exactly_the_allowlisted_tools():
    import json

    export = json.loads((REPO / "deploy" / "n8n" / "friday-tools.workflow.json").read_text())
    nodes = export["nodes"]
    trigger = [n for n in nodes if n["type"] == "@n8n/n8n-nodes-langchain.mcpTrigger"]
    assert len(trigger) == 1
    assert trigger[0]["parameters"]["authentication"] == "bearerAuth"
    tools = [n for n in nodes if n["type"] == "@n8n/n8n-nodes-langchain.toolWorkflow"]
    assert sorted(n["name"] for n in tools) == sorted(wf.server_tool for wf in n8n.WORKFLOWS)
    for node in tools:
        workflow_id = node["name"].removeprefix("wf_")
        assert node["parameters"]["workflowId"]["value"] == workflow_id
        assert node["name"] in export["connections"]
    # No credential is committed in the export: the trigger names an empty
    # credential slot the owner fills in n8n.
    assert trigger[0]["credentials"]["httpBearerAuth"]["id"] == ""
    assert not re.search(r"Bearer [A-Za-z0-9_.-]{12,}", json.dumps(export))
