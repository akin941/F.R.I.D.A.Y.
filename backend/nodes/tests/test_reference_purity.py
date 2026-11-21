"""The reference node and the conformance suite import without Django
(Phase 10 brief branch 2): they are the executable spec the Swift node
mirrors and run outside the Django project."""

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

PROBE = r"""
import json, sys
import nodes.reference, nodes.reference.node, nodes.reference.verifier_v2
import nodes.reference.confirm, nodes.reference.executor, nodes.reference.sandbox
import nodes.reference.local_policy, nodes.reference.stream_scrub, nodes.reference.transport
import nodes.reference.worktree, nodes.reference._scrubber_vendored
import nodes.reference.fixtures.factory
import nodes.conformance, nodes.conformance.cases, nodes.conformance.runner
import nodes.conformance.gateway, nodes.conformance.build
import nodes.conformance.escape, nodes.conformance.escape.runner
nodes.conformance.cases.all_cases()
nodes.conformance.build.sandbox_document()
project = ("config", "core", "accounts", "workspaces", "audit", "ai_core", "assistant",
           "memory", "knowledge", "tasks", "approvals", "tools", "agents", "skills",
           "integrations", "harness", "connectors", "deploys", "ops", "voice", "hud")
allowed = ("nodes.protocol", "nodes.capabilities", "nodes.reference", "nodes.conformance")
loaded = sorted(
    name for name in sys.modules
    if name == "django" or name.startswith("django.") or name.split(".")[0] in project
    or (name.startswith("nodes.") and not name.startswith(allowed))
)
print(json.dumps(loaded))
"""


def test_reference_node_and_conformance_import_without_django():
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=BACKEND,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []
