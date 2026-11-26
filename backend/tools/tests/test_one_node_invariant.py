"""Phase 13 D7: exactly one node per invocation, no broadcast.

- Registry walk: every registered `ReadToolSpec` that reaches a node (the
  `friday-node` source) is called with two online reference nodes and a
  message naming one of them; every chain row of the call is that node's
  and the other node receives nothing. A read tool outside that source
  must not accept the "which PC" context (so a new node-reaching read tool
  is caught here: it must join the walk).
- Every gated `ToolSpec` whose payload names a node (`node_action`) takes
  exactly one string `node_id`: a list is refused before any row exists.
- The models carry one `node` FK and no list of nodes anywhere.
"""

from types import SimpleNamespace

import pytest
from django.db import models

from core.channels import CHANNEL_WEB
from nodes.models import Node, NodeActionExecution, NodeInvocation
from nodes.reference.fixtures.factory import build_fixture_home
from nodes.services import pair_node
from nodes.tests.two_nodes import pairing_code
from tools import node_actions, node_reads
from tools.registry import (
    _READ_REGISTRY,
    _REGISTRY,
    ToolCallContext,
    ToolValidationError,
    run_read_tool,
)
from tools.tests.node_reads_helpers import cleanup, connect, make_world

pytestmark = pytest.mark.django_db

# Per tool: (workspace attribute, arguments) that reach the node.
CALLS = {
    "node_sessions": ("personal", {}),
    "node_session_detail": ("personal", {"session_id": "cc-friday-1"}),
    "node_usage": ("personal", {}),
    "node_git_status": ("personal", {"repo_path": "~/code/friday"}),
    "node_desktop": ("personal", {}),
}


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("one-node-home"))


@pytest.fixture
def world(db):
    world = make_world()
    world.desk = pair_node(
        raw_code=pairing_code(world.owner),
        node_name="Masaüstü",
        protocol_versions=[1],
        platform="linux",
    ).node
    yield world
    cleanup(world)
    cleanup(SimpleNamespace(node=world.desk))


@pytest.fixture
def links(world, home, monkeypatch):
    mac = connect(world, home).install(monkeypatch)
    desk = connect(SimpleNamespace(node=world.desk, keyring=world.keyring), home).install(
        monkeypatch
    )
    return {"mac": mac, "desk": desk}


# `friday-node` reads answered from server-side rows only (no node call):
# `node_action_result` reads `NodeActionExecution.tail` (Phase 10 D12).
SERVER_SIDE_READS = {"node_action_result"}


def node_sourced_reads():
    return sorted(
        name for name, spec in _READ_REGISTRY.items() if spec.source == node_reads.SOURCE_LABEL
    )


def test_the_walk_covers_every_node_reaching_read_tool():
    assert sorted(CALLS) == sorted(node_reads.TOOL_NAMES)
    assert node_sourced_reads() == sorted(set(CALLS) | SERVER_SIDE_READS)
    for name, spec in _READ_REGISTRY.items():
        # Only the walked tools reach a node, and only they take the
        # "which PC" context.
        assert spec.accepts_context == (name in CALLS), name


@pytest.mark.parametrize("tool", sorted(CALLS))
@pytest.mark.parametrize(("named", "text"), [("desk", "Masaüstünde"), ("mac", "MacBook'ta")])
def test_every_node_read_calls_exactly_the_named_node(world, links, tool, named, text):
    workspace_attr, args = CALLS[tool]
    other = "mac" if named == "desk" else "desk"
    target = world.desk if named == "desk" else world.node
    run_read_tool(
        _READ_REGISTRY[tool],
        workspace=getattr(world, workspace_attr),
        actor=world.owner,
        args=args,
        channel=CHANNEL_WEB,
        context=ToolCallContext(source_text=f"{text} bak", input_mode="text"),
    )
    rows = list(NodeInvocation.objects.all())
    assert rows, tool
    assert {row.node_id for row in rows} == {target.pk}
    assert links[other].received == []
    assert links[named].received


def test_node_sessions_on_the_desktop_sends_nothing_to_the_macbook(world, links):
    run_read_tool(
        _READ_REGISTRY["node_sessions"],
        workspace=world.firm,
        actor=world.owner,
        args={},
        channel=CHANNEL_WEB,
        context=ToolCallContext(source_text="Masaüstünde oturumlar", input_mode="text"),
    )
    assert links["mac"].received == [] and links["mac"].answered == []
    assert NodeInvocation.objects.filter(node=world.node).count() == 0


def test_node_payload_tools_take_exactly_one_node_id(world):
    node_tools = [name for name in _REGISTRY if name == node_actions.TOOL_NAME]
    assert node_tools == [node_actions.TOOL_NAME]
    spec = _REGISTRY[node_actions.TOOL_NAME]
    for bad in ([str(world.node.pk), str(world.desk.pk)], {"a": 1}, None, 7):
        with pytest.raises(ToolValidationError):
            spec.validate_request(
                world.personal, {"node_id": bad, "capability": "repo.command.run", "args": {}}
            )


def test_no_model_holds_a_list_of_nodes():
    for model in (NodeInvocation, NodeActionExecution):
        fks = [f for f in model._meta.get_fields() if getattr(f, "related_model", None) is Node]
        assert [f.name for f in fks] == ["node"], model
        assert isinstance(fks[0], models.ForeignKey)
        assert not any(isinstance(f, models.ManyToManyField) for f in model._meta.get_fields())
