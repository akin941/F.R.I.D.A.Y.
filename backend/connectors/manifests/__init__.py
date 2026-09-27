"""Committed connector manifests (ADR-010 decision 3, brief D3).

`BUILTIN` names every manifest module in this package; `load_builtin()`
registers them at startup (`ConnectorsConfig.ready`). A new connector is a
new module here, its name added to `BUILTIN`, its governance twin
`docs/mcp/<name>.md` and its row in `docs/mcp/allowlist.md` -- one reviewed
commit (`connectors/tests/test_manifests.py` checks all four stay in step).
"""

from importlib import import_module

from connectors.manifest import get_manifest, register_manifest

BUILTIN = ("external_installation", "home_assistant")


def load_builtin():
    for name in BUILTIN:
        manifest = import_module(f"connectors.manifests.{name}").MANIFEST
        if get_manifest(manifest.name) is None:
            register_manifest(manifest)
