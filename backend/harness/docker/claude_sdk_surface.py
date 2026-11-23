#!/usr/bin/env python3
"""Writes the surface snapshot of the INSTALLED `claude-agent-sdk` (Phase 10
brief D10 "Pins and contract tests", branch 7).

Run by `generate_driver_schemas.sh claude <image>` inside the built image,
with the image's bridge interpreter (`/opt/friday-bridge/bin/python -I`),
with no network. Standard library plus the SDK only. Prints one JSON
document (format `friday-claude-agent-sdk-surface/v1`) to stdout:

- `version`: the installed distribution's version;
- `classes`: every public class the module exports, with its fields
  (dataclass fields, else annotations) and public methods (plus the async
  context-manager dunders);
- `functions`: every public function it exports;
- `provisional: false`.

The host side compares it with the pin and the bridge's `SDK_SURFACE_USED`
(`harness/tests/test_claude_driver_contract.py`); method names are read
from the package, never written from memory.

    python -I claude_sdk_surface.py [module] [distribution]
"""

import dataclasses
import importlib
import inspect
import json
import sys

FORMAT = "friday-claude-agent-sdk-surface/v1"
DUNDERS = ("__aenter__", "__aexit__", "__aiter__", "__anext__")


def _version(module, distribution):
    try:
        from importlib import metadata

        return metadata.version(distribution)
    except Exception:
        version = getattr(module, "__version__", "")
        return version if isinstance(version, str) else ""


def _fields(cls):
    if dataclasses.is_dataclass(cls):
        return sorted(field.name for field in dataclasses.fields(cls))
    annotations = {}
    for base in reversed(cls.__mro__):
        annotations.update(getattr(base, "__annotations__", {}) or {})
    return sorted(name for name in annotations if not name.startswith("_"))


def _methods(cls):
    names = set()
    for name, value in inspect.getmembers(cls):
        if name in DUNDERS or (not name.startswith("_") and callable(value)):
            names.add(name)
    return sorted(names)


def surface(module_name="claude_agent_sdk", distribution="claude-agent-sdk"):
    module = importlib.import_module(module_name)
    exported = getattr(module, "__all__", None) or [
        name for name in dir(module) if not name.startswith("_")
    ]
    classes, functions = {}, []
    for name in sorted(set(exported)):
        value = getattr(module, name, None)
        if inspect.isclass(value):
            classes[name] = {"fields": _fields(value), "methods": _methods(value)}
        elif inspect.isfunction(value) or inspect.iscoroutinefunction(value):
            functions.append(name)
    version = _version(module, distribution)
    if not version:
        raise SystemExit("installed SDK reports no version")
    return {
        "schema_format": FORMAT,
        "package": distribution,
        "module": module_name,
        "version": version,
        "provisional": False,
        "classes": classes,
        "functions": sorted(functions),
    }


def main(argv):
    module_name = argv[0] if argv else "claude_agent_sdk"
    distribution = argv[1] if len(argv) > 1 else "claude-agent-sdk"
    json.dump(surface(module_name, distribution), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
