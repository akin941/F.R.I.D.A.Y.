"""Phase 12 D19: `proactive` is a registered, linted, tested kernel app and
its import contracts are configured as the brief states (`lint-imports`
enforces them; this test keeps anyone from quietly narrowing one)."""

import tomllib
from pathlib import Path

from django.apps import apps

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
APPS = [
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
    "agents",
    "skills",
    "integrations",
    "harness",
    "connectors",
    "deploys",
    "ops",
    "voice",
    "hud",
    "nodes",
]
MAY_IMPORT = {"core", "workspaces", "audit", "ai_core", "accounts"}
IMPORTERS = {"assistant", "hud", "ops"}
# Branch 2 widens the hud contract by `proactive` (the notice section);
# until then hud's own allowlist still forbids it.
NOT_YET = {"hud"}


def _config():
    return tomllib.loads(PYPROJECT.read_text())


def _pairs(config):
    pairs = set()
    for contract in config["tool"]["importlinter"]["contracts"]:
        if contract["type"] == "forbidden":
            for source in contract["source_modules"]:
                for target in contract["forbidden_modules"]:
                    pairs.add((source, target))
    return pairs


def test_registered_linted_and_tested():
    assert apps.get_app_config("proactive").name == "proactive"
    config = _config()
    assert "proactive" in config["tool"]["importlinter"]["root_packages"]
    assert "proactive" in config["tool"]["pytest"]["ini_options"]["testpaths"]


def test_proactive_may_import_only_its_allowlist():
    pairs = _pairs(_config())
    for other in APPS:
        assert (("proactive", other) in pairs) == (other not in MAY_IMPORT), other
    assert ("proactive", "config") in pairs


def test_only_assistant_hud_ops_may_import_proactive():
    pairs = _pairs(_config())
    for other in APPS:
        expected = other not in IMPORTERS or other in NOT_YET
        assert ((other, "proactive") in pairs) == expected, other


def test_assistant_and_hud_only_through_the_api():
    config = _config()
    [contract] = [
        c
        for c in config["tool"]["importlinter"]["contracts"]
        if c["name"].startswith("assistant and hud reach proactive only through proactive.api")
    ]
    assert set(contract["source_modules"]) == {"assistant", "hud"}
    forbidden = set(contract["forbidden_modules"])
    assert {"proactive.services", "proactive.tasks", "proactive.views"} <= forbidden
    assert "proactive.api" not in forbidden
