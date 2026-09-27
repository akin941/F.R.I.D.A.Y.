"""Phase 5 branch 1 (ADR-010): `connectors` and `deploys` exist as
registered kernel apps before any code lands, and the import-linter
contracts ADR-010 names are actually configured -- `lint-imports` enforces
them, this test keeps anyone from quietly deleting or narrowing one."""

import tomllib
from pathlib import Path

import pytest
from django.apps import apps

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

KERNEL_APPS = [
    "core",
    "accounts",
    "workspaces",
    "audit",
    "ai_core",
    "assistant",
    "memory",
    "knowledge",
    "tasks",
    "approvals",
    "tools",
    "skills",
    "agents",
    "integrations",
    "harness",
    "connectors",
    "deploys",
]

# ADR-010's allowlists: what each new app may import (besides itself).
_BASE_ALLOWED = {"core", "workspaces", "audit", "ai_core", "tools", "memory"}
ALLOWED = {
    "connectors": _BASE_ALLOWED,
    "deploys": _BASE_ALLOWED | {"connectors", "approvals"},
}
# Who may import each new app. Everyone else is forbidden.
IMPORTERS = {
    "connectors": {"deploys", "harness"},
    "deploys": set(),
}


def _forbidden_pairs():
    config = tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]
    pairs = set()
    for contract in config["contracts"]:
        if contract["type"] != "forbidden":
            continue
        for source in contract["source_modules"]:
            for target in contract["forbidden_modules"]:
                pairs.add((source, target))
    return config, pairs


@pytest.mark.parametrize("label", ["connectors", "deploys"])
def test_app_is_registered(label):
    assert apps.get_app_config(label).name == label


@pytest.mark.parametrize("label", ["connectors", "deploys"])
def test_app_is_a_linted_and_tested_root_package(label):
    config, _ = _forbidden_pairs()
    assert label in config["root_packages"]
    testpaths = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]["testpaths"]
    assert label in testpaths


@pytest.mark.parametrize("app", sorted(ALLOWED))
def test_new_app_may_import_only_its_allowlist(app):
    _, pairs = _forbidden_pairs()
    for other in KERNEL_APPS:
        if other == app:
            continue
        forbidden = (app, other) in pairs
        assert forbidden == (other not in ALLOWED[app]), (app, other)


@pytest.mark.parametrize("app", sorted(IMPORTERS))
def test_only_named_apps_may_import_the_new_app(app):
    _, pairs = _forbidden_pairs()
    for other in KERNEL_APPS:
        if other == app:
            continue
        forbidden = (other, app) in pairs
        assert forbidden == (other not in IMPORTERS[app]), (other, app)
