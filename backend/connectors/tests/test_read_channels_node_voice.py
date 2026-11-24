"""Phase 11 branch 1 (brief D4, ADR-010 amendment 2026-11): personal and
firm reads also over `node_voice`.

- the pinned `READ_CHANNELS` sets;
- `connectors.manifest` still refuses a manifest whose `read_channels`
  exceed its class's set and accepts one that narrows itself without
  `node_voice`;
- `connectors.reads`: a personal read whose manifest opts into
  `node_voice` runs over it and is still refused over `cli`; a connector
  (or `ReadToolSpec`) that omits `node_voice` refuses it with
  `channel_forbidden`; `cli`, `hook`, `openai`, `telegram` and `node` stay
  refused for personal reads; the shipped manifests are unchanged."""

import dataclasses

import pytest

from connectors.manifest import validate_manifest
from connectors.manifests.google import MANIFEST as GOOGLE
from connectors.manifests.home_assistant import MANIFEST as HOME_ASSISTANT
from connectors.tests import fakes
from connectors.tests.conftest import _install, _uninstall
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_TELEGRAM,
    CHANNEL_WEB,
)
from tools.registry import (
    DATA_CLASS_FIRM,
    DATA_CLASS_PERSONAL,
    READ_CHANNELS,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
    get_read_tool,
    list_read_tools,
    register_read_tool,
    unregister_read_tool,
)

pytestmark = pytest.mark.django_db


def test_the_read_channel_sets_are_pinned():
    assert READ_CHANNELS == {
        DATA_CLASS_FIRM: frozenset({CHANNEL_WEB, CHANNEL_CLI, CHANNEL_NODE_VOICE}),
        DATA_CLASS_PERSONAL: frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE}),
    }
    for refused in (CHANNEL_CLI, CHANNEL_HOOK, CHANNEL_OPENAI, CHANNEL_TELEGRAM, CHANNEL_NODE):
        assert refused not in READ_CHANNELS[DATA_CLASS_PERSONAL]


def test_shipped_personal_manifests_do_not_opt_in_yet():
    # Opting a connector in is a manifest edit under the MCP governance
    # process (ADR-010 amendment 2026-11), not part of this branch.
    assert CHANNEL_NODE_VOICE not in GOOGLE.read_channels
    assert CHANNEL_NODE_VOICE not in HOME_ASSISTANT.read_channels


@pytest.mark.parametrize("data_class", ["personal", "firm"])
def test_a_manifest_may_opt_into_node_voice(data_class):
    manifest = dataclasses.replace(
        fakes.make_manifest(data_class=data_class),
        read_channels=frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE}),
    )
    validate_manifest(manifest)


@pytest.mark.parametrize("extra", [CHANNEL_CLI, CHANNEL_HOOK, CHANNEL_NODE, CHANNEL_OPENAI])
def test_a_personal_manifest_still_cannot_widen(extra):
    manifest = dataclasses.replace(
        fakes.make_manifest(data_class="personal"),
        read_channels=frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE, extra}),
    )
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_a_manifest_narrowed_without_node_voice_is_accepted():
    validate_manifest(fakes.make_manifest(data_class="personal"))


@pytest.fixture
def voice_personal_manifest(server):
    manifest = fakes.make_manifest(
        name="calendarish",
        data_class="personal",
        url_env=fakes.PERSONAL_URL_ENV,
        read_channels={CHANNEL_WEB, CHANNEL_NODE_VOICE},
    )
    _install(manifest)
    yield manifest
    _uninstall(manifest)


def _read(workspace, owner, channel, name):
    return get_read_tool(name).execute(
        workspace=workspace, actor=owner, args={"query": "bugün"}, channel=channel
    )


def test_a_personal_read_runs_over_node_voice_and_not_over_cli(
    voice_personal_manifest, workspace, owner, enable, server
):
    enable(voice_personal_manifest, workspace)
    server.search_blocks = [fakes.TextContent(text="10:00 toplantı")]
    names = [s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_NODE_VOICE)]
    assert names == ["calendarish_search"]
    assert _read(workspace, owner, CHANNEL_NODE_VOICE, "calendarish_search").text == (
        "10:00 toplantı"
    )
    for channel in (CHANNEL_CLI, CHANNEL_HOOK, CHANNEL_OPENAI, CHANNEL_TELEGRAM, CHANNEL_NODE):
        assert list_read_tools(workspace=workspace, channel=channel) == []
        with pytest.raises(ReadToolError) as info:
            _read(workspace, owner, channel, "calendarish_search")
        assert info.value.code == "channel_forbidden"


def test_a_connector_without_node_voice_refuses_it(personal_manifest, workspace, owner, enable):
    enable(personal_manifest, workspace)
    assert list_read_tools(workspace=workspace, channel=CHANNEL_NODE_VOICE) == []
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, CHANNEL_NODE_VOICE, "mailish_search")
    assert info.value.code == "channel_forbidden"


def test_a_firm_connector_without_node_voice_refuses_it(fake_manifest, workspace, owner, enable):
    enable(fake_manifest, workspace)
    with pytest.raises(ReadToolError) as info:
        _read(workspace, owner, CHANNEL_NODE_VOICE, "fake_search")
    assert info.value.code == "channel_forbidden"


def test_a_read_tool_spec_may_narrow_itself_below_the_set(workspace):
    spec = ReadToolSpec(
        name="narrow_personal_read",
        description="A personal read that stays on the web.",
        argument_serializer=None,
        data_class=DATA_CLASS_PERSONAL,
        channels=frozenset({CHANNEL_WEB}),
        execute=lambda **kwargs: ReadResult(text="x"),
        is_available=lambda workspace: True,
    )
    register_read_tool(spec)
    try:
        voice_names = {
            s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_NODE_VOICE)
        }
        web_names = {s.name for s in list_read_tools(workspace=workspace, channel=CHANNEL_WEB)}
        assert "narrow_personal_read" in web_names
        assert "narrow_personal_read" not in voice_names
        wide = dataclasses.replace(
            spec,
            name="wide_personal_read",
            channels=frozenset({CHANNEL_WEB, CHANNEL_NODE_VOICE, CHANNEL_CLI}),
        )
        with pytest.raises(ValueError):
            register_read_tool(wide)
    finally:
        unregister_read_tool("narrow_personal_read")
