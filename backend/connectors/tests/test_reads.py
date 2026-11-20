"""The read executor (brief D4): audited, bounded, scoped, channel-bound."""

import pytest

from audit.models import AuditLog
from connectors.reads import arguments_sha256
from connectors.tests import fakes
from core.channels import CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_TELEGRAM, CHANNEL_WEB
from tools.registry import ReadToolError, get_read_tool, list_read_tools

pytestmark = pytest.mark.django_db


def _read(workspace, owner, args, channel=CHANNEL_WEB, name="fake_search"):
    return get_read_tool(name).execute(workspace=workspace, actor=owner, args=args, channel=channel)


def test_a_read_is_audited_without_content(fake_manifest, workspace, owner, enable, server):
    binding = enable(fake_manifest, workspace)
    server.search_blocks = [fakes.TextContent(text="SECRET-RESULT-TEXT")]
    result = _read(workspace, owner, {"query": "invoices", "limit": 3})
    assert result.text == "SECRET-RESULT-TEXT"
    log = AuditLog.objects.get(action="connector.read")
    assert log.metadata["binding"] == str(binding.id)
    assert log.metadata["tool"] == "fake_search"
    assert log.metadata["outcome"] == "ok"
    assert log.metadata["channel"] == "web"
    assert log.metadata["size"] == len("SECRET-RESULT-TEXT")
    assert log.metadata["args_sha256"] == arguments_sha256({"query": "invoices", "limit": 3})
    assert "duration_ms" in log.metadata
    assert "SECRET-RESULT-TEXT" not in str(log.metadata)
    assert "invoices" not in str(log.metadata)


def test_result_is_bounded_and_flagged(fake_manifest, workspace, owner, enable, server, settings):
    settings.CONNECTOR_RESULT_MAX_CHARS = 50
    enable(fake_manifest, workspace)
    server.search_blocks = [fakes.TextContent(text="x" * 40)]
    server.search_structured = {"rows": ["y" * 40]}
    result = _read(workspace, owner, {"query": "q"})
    assert len(result.text) == 50
    assert result.truncated is True
    assert result.size > 50
    assert AuditLog.objects.get(action="connector.read").metadata["truncated"] is True


def test_images_are_dropped(fake_manifest, workspace, owner, enable, server):
    enable(fake_manifest, workspace)
    server.search_blocks = [fakes.IMAGE_BLOCK, fakes.TextContent(text="only text")]
    assert _read(workspace, owner, {"query": "q"}).text == "only text"


@pytest.mark.parametrize(
    "args, code",
    [
        ({"limit": 3}, "invalid_arguments"),
        ({"query": "other-firm"}, "out_of_scope"),
        ("x", "invalid_arguments"),
    ],
)
def test_invalid_or_out_of_scope_arguments_never_reach_the_server(
    fake_manifest, workspace, owner, enable, server, args, code
):
    enable(fake_manifest, workspace)
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, args)
    assert info.value.code == code
    assert server.calls == []
    assert AuditLog.objects.get(action="connector.read").metadata["outcome"] == code


@pytest.mark.parametrize("channel", [CHANNEL_TELEGRAM, CHANNEL_OPENAI])
def test_disallowed_channels_are_refused(fake_manifest, workspace, owner, enable, server, channel):
    enable(fake_manifest, workspace)
    assert list_read_tools(workspace=workspace, channel=channel) == []
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, {"query": "q"}, channel=channel)
    assert info.value.code == "channel_forbidden"
    assert server.calls == []


def test_personal_reads_are_web_only(personal_manifest, workspace, owner, enable, server):
    enable(personal_manifest, workspace)
    assert list_read_tools(workspace=workspace, channel=CHANNEL_CLI) == []
    assert [s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_WEB)] == [
        "mailish_search"
    ]


def test_other_workspaces_bindings_are_invisible(
    fake_manifest, workspace, firm, owner, enable, server
):
    enable(fake_manifest, firm)
    assert list_read_tools(workspace=workspace, channel=CHANNEL_WEB) == []
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, {"query": "q"})
    assert info.value.code == "not_available"
    assert server.calls == []


def test_a_tool_error_is_a_failed_read(fake_manifest, workspace, owner, enable, server):
    enable(fake_manifest, workspace)
    server.search_is_error = True
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, {"query": "q"})
    assert info.value.code == "tool_error"


def test_drift_detected_at_read_time_disables_the_tool(
    fake_manifest, workspace, owner, enable, server
):
    binding = enable(fake_manifest, workspace)
    server.schemas["fake_search"] = {"type": "object"}
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, {"query": "q"})
    assert info.value.code == "pin_mismatch"
    assert server.calls == []
    binding.refresh_from_db()
    assert binding.tool_status("fake_search") == "mismatch"
    assert binding.tool_status("fake_create_note") == "ok"
    assert AuditLog.objects.filter(action="connector.pin_mismatch").count() == 1
