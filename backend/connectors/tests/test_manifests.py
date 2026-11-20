"""Manifests are code with governance twins (brief D3, D14)."""

from pathlib import Path

import pytest

from connectors.manifest import (
    STATUS_BLOCKED,
    get_manifest,
    list_manifests,
    register_manifest,
    schema_hash,
    validate_manifest,
)
from connectors.manifests import BUILTIN
from connectors.tests import fakes
from tools.registry import get_read_tool, get_tool

DOCS = Path(__file__).resolve().parents[3] / "docs" / "mcp"
MANIFEST_DIR = Path(__file__).resolve().parents[1] / "manifests"


def test_every_manifest_module_is_builtin_and_has_its_governance_twin():
    modules = {p.stem for p in MANIFEST_DIR.glob("*.py") if p.stem != "__init__"}
    assert modules == set(BUILTIN)
    allowlist = (DOCS / "allowlist.md").read_text()
    for name in BUILTIN:
        assert get_manifest(name) is not None, name
        # The twin may spell underscores as hyphens (`home_assistant` ->
        # `docs/mcp/home-assistant.md`, the file name the Phase 5 brief uses).
        twins = {DOCS / f"{name}.md", DOCS / f"{name.replace('_', '-')}.md"}
        assert any(twin.exists() for twin in twins), f"docs/mcp/{name}.md is missing"
        assert f"`{name}` connector" in allowlist, f"allowlist row for {name} is missing"


def test_template_exists():
    assert (DOCS / "_template.md").exists()


def test_external_installation_is_a_blocked_read_only_firm_placeholder():
    ext = get_manifest("external_installation")
    assert ext.status == STATUS_BLOCKED
    assert ext.data_class == "firm"
    assert ext.workspace_types == frozenset({"company"})
    assert ext.writes == ()
    assert not [m for m in list_manifests() if m.blocked and (m.reads or m.writes)] or all(
        get_read_tool(p.name) is None and get_tool(ext.gate_name(p)) is None
        for m in list_manifests()
        if m.blocked
        for p in m.tools
    )


def test_schema_hash_is_key_order_independent_and_value_sensitive():
    a = {"type": "object", "properties": {"x": {"type": "string"}}}
    b = {"properties": {"x": {"type": "string"}}, "type": "object"}
    assert schema_hash(a) == schema_hash(b)
    assert schema_hash(a) != schema_hash({**a, "required": ["x"]})


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Bad-Name"},
        {"version": 0},
        {"data_class": "secret"},
        {"workspace_types": frozenset({"company"}), "data_class": "personal"},
        {"read_channels": frozenset({"telegram"})},
        {"url_env": ""},
        {"sidecar_image": "ghcr.io/x/y:latest"},
    ],
)
def test_invalid_manifests_fail_at_boot(change):
    import dataclasses

    manifest = dataclasses.replace(fakes.make_manifest(), **change)
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_personal_manifest_reads_are_web_only():
    import dataclasses

    manifest = dataclasses.replace(
        fakes.make_manifest(data_class="personal"), read_channels=frozenset({"web", "cli"})
    )
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_a_bad_pin_fails_at_boot():
    import dataclasses

    manifest = fakes.make_manifest()
    policy = dataclasses.replace(manifest.tools[0], schema_sha256="abc")
    with pytest.raises(ValueError):
        validate_manifest(dataclasses.replace(manifest, tools=(policy,)))


def test_duplicate_registration_is_refused():
    with pytest.raises(ValueError):
        register_manifest(get_manifest("external_installation"))
