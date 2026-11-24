"""Phase 12 D11: the async-read half of `tools.registry` -- spec validation,
the guarded `start_async_read` door, `fetch_async_read` and the listener
fan-out. `tools` imports neither side; these use plain callables."""

import pytest

from audit.unattended import recorded_unattended_run
from core.unattended import UnattendedDenied
from tools import registry
from tools.registry import (
    ASYNC_FAILED,
    ASYNC_OK,
    AsyncReadHandle,
    AsyncReadOutcome,
    ReadResult,
    ReadToolError,
    ReadToolSpec,
)


def _spec(**change):
    fields = {
        "name": "test_async_read",
        "description": "A test read.",
        "argument_serializer": None,
        "data_class": "personal",
        "channels": frozenset({"web"}),
        "execute": lambda **kw: ReadResult(text="sync"),
        "is_available": lambda workspace: True,
        "mode": "async",
        "start_async": lambda **kw: AsyncReadHandle(id="h1", tool="test_async_read"),
        "fetch_async": lambda handle: AsyncReadOutcome(
            status=ASYNC_OK, result=ReadResult(text="done")
        ),
        "label": "Test okuması",
    }
    fields.update(change)
    return ReadToolSpec(**fields)


@pytest.fixture
def registered():
    spec = _spec()
    registry.register_read_tool(spec)
    yield spec
    registry.unregister_read_tool(spec.name)


@pytest.mark.parametrize(
    "change", [{"mode": "later"}, {"start_async": None}, {"fetch_async": None}]
)
def test_an_invalid_async_spec_is_refused(change):
    with pytest.raises(ValueError):
        registry.register_read_tool(_spec(**change))
    assert registry.get_read_tool("test_async_read") is None


def test_start_and_fetch_go_through_the_spec(registered):
    handle = registry.start_async_read(
        registered, workspace=None, actor=None, args={}, channel="web"
    )
    assert handle == AsyncReadHandle(id="h1", tool="test_async_read")
    outcome = registry.fetch_async_read(handle)
    assert outcome.status == ASYNC_OK and outcome.terminal
    assert outcome.result.text == "done"
    assert registered.display_label == "Test okuması"


def test_a_sync_spec_cannot_be_started_async():
    spec = _spec(mode="sync", start_async=None, fetch_async=None)
    with pytest.raises(ReadToolError) as info:
        registry.start_async_read(spec, workspace=None, actor=None, args={}, channel="web")
    assert info.value.code == "not_available"


def test_fetch_of_an_unregistered_tool_is_failed():
    outcome = registry.fetch_async_read(AsyncReadHandle(id="x", tool="no_such_tool"))
    assert (outcome.status, outcome.error_code) == (ASYNC_FAILED, "not_available")


@pytest.mark.django_db
def test_start_is_denied_inside_an_unattended_run_before_the_spec(registered):
    started = []
    spec = _spec(start_async=lambda **kw: started.append(kw))
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-r"):
            registry.start_async_read(spec, workspace=None, actor=None, args={}, channel="web")
    assert started == []


def test_listeners_are_notified_and_one_failure_does_not_block_another():
    seen = []

    def broken(handle):
        raise RuntimeError("boom")

    def good(handle):
        seen.append(handle)

    registry.register_async_read_listener(broken)
    registry.register_async_read_listener(good)
    registry.register_async_read_listener(good)  # idempotent
    try:
        handle = AsyncReadHandle(id="h2", tool="t")
        registry.notify_async_read(handle)
        assert seen == [handle]
    finally:
        registry.unregister_async_read_listener(broken)
        registry.unregister_async_read_listener(good)
