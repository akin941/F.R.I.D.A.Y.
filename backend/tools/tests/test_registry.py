from tools.registry import ToolSpec, get_tool, list_tool_names, register_tool, unregister_tool


def _spec(name="stub_tool", gated=True):
    return ToolSpec(
        name=name,
        gated=gated,
        create_execution=lambda approval_request: "exec-sentinel",
        dispatch=lambda execution_id: None,
    )


class TestRegistry:
    def test_unknown_tool_returns_none(self):
        assert get_tool("does_not_exist") is None

    def test_register_then_get_returns_the_same_spec(self):
        spec = _spec()
        register_tool(spec)
        try:
            assert get_tool("stub_tool") is spec
        finally:
            unregister_tool("stub_tool")

    def test_registering_the_same_name_twice_replaces_it(self):
        first = _spec()
        second = _spec()
        register_tool(first)
        try:
            register_tool(second)
            assert get_tool("stub_tool") is second
        finally:
            unregister_tool("stub_tool")

    def test_unregister_is_idempotent(self):
        unregister_tool("never_registered")  # must not raise


class TestListToolNames:
    def test_returns_registered_names_sorted(self):
        register_tool(_spec(name="zeta_tool"))
        register_tool(_spec(name="alpha_tool"))
        try:
            names = list_tool_names()
            assert "alpha_tool" in names
            assert "zeta_tool" in names
            assert names.index("alpha_tool") < names.index("zeta_tool")
        finally:
            unregister_tool("zeta_tool")
            unregister_tool("alpha_tool")

    def test_unregistered_name_is_absent(self):
        assert "definitely_not_registered" not in list_tool_names()
