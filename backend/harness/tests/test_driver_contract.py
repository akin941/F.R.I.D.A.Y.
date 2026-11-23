"""Contract test per pin (Phase 10 brief D10 "Pins and contract tests"):
every fixture transcript frame validates against the pinned snapshot, every
method the driver sends or handles exists in it, and the pin equals the
Dockerfile ARG and the snapshot version. Plus the driver selection system
checks (harness.E002-E005, W002). No DB."""

from pathlib import Path

import pytest

from harness import drivers
from harness.checks import check_harness_drivers
from harness.drivers import schema
from harness.drivers.codex_app_server import CodexAppServerDriver
from harness.drivers.schema import (
    CLIENT_NOTIFICATIONS,
    CLIENT_REQUESTS,
    SERVER_NOTIFICATIONS,
    SERVER_REQUESTS,
    codex_pin_errors,
    dockerfile_arg,
    load_codex_snapshot,
)
from harness.tests.doubles.codex_app_server import FIXTURES, load_transcript

PIN = "0.1.0"
FIXTURE_NAMES = sorted(path.name for path in FIXTURES.glob("*.jsonl"))


@pytest.fixture
def snapshot(settings):
    return load_codex_snapshot(settings.HARNESS_CODEX_APP_SERVER_VERSION)


def _frame_problems(snapshot, frames):
    """Validates a transcript the way the protocol pairs its frames."""
    problems = []
    open_requests = {}  # client request id -> method
    open_server_requests = {}  # server request id -> method
    for number, frame in enumerate(frames, 1):
        msg, direction = frame["msg"], frame["dir"]
        method = msg.get("method")
        if direction == "c2s":
            if method and "id" in msg:
                ok = snapshot.has(CLIENT_REQUESTS, method) and snapshot.validate(
                    CLIENT_REQUESTS, method, "params", msg.get("params")
                )
                open_requests[msg["id"]] = method
            elif method:
                ok = snapshot.has(CLIENT_NOTIFICATIONS, method) and snapshot.validate(
                    CLIENT_NOTIFICATIONS, method, "params", msg.get("params")
                )
            else:
                answered = open_server_requests.pop(msg.get("id"), None)
                ok = answered is not None and snapshot.validate(
                    SERVER_REQUESTS, answered, "result", msg.get("result")
                )
        else:
            if method and "id" in msg:
                ok = snapshot.has(SERVER_REQUESTS, method) and snapshot.validate(
                    SERVER_REQUESTS, method, "params", msg.get("params")
                )
                open_server_requests[msg["id"]] = method
            elif method:
                ok = snapshot.has(SERVER_NOTIFICATIONS, method) and snapshot.validate(
                    SERVER_NOTIFICATIONS, method, "params", msg.get("params")
                )
            else:
                requested = open_requests.pop(msg.get("id"), None)
                ok = requested is not None and snapshot.validate(
                    CLIENT_REQUESTS, requested, "result", msg.get("result")
                )
        if not ok:
            problems.append(f"frame {number}: {msg!r:.200}")
    if open_server_requests:
        problems.append(f"unanswered server requests: {sorted(open_server_requests)}")
    return problems


def test_fixtures_exist():
    assert {"two_escalations.jsonl", "file_change_no_usage.jsonl"} <= set(FIXTURE_NAMES)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_frame_validates_against_the_snapshot(snapshot, name):
    assert _frame_problems(snapshot, load_transcript(name)) == []


def test_a_frame_outside_the_snapshot_is_caught(snapshot):
    frames = [{"dir": "s2c", "msg": {"method": "codex/unknown", "params": {}}}]
    assert _frame_problems(snapshot, frames)
    frames = [
        {
            "dir": "s2c",
            "msg": {
                "id": 1,
                "method": "item/commandExecution/requestApproval",
                "params": {"threadId": "t"},
            },
        },
        {"dir": "c2s", "msg": {"id": 1, "result": {"decision": "approved"}}},
    ]
    assert len(_frame_problems(snapshot, frames)) == 2


def test_every_method_the_driver_uses_exists_in_the_snapshot(snapshot):
    for method in CodexAppServerDriver.SENDS_REQUESTS:
        assert snapshot.has(CLIENT_REQUESTS, method), method
    for method in CodexAppServerDriver.SENDS_NOTIFICATIONS:
        assert snapshot.has(CLIENT_NOTIFICATIONS, method), method
    for method in CodexAppServerDriver.HANDLES_REQUESTS:
        assert snapshot.has(SERVER_REQUESTS, method), method
    for method in CodexAppServerDriver.HANDLES_NOTIFICATIONS:
        assert snapshot.has(SERVER_NOTIFICATIONS, method), method


def test_driver_answers_validate_and_accept_for_session_is_never_sent(snapshot):
    for method in CodexAppServerDriver.HANDLES_REQUESTS:
        assert snapshot.validate(SERVER_REQUESTS, method, "result", {"decision": "accept"})
        assert snapshot.validate(SERVER_REQUESTS, method, "result", {"decision": "decline"})
    source = Path(drivers.__file__).with_name("codex_app_server.py").read_text()
    assert "acceptForSession" not in source.split('"""', 2)[2]


def test_committed_snapshot_is_marked_provisional(snapshot):
    assert snapshot.version == PIN
    assert snapshot.provisional is True


def test_pin_agrees_with_dockerfile_and_snapshot(settings):
    assert settings.HARNESS_CODEX_APP_SERVER_VERSION == PIN
    assert dockerfile_arg(schema.CODEX_DOCKERFILE, "CODEX_CLI_VERSION") == PIN
    assert codex_pin_errors() == []


def test_pin_mismatch_with_dockerfile_is_an_error(tmp_path):
    dockerfile = tmp_path / "codex_cli.Dockerfile"
    dockerfile.write_text("FROM x\nARG CODEX_CLI_VERSION=9.9.9\n")
    errors = codex_pin_errors(pin=PIN, dockerfile=dockerfile)
    assert any("9.9.9" in error for error in errors)


def test_dockerfile_without_the_arg_is_an_error(tmp_path):
    dockerfile = tmp_path / "codex_cli.Dockerfile"
    dockerfile.write_text("FROM x\n")
    assert codex_pin_errors(pin=PIN, dockerfile=dockerfile)


def test_pin_without_a_snapshot_is_an_error(tmp_path):
    dockerfile = tmp_path / "codex_cli.Dockerfile"
    dockerfile.write_text("ARG CODEX_CLI_VERSION=7.0.0\n")
    errors = codex_pin_errors(pin="7.0.0", dockerfile=dockerfile)
    assert any("No Codex app-server schema snapshot" in error for error in errors)


def test_snapshot_version_mismatch_is_an_error(tmp_path, monkeypatch):
    root = tmp_path / "codex_schema" / "2.0.0"
    root.mkdir(parents=True)
    (root / "protocol.json").write_text(
        '{"schema_format": "friday-codex-app-server-subset/v1", "version": "1.0.0"}'
    )
    monkeypatch.setattr(schema, "CODEX_SCHEMA_ROOT", tmp_path / "codex_schema")
    schema.load_codex_snapshot.cache_clear()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("ARG CODEX_CLI_VERSION=2.0.0\n")
    try:
        errors = codex_pin_errors(pin="2.0.0", dockerfile=dockerfile)
    finally:
        schema.load_codex_snapshot.cache_clear()
    assert any("records version '1.0.0'" in error for error in errors)


def test_invalid_pin_is_refused():
    with pytest.raises(ValueError):
        load_codex_snapshot("../../etc")


def test_snapshot_converter_builds_a_loadable_non_provisional_snapshot(tmp_path):
    """`codex_schema_subset.py` (run by generate_driver_schemas.sh) turns a
    generated-schema layout into the snapshot format, stdlib only (`-I`,
    run from outside the project so it can import nothing of ours)."""
    import json
    import subprocess
    import sys

    generated = tmp_path / "generated"
    generated.mkdir()

    def union(entries):
        return {
            "oneOf": [
                {
                    "properties": {
                        "method": {"enum": [method]},
                        "params": {"$ref": f"#/definitions/{params}"},
                    }
                }
                for method, params in entries
            ],
            "definitions": {params: {"type": "object"} for _method, params in entries},
        }

    (generated / "ClientRequest.json").write_text(
        json.dumps(union([("initialize", "InitializeParams")]))
    )
    (generated / "InitializeResponse.json").write_text(json.dumps({"type": "object"}))
    (generated / "ClientNotification.json").write_text(
        json.dumps({"oneOf": [{"properties": {"method": {"const": "initialized"}}}]})
    )
    (generated / "ServerRequest.json").write_text(
        json.dumps(union([("item/commandExecution/requestApproval", "CmdParams")]))
    )
    (generated / "ServerNotification.json").write_text(
        json.dumps(union([("turn/completed", "TurnCompletedParams")]))
    )
    out = tmp_path / "out" / "protocol.json"
    script = Path(schema.__file__).resolve().parents[1] / "docker" / "codex_schema_subset.py"
    subprocess.run(
        [sys.executable, "-I", str(script), str(generated), "3.1.4", str(out)],
        check=True,
        cwd=tmp_path,
    )
    snapshot = schema.ProtocolSnapshot(json.loads(out.read_text()))
    assert snapshot.version == "3.1.4"
    assert snapshot.provisional is False
    assert snapshot.has(CLIENT_REQUESTS, "initialize")
    assert snapshot.validator(CLIENT_REQUESTS, "initialize", "result") is not None
    assert snapshot.has(SERVER_NOTIFICATIONS, "turn/completed")
    assert snapshot.validate(SERVER_REQUESTS, "item/commandExecution/requestApproval", "params", {})


def test_snapshot_converter_refuses_an_unknown_layout(tmp_path):
    import subprocess
    import sys

    script = Path(schema.__file__).resolve().parents[1] / "docker" / "codex_schema_subset.py"
    result = subprocess.run(
        [sys.executable, "-I", str(script), str(tmp_path), "1.0.0", str(tmp_path / "p.json")],
        capture_output=True,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert not (tmp_path / "p.json").exists()


# --- selection and system checks ------------------------------------------


def _ids(settings):
    return sorted(message.id for message in check_harness_drivers())


def test_default_configuration_passes_the_checks(settings):
    assert settings.HARNESS_DRIVERS_ENABLED is False
    assert settings.HARNESS_DRIVER_CODEX == "cli"
    assert _ids(settings) == []
    assert drivers.selected_driver("codex_cli") == "cli"


def test_non_cli_driver_while_disabled_is_refused(settings):
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVERS_ENABLED = False
    assert _ids(settings) == ["harness.E002"]
    # The worker runs no system checks: disabled means the CLI path runs.
    assert drivers.selected_driver("codex_cli") == "cli"


def test_enabled_driver_without_d4_is_refused(settings):
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = ""
    assert "harness.E003" in _ids(settings)
    with pytest.raises(drivers.DriverConfigurationError):
        drivers.selected_driver("codex_cli")


def test_enabled_driver_with_a_non_date_d4_is_refused(settings):
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "yes"
    assert "harness.E003" in _ids(settings)


def test_enabled_driver_with_d4_warns_on_a_provisional_snapshot(settings):
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-20"
    assert _ids(settings) == ["harness.W002"]
    assert drivers.selected_driver("codex_cli") == "app_server"


def test_unknown_driver_value_is_refused(settings):
    settings.HARNESS_DRIVER_CODEX = "turbo"
    assert _ids(settings) == ["harness.E005"]
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-20"
    with pytest.raises(drivers.DriverConfigurationError):
        drivers.selected_driver("codex_cli")


def test_pin_mismatch_is_a_boot_error(settings):
    settings.HARNESS_CODEX_APP_SERVER_VERSION = "9.9.9"
    assert "harness.E004" in _ids(settings)


def test_agents_without_a_driver_setting_use_the_cli(settings):
    settings.HARNESS_DRIVERS_ENABLED = True
    assert drivers.selected_driver("claude_code") == "cli"


def test_driver_status_reports_each_driver(settings):
    rows = {row["agent"]: row for row in drivers.driver_status()}
    assert set(rows) == {"codex_cli", "claude_code"}
    row = rows["codex_cli"]
    assert row["enabled"] is False
    assert row["provisional"] is True
    assert row["pin_errors"] == []
