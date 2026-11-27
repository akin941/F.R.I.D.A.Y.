"""The package imports nothing from `backend/` (D10, D16): importing every
module in a fresh interpreter loads no `nodes`, `integrations`, `django`
or other backend top-level package, and the only third-party packages are
the three pinned ones."""

import json
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = PACKAGE_ROOT.parents[1] / "backend"

PROBE = """
import json, sys
import friday_node_linux, friday_node_linux.cli, friday_node_linux.conformance
import friday_node_linux.daemon, friday_node_linux.readers_root, friday_node_linux.credential
print(json.dumps(sorted({name.split(".")[0] for name in sys.modules})))
"""


def backend_top_level() -> set:
    return {
        path.name
        for path in BACKEND.iterdir()
        if (path.is_dir() and (path / "__init__.py").exists()) or path.suffix == ".py"
    } | {"django", "rest_framework", "celery"}


def test_importing_the_package_loads_no_backend_module():
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
        cwd=PACKAGE_ROOT.parent,  # not the package dir: no accidental sys.path entry
    )
    loaded = set(json.loads(result.stdout))
    forbidden = {name.removesuffix(".py") for name in backend_top_level()}
    assert "nodes" in forbidden and "integrations" in forbidden
    assert not loaded & forbidden, loaded & forbidden
    third_party = loaded - set(sys.stdlib_module_names) - {"friday_node_linux", "__main__"}
    allowed = {"cryptography", "jsonschema", "wsproto", "h11", "attrs", "attr", "referencing"}
    allowed |= {"jsonschema_specifications", "rpds", "_cffi_backend", "cffi", "typing_extensions"}
    allowed |= {"_openssl", "_virtualenv", "sitecustomize"}  # cryptography, the venv itself
    assert third_party <= allowed, third_party - allowed


def test_the_vendored_code_never_imports_a_backend_package():
    vendor = PACKAGE_ROOT / "friday_node_linux" / "_vendor"
    for path in vendor.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("from nodes", "import nodes")):
                raise AssertionError(f"{path}: {line}")
            if stripped.startswith(("from integrations", "import integrations")):
                raise AssertionError(f"{path}: {line}")
