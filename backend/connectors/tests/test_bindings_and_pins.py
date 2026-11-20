"""Bindings and pins (brief D3): disabled by default, audited toggles,
blocked/personal manifests unbindable where they must be, schema drift ->
disabled + audit, sticky until a new manifest version."""

import pytest

from accounts.services import register_user
from audit.models import AuditLog
from connectors.models import ConnectorBinding
from connectors.services import (
    ConnectorInvalidTransition,
    ConnectorPermissionDenied,
    check_binding_pins,
    set_binding_enabled,
)
from connectors.tasks import check_connector_pins
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from tools.registry import list_read_tools
from workspaces.models import WorkspaceMember

pytestmark = pytest.mark.django_db


def test_a_binding_is_disabled_by_default_and_nothing_is_offered(fake_manifest, workspace):
    binding = ConnectorBinding.objects.create(workspace=workspace, connector=fake_manifest.name)
    assert binding.enabled is False
    assert list_read_tools(workspace=workspace, channel=CHANNEL_WEB) == []


def test_enable_runs_the_pin_check_and_audits(fake_manifest, workspace, owner, server, committed):
    with committed():
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="fake", enabled=True, channel=CHANNEL_WEB
        )
    binding = ConnectorBinding.objects.get(workspace=workspace, connector="fake")
    assert binding.enabled
    assert binding.last_health["tools"] == {"fake_search": "ok", "fake_create_note": "ok"}
    assert binding.last_health["protocol_version"] == "2026-07-28"
    assert binding.last_health["server_name"] == "fake-mcp"
    log = AuditLog.objects.get(action="connector.binding.enabled")
    assert log.metadata == {"connector": "fake", "manifest_version": 1, "channel": "web"}
    # The unlisted server tool is nowhere: not in health, not offered.
    assert "fake_merge" not in str(binding.last_health)
    assert [s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_WEB)] == [
        "fake_search"
    ]
    # Idempotent: a second enable writes no second audit row.
    set_binding_enabled(
        workspace=workspace, actor=owner, connector="fake", enabled=True, channel=CHANNEL_WEB
    )
    assert AuditLog.objects.filter(action="connector.binding.enabled").count() == 1


def test_a_server_that_is_down_leaves_every_tool_unchecked(
    fake_manifest, workspace, owner, monkeypatch, committed
):
    monkeypatch.delenv(fakes.URL_ENV)
    with committed():
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="fake", enabled=True, channel=CHANNEL_WEB
        )
    binding = ConnectorBinding.objects.get(workspace=workspace, connector="fake")
    assert binding.last_health["error"] == "not_configured"
    assert set(binding.last_health["tools"].values()) == {"unchecked"}
    assert list_read_tools(workspace=workspace, channel=CHANNEL_WEB) == []


def test_schema_drift_disables_the_tool_and_is_audited(fake_manifest, workspace, enable, server):
    binding = enable(fake_manifest, workspace)
    server.schemas["fake_search"]["properties"]["limit"]["type"] = "string"
    check_binding_pins(binding.id)
    binding.refresh_from_db()
    assert binding.tool_status("fake_search") == "mismatch"
    assert binding.tool_status("fake_create_note") == "ok"
    log = AuditLog.objects.get(action="connector.pin_mismatch")
    assert log.metadata["tool"] == "fake_search"
    assert log.metadata["expected_sha256"] == fake_manifest.get_tool("fake_search").schema_sha256
    assert log.metadata["actual_sha256"] != log.metadata["expected_sha256"]
    assert list_read_tools(workspace=workspace, channel=CHANNEL_WEB) == []


def test_a_disabled_tool_stays_disabled_until_the_manifest_changes(
    fake_manifest, workspace, owner, enable, server, committed
):
    binding = enable(fake_manifest, workspace)
    original = server.schemas["fake_search"]
    server.schemas["fake_search"] = {"type": "object", "properties": {}}
    check_binding_pins(binding.id)
    # The server flips back, and the owner toggles the binding off and on:
    # still disabled -- only a reviewed manifest change clears it.
    server.schemas["fake_search"] = original
    check_binding_pins(binding.id)
    for enabled in (False, True):
        with committed():
            set_binding_enabled(
                workspace=workspace,
                actor=owner,
                connector="fake",
                enabled=enabled,
                channel=CHANNEL_WEB,
            )
    binding.refresh_from_db()
    assert binding.tool_status("fake_search") == "mismatch"
    assert AuditLog.objects.filter(action="connector.pin_mismatch").count() == 1


def test_the_pin_beat_checks_every_enabled_binding(fake_manifest, workspace, enable, server):
    enable(fake_manifest, workspace)
    del server.schemas["fake_create_note"]
    assert check_connector_pins() == 1
    binding = ConnectorBinding.objects.get(workspace=workspace)
    assert binding.tool_status("fake_create_note") == "missing"


def test_blocked_manifest_can_never_bind(owner, firm):
    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=firm,
            actor=owner,
            connector="external_installation",
            enabled=True,
            channel=CHANNEL_WEB,
        )
    assert not ConnectorBinding.objects.filter(connector="external_installation").exists()


def test_personal_manifest_cannot_bind_to_a_firm(personal_manifest, owner, firm):
    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=firm, actor=owner, connector="mailish", enabled=True, channel=CHANNEL_WEB
        )


def test_unknown_connector_is_refused(owner, workspace):
    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="nope", enabled=True, channel=CHANNEL_WEB
        )


def test_only_web_and_only_curators(fake_manifest, owner, workspace):
    with pytest.raises(ConnectorPermissionDenied):
        set_binding_enabled(
            workspace=workspace, actor=owner, connector="fake", enabled=True, channel=CHANNEL_CLI
        )
    member = register_user(email="member@test.dev", password="Str0ngDevPass!42")
    WorkspaceMember.objects.create(
        workspace=workspace, user=member, role=WorkspaceMember.Role.MEMBER
    )
    with pytest.raises(ConnectorPermissionDenied):
        set_binding_enabled(
            workspace=workspace, actor=member, connector="fake", enabled=True, channel=CHANNEL_WEB
        )
    assert not ConnectorBinding.objects.exists()
