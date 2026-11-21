"""`nodes.protocol` and `nodes.capabilities` import without Django (Phase 9
brief D2): they are the spec the Swift node mirrors, and the reference node
(branch 3) runs outside the Django project. A subprocess with a clean
interpreter imports them and asserts no Django and no other project
package entered `sys.modules`."""

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

PROBE = r"""
import json, sys
import nodes.protocol
import nodes.protocol.canonical, nodes.protocol.signing, nodes.protocol.messages
import nodes.protocol.grants, nodes.protocol.verify
import nodes.capabilities
from nodes.capabilities import load_action_contracts, load_contracts, load_recipes
from nodes.protocol import SigningKey, build_invoke, sign_envelope, validate, verify_envelope
from nodes.protocol import NodeState, build_gated_invoke, verify_invoke

load_contracts()
load_action_contracts()
load_recipes()
gate_key = SigningKey.generate()
gated = build_gated_invoke(
    signer=gate_key,
    approval_id="3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b0a",
    node_id="7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70",
    workspace_id="0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b",
    capability="repo.command.run",
    capability_version=1,
    args={"repo_path": "~/work/acme", "command_kind": "test"},
    trace_id="t",
    issued_at=1800000000,
    ttl_seconds=90,
)
verify_invoke(
    gated,
    version=2,
    state=NodeState(
        node_id="7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70",
        pinned={gate_key.key_id: gate_key.public_key_bytes},
        catalogue={n: c.catalogue_entry() for n, c in load_action_contracts().items()},
    ),
    now=1800000001,
)
key = SigningKey.generate()
envelope = sign_envelope(
    build_invoke(
        node_id="7d0f2b8e-1c4a-4f5e-9b6d-2a3c4d5e6f70",
        workspace_id="0b1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b",
        capability="apps.frontmost",
        capability_version=1,
        args={},
        trace_id="t",
        issued_at=1800000000,
        ttl_seconds=30,
    ),
    key,
)
validate(envelope, "server_to_node")
verify_envelope(envelope, {key.key_id: key.public_key_bytes})
project = ("config", "core", "accounts", "workspaces", "audit", "ai_core", "assistant",
           "memory", "knowledge", "tasks", "approvals", "tools", "agents", "skills",
           "integrations", "harness", "connectors", "deploys", "ops", "voice", "hud")
loaded = sorted(
    name for name in sys.modules
    if name == "django" or name.startswith("django.") or name.split(".")[0] in project
    or (name.startswith("nodes.") and not name.startswith(("nodes.protocol", "nodes.capabilities")))
)
print(json.dumps(loaded))
"""


def test_protocol_and_capabilities_import_without_django():
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)}
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []
