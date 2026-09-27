"""Phase 5 branch 1 (ADR-010, brief D4): the read-tool registry the chat
router will discover connector reads through. Registration validates the
data class and channel set (fail closed: `personal` reads web only, nothing
ever over Telegram or the facade); listing filters by channel and by the
workspace's binding availability, both required."""

from types import SimpleNamespace

import pytest

from core.channels import (
    CHANNEL_CLI,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from tools.registry import (
    DATA_CLASS_FIRM,
    DATA_CLASS_PERSONAL,
    ReadResult,
    ReadToolSpec,
    ToolSpec,
    get_read_tool,
    get_tool,
    list_read_tools,
    list_tool_names,
    register_read_tool,
    register_tool,
    unregister_read_tool,
)

FIRM_WS = SimpleNamespace(id="firm-a")
OTHER_WS = SimpleNamespace(id="firm-b")


def _read(name="stub_read", data_class=DATA_CLASS_FIRM, channels=None, bound=(FIRM_WS,)):
    bound_ids = {workspace.id for workspace in bound}
    return ReadToolSpec(
        name=name,
        description="Jarvis-authored description",
        argument_serializer=object,
        data_class=data_class,
        channels=frozenset(channels if channels is not None else {CHANNEL_WEB}),
        execute=lambda *, workspace, actor, args, channel: ReadResult(text="ok"),
        is_available=lambda workspace: workspace.id in bound_ids,
    )


@pytest.fixture
def registered(monkeypatch):
    """Registers into an empty read registry: the committed manifests' own
    reads (e.g. `ha_states`) query real bindings and are not what these
    unit tests (with stand-in workspaces) are about."""
    from tools import registry

    monkeypatch.setattr(registry, "_READ_REGISTRY", {})
    names = []

    def register(spec):
        register_read_tool(spec)
        names.append(spec.name)
        return spec

    yield register
    for name in names:
        unregister_read_tool(name)


class TestRegistration:
    def test_register_then_get(self, registered):
        spec = registered(_read())
        assert get_read_tool("stub_read") is spec

    def test_unknown_read_tool_is_none(self):
        assert get_read_tool("no_such_read") is None

    def test_unregister_is_idempotent(self):
        unregister_read_tool("never_registered")

    def test_unknown_data_class_is_refused(self):
        with pytest.raises(ValueError, match="data class"):
            register_read_tool(_read(data_class="public"))

    def test_empty_channel_set_is_refused(self):
        with pytest.raises(ValueError, match="at least one channel"):
            register_read_tool(_read(channels=set()))

    @pytest.mark.parametrize(
        "channel", [CHANNEL_TELEGRAM, CHANNEL_OPENAI, CHANNEL_SYSTEM, CHANNEL_UNKNOWN]
    )
    def test_firm_reads_never_widen_past_web_and_cli(self, channel):
        with pytest.raises(ValueError, match="not allowed"):
            register_read_tool(_read(channels={CHANNEL_WEB, channel}))
        assert get_read_tool("stub_read") is None

    def test_firm_reads_may_use_web_and_cli(self, registered):
        spec = registered(_read(channels={CHANNEL_WEB, CHANNEL_CLI}))
        assert spec.channels == {CHANNEL_WEB, CHANNEL_CLI}

    def test_personal_reads_are_web_only(self, registered):
        with pytest.raises(ValueError, match="not allowed"):
            register_read_tool(_read(data_class=DATA_CLASS_PERSONAL, channels={CHANNEL_CLI}))
        registered(_read(data_class=DATA_CLASS_PERSONAL, channels={CHANNEL_WEB}))

    def test_a_read_cannot_shadow_a_gated_tool(self):
        with pytest.raises(ValueError, match="gated tool"):
            register_read_tool(_read(name="vault_export"))
        assert get_tool("vault_export") is not None

    def test_a_gated_tool_cannot_shadow_a_read(self, registered):
        registered(_read(name="shared_name"))
        with pytest.raises(ValueError, match="read tool"):
            register_tool(
                ToolSpec(
                    name="shared_name",
                    gated=True,
                    create_execution=lambda approval_request: None,
                    dispatch=lambda execution_id: None,
                )
            )
        assert get_tool("shared_name") is None

    def test_reads_never_enter_the_gated_tool_vocabulary(self, registered):
        registered(_read())
        assert "stub_read" not in list_tool_names()


class TestListReadTools:
    def test_filters_by_channel(self, registered):
        registered(_read(name="web_only", channels={CHANNEL_WEB}))
        registered(_read(name="web_and_cli", channels={CHANNEL_WEB, CHANNEL_CLI}))
        web = [spec.name for spec in list_read_tools(workspace=FIRM_WS, channel=CHANNEL_WEB)]
        cli = [spec.name for spec in list_read_tools(workspace=FIRM_WS, channel=CHANNEL_CLI)]
        assert web == ["web_and_cli", "web_only"]
        assert cli == ["web_and_cli"]

    @pytest.mark.parametrize("channel", [CHANNEL_TELEGRAM, CHANNEL_OPENAI, CHANNEL_UNKNOWN])
    def test_low_trust_relays_and_the_facade_see_nothing(self, registered, channel):
        registered(_read(channels={CHANNEL_WEB, CHANNEL_CLI}))
        assert list_read_tools(workspace=FIRM_WS, channel=channel) == []

    def test_filters_by_workspace_binding(self, registered):
        registered(_read(name="bound_to_a", bound=(FIRM_WS,)))
        registered(_read(name="bound_to_b", bound=(OTHER_WS,)))
        names = [spec.name for spec in list_read_tools(workspace=FIRM_WS, channel=CHANNEL_WEB)]
        assert names == ["bound_to_a"]

    def test_unbound_everywhere_lists_nothing(self, registered):
        registered(_read(bound=()))
        assert list_read_tools(workspace=FIRM_WS, channel=CHANNEL_WEB) == []

    def test_both_filters_are_required(self):
        with pytest.raises(TypeError):
            list_read_tools(channel=CHANNEL_WEB)
        with pytest.raises(TypeError):
            list_read_tools(workspace=FIRM_WS)


def test_read_result_carries_text_and_optional_structured_content():
    assert ReadResult(text="x").structured is None
    assert ReadResult(text="x", structured={"a": 1}).structured == {"a": 1}
