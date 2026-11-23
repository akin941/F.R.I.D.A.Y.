"""Contract tests for the Claude Agent SDK driver pin (Phase 10 brief D10
"Pins and contract tests", branch 7): every fixture frame validates against
the bridge schema; every frame type the driver sends or handles exists in
it; every SDK name the bridge uses exists in the pinned surface snapshot
(and in the fake SDK the tests run it with); the pin equals the Dockerfile
ARG and the snapshot version; the surface generator yields a loadable,
non-provisional snapshot; and the selection system checks cover the Claude
driver. Plus the wrapper and image wiring, read statically."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from harness import drivers
from harness.checks import check_harness_drivers
from harness.drivers import claude_surface
from harness.drivers.claude_agent_sdk import ClaudeAgentSdkDriver
from harness.drivers.claude_surface import (
    BRIDGE_TO_HOST,
    HOST_TO_BRIDGE,
    SdkSurface,
    claude_pin_errors,
    load_bridge_schema,
    load_claude_surface,
)
from harness.tests.doubles.claude_agent_sdk import FIXTURES, load_bridge, make_fake_sdk

DOCKER_DIR = Path(claude_surface.__file__).resolve().parents[1] / "docker"
PIN = "0.1.0"
DIRECTIONS = {"h2b": HOST_TO_BRIDGE, "b2h": BRIDGE_TO_HOST}


@pytest.fixture
def schema():
    return load_bridge_schema()


@pytest.fixture
def surface(settings):
    return load_claude_surface(settings.HARNESS_CLAUDE_AGENT_SDK_VERSION)


@pytest.fixture
def bridge():
    return load_bridge(sdk=make_fake_sdk([]))


def _transcript():
    frames = []
    for line in (FIXTURES / "bridge_transcript.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            frames.append(json.loads(line))
    return frames


# --- the bridge protocol --------------------------------------------------------------


def test_every_fixture_frame_validates_against_the_bridge_schema(schema):
    frames = _transcript()
    assert frames
    problems = [
        frame for frame in frames if not schema.validate(DIRECTIONS[frame["dir"]], frame["msg"])
    ]
    assert problems == []


@pytest.mark.parametrize(
    ("direction", "frame"),
    [
        (BRIDGE_TO_HOST, {"type": "telemetry"}),
        (BRIDGE_TO_HOST, {"type": "permission_request", "request_id": "p"}),
        (BRIDGE_TO_HOST, {"type": "message", "turn_id": "t", "text": "x", "extra": 1}),
        (HOST_TO_BRIDGE, {"type": "permission_response", "request_id": "p", "decision": "always"}),
        (HOST_TO_BRIDGE, {"type": "ready"}),
        (HOST_TO_BRIDGE, "not a frame"),
    ],
)
def test_a_frame_outside_the_schema_is_caught(schema, direction, frame):
    assert schema.validate(direction, frame) is False


def test_every_frame_type_the_driver_uses_exists_in_the_schema(schema):
    assert set(ClaudeAgentSdkDriver.SENDS) == schema.types(HOST_TO_BRIDGE)
    assert set(ClaudeAgentSdkDriver.HANDLES) == schema.types(BRIDGE_TO_HOST)


def test_the_bridge_and_the_schema_agree_on_the_protocol(schema, bridge):
    assert bridge.PROTOCOL == schema.protocol == claude_surface.BRIDGE_PROTOCOL
    assert bridge.REPO_ROOT == drivers.base.SANDBOX_POLICY["claude_code"]["writable_roots"][0]
    assert bridge.PERMISSION_MODE == drivers.base.SANDBOX_POLICY["claude_code"]["permission_mode"]


def test_the_driver_never_sends_a_session_wide_decision(schema):
    decisions = schema.document[HOST_TO_BRIDGE]["permission_response"]["properties"]["decision"]
    assert decisions == {"enum": ["allow", "deny"]}


# --- the SDK surface ----------------------------------------------------------------


def test_every_sdk_name_the_bridge_uses_exists_in_the_snapshot(surface, bridge):
    assert surface.missing(bridge.SDK_SURFACE_USED) == []


def test_the_fake_sdk_provides_every_name_the_bridge_uses(bridge):
    sdk = make_fake_sdk([])
    for name, members in bridge.SDK_SURFACE_USED.items():
        cls = getattr(sdk, name)
        for method in members.get("methods", ()):
            assert hasattr(cls, method), f"{name}.{method}"
        fields = set(getattr(cls, "__dataclass_fields__", {}))
        for field_name in members.get("fields", ()):
            assert field_name in fields, f"{name}.{field_name}"


def test_a_name_missing_from_the_snapshot_is_reported(surface):
    used = {"ClaudeSDKClient": {"methods": ["query", "teleport"]}, "Nope": {}}
    assert surface.missing(used) == ["ClaudeSDKClient.teleport", "Nope"]


def test_committed_snapshot_is_marked_provisional(surface):
    assert surface.provisional is True
    assert surface.version == PIN


def test_pin_agrees_with_dockerfile_and_snapshot(settings):
    assert settings.HARNESS_CLAUDE_AGENT_SDK_VERSION == PIN
    assert claude_pin_errors() == []


def test_pin_mismatch_with_dockerfile_is_an_error(tmp_path):
    dockerfile = tmp_path / "claude_code.Dockerfile"
    dockerfile.write_text("ARG CLAUDE_AGENT_SDK_VERSION=0.2.0\n")
    errors = claude_pin_errors(pin=PIN, dockerfile=dockerfile)
    assert len(errors) == 1 and "0.2.0" in errors[0]


def test_dockerfile_without_the_arg_is_an_error(tmp_path):
    dockerfile = tmp_path / "claude_code.Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    assert claude_pin_errors(pin=PIN, dockerfile=dockerfile)


def test_pin_without_a_snapshot_is_an_error(tmp_path):
    dockerfile = tmp_path / "claude_code.Dockerfile"
    dockerfile.write_text("ARG CLAUDE_AGENT_SDK_VERSION=7.0.0\n")
    errors = claude_pin_errors(pin="7.0.0", dockerfile=dockerfile)
    assert errors and "No Claude Agent SDK surface snapshot" in errors[0]


def test_snapshot_version_mismatch_is_an_error(tmp_path, monkeypatch):
    root = tmp_path / "claude_sdk_surface" / "2.0.0"
    root.mkdir(parents=True)
    (root / "sdk_surface.json").write_text(
        '{"schema_format": "friday-claude-agent-sdk-surface/v1", "version": "1.0.0"}'
    )
    monkeypatch.setattr(claude_surface, "CLAUDE_SURFACE_ROOT", tmp_path / "claude_sdk_surface")
    load_claude_surface.cache_clear()
    try:
        dockerfile = tmp_path / "claude_code.Dockerfile"
        dockerfile.write_text("ARG CLAUDE_AGENT_SDK_VERSION=2.0.0\n")
        errors = claude_pin_errors(pin="2.0.0", dockerfile=dockerfile)
    finally:
        load_claude_surface.cache_clear()
    assert errors and "records version '1.0.0'" in errors[0]


def test_invalid_pin_is_refused():
    with pytest.raises(ValueError):
        load_claude_surface("../../etc")


def test_malformed_documents_are_refused():
    with pytest.raises(ValueError):
        SdkSurface({"schema_format": "other"})
    with pytest.raises(ValueError):
        claude_surface.BridgeSchema({"schema_format": "friday-agent-bridge-schema/v1"})


def test_surface_generator_builds_a_loadable_non_provisional_snapshot(monkeypatch, bridge):
    """`claude_sdk_surface.py` (run by generate_driver_schemas.sh inside the
    image) introspects the installed SDK; here it runs against the fake."""
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", make_fake_sdk([], version="0.3.1"))
    spec = importlib.util.spec_from_file_location(
        "claude_sdk_surface_under_test", DOCKER_DIR / "claude_sdk_surface.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = module.surface()
    loaded = SdkSurface(json.loads(json.dumps(document)))
    assert loaded.provisional is False
    assert loaded.version == "0.3.1"
    assert loaded.missing(bridge.SDK_SURFACE_USED) == []
    assert "receive_response" in loaded.classes["ClaudeSDKClient"]["methods"]
    assert "__aenter__" in loaded.classes["ClaudeSDKClient"]["methods"]


def test_surface_generator_imports_only_the_standard_library():
    import ast

    tree = ast.parse((DOCKER_DIR / "claude_sdk_surface.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names)


# --- the image and the wrapper ------------------------------------------------------


def test_the_image_installs_the_pinned_sdk_and_copies_the_bridge():
    text = (DOCKER_DIR / "claude_code.Dockerfile").read_text(encoding="utf-8")
    assert "ARG CLAUDE_AGENT_SDK_VERSION=" in text
    assert '"claude-agent-sdk==${CLAUDE_AGENT_SDK_VERSION}"' in text
    assert "COPY claude_agent_bridge.py /home/harness/claude_agent_bridge.py" in text


def test_the_wrapper_runs_the_bridge_only_in_driver_mode():
    script = (DOCKER_DIR / "claude_code_run.sh").read_text(encoding="utf-8")
    assert '"$driver" = "agent_sdk"' in script
    assert "/opt/friday-bridge/bin/python -I" in script
    assert '--test-command "$test_command" 2>/dev/null' in script
    assert "unknown driver" in script and "exit 64" in script
    # The one-shot path and read-only discovery are unchanged.
    assert 'claude -p "$goal" --output-format json --permission-mode acceptEdits' in script
    assert 'exec claude -p "$goal" --output-format json --permission-mode plan' in script


def test_the_schema_generator_knows_the_claude_target():
    script = (DOCKER_DIR / "generate_driver_schemas.sh").read_text(encoding="utf-8")
    assert "    claude)" in script
    assert "--network none" in script
    assert "claude_sdk_surface.py" in script


# --- selection and system checks --------------------------------------------------


def _ids():
    return sorted(message.id for message in check_harness_drivers())


def test_default_configuration_passes_the_checks(settings):
    assert settings.HARNESS_DRIVER_CLAUDE == "cli"
    assert _ids() == []
    assert drivers.selected_driver("claude_code") == "cli"


def test_agent_sdk_while_disabled_is_refused(settings):
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVERS_ENABLED = False
    assert _ids() == ["harness.E002"]
    assert drivers.selected_driver("claude_code") == "cli"


def test_agent_sdk_without_d4_is_refused(settings):
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = ""
    assert "harness.E003" in _ids()
    with pytest.raises(drivers.DriverConfigurationError):
        drivers.selected_driver("claude_code")


def test_agent_sdk_with_d4_warns_on_the_provisional_surface(settings):
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-20"
    assert _ids() == ["harness.W002"]
    assert drivers.selected_driver("claude_code") == "agent_sdk"


def test_a_codex_value_for_claude_is_refused(settings):
    settings.HARNESS_DRIVER_CLAUDE = "app_server"
    assert _ids() == ["harness.E005"]
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-20"
    with pytest.raises(drivers.DriverConfigurationError):
        drivers.selected_driver("claude_code")


def test_claude_pin_mismatch_is_a_boot_error(settings):
    settings.HARNESS_CLAUDE_AGENT_SDK_VERSION = "9.9.9"
    assert _ids() == ["harness.E004", "harness.E004"]  # no ARG match, no snapshot


def test_driver_status_reports_the_claude_driver(settings):
    rows = {row["agent"]: row for row in drivers.driver_status()}
    row = rows["claude_code"]
    assert (row["driver"], row["known"], row["enabled"]) == ("cli", True, False)
    assert row["pin"] == PIN
    assert row["provisional"] is True
    assert row["pin_errors"] == []
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVERS_ENABLED = True
    rows = {row["agent"]: row for row in drivers.driver_status()}
    assert rows["claude_code"]["enabled"] is True
    assert rows["codex_cli"]["enabled"] is False
