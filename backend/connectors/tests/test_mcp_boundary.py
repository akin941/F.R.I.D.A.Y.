"""Production code touches only the MCP *client* side (ADR-010 decision 3,
brief D2). Two layers: the import-linter contract forbidding `mcp.server`
(and the stdio/SSE transports) is configured, and -- because a contract
cannot see `import mcp` / `from mcp import X`, which executes the package
`__init__` that pulls the server side in by name -- an AST scan allows only
explicit client/type modules."""

import ast
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
PYPROJECT = BACKEND / "pyproject.toml"
_SKIP = {".venv", "tests", "migrations", "node_modules", "__pycache__"}
# The stdio<->HTTP bridge runs only inside a connector sidecar (D1).
_BRIDGE = Path("connectors/docker/stdio_bridge.py")
ALLOWED_PREFIXES = ("mcp.client.client", "mcp.client.streamable_http", "mcp.types", "mcp_types")


def _mcp_imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "mcp" or alias.name.startswith(("mcp.", "mcp_types")):
                    yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "mcp" or node.module.startswith(("mcp.", "mcp_types")):
                yield node.module


def test_production_modules_import_only_the_client_side():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if _SKIP.intersection(rel.parts) or rel == _BRIDGE:
            continue
        for module in _mcp_imports(path):
            if not module.startswith(ALLOWED_PREFIXES):
                offenders.append(f"{rel}: {module}")
    assert not offenders, offenders


def test_the_facade_is_the_only_production_mcp_importer():
    importers = set()
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if _SKIP.intersection(rel.parts) or rel == _BRIDGE:
            continue
        if any(True for _ in _mcp_imports(path)):
            importers.add(str(rel))
    assert importers == {"connectors/mcp_client.py"}


def test_the_import_linter_contract_is_configured():
    config = tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]
    assert "mcp" in config["root_packages"]
    contract = next(
        c
        for c in config["contracts"]
        if c["type"] == "forbidden" and "mcp.server" in c["forbidden_modules"]
    )
    assert {"mcp.client.stdio", "mcp.client.sse"} <= set(contract["forbidden_modules"])
    for app in ("connectors", "assistant", "tools", "ai_core", "harness", "deploys"):
        assert app in contract["source_modules"]
