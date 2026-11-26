"""Phase 13 D5/D6: the node read tools on the "which PC" resolver, with two
reference nodes linked in process over the real gateway read path
(`tools.tests.node_reads_helpers`): "MacBook" and "Masaüstü" (Linux), both
online.

- the owner's words pick the node; the other node receives nothing;
- neither named nor recently active -> `node_ambiguous` with the ask
  template, zero `NodeInvocation` rows, the audit row naming both ids;
- a named offline node: the call goes to it only (its chain row says
  `node_offline`), never to the other node;
- the speaking node (`ToolCallContext.origin_node_id`) wins;
- the router's optional `node` argument is untrusted: naming another
  owner's node or contradicting the owner's words is refused, nothing sent;
- availability is "some usable online node offers a capability".
"""

from types import SimpleNamespace

import pytest

from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes.models import NodeInvocation
from nodes.reference.fixtures.factory import build_fixture_home
from nodes.services import pair_node, relabel_node
from nodes.tests.two_nodes import pairing_code
from tools import node_reads
from tools.owner_reads import AUDIT_ACTION
from tools.registry import ReadToolError, ToolCallContext, get_read_tool, run_read_tool
from tools.tests.node_reads_helpers import cleanup, connect, make_world

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("node-targeting-home"))


@pytest.fixture
def world(db):
    world = make_world()
    paired = pair_node(
        raw_code=pairing_code(world.owner),
        node_name="Masaüstü",
        protocol_versions=[1],
        platform="linux",
    )
    world.desk = relabel_node(
        actor=world.owner, node=paired.node, channel=CHANNEL_WEB, role="workstation"
    )
    world.mac = world.node
    yield world
    cleanup(world)
    cleanup(SimpleNamespace(node=world.desk))


@pytest.fixture
def links(world, home, monkeypatch):
    mac = connect(world, home).install(monkeypatch)
    desk = connect(SimpleNamespace(node=world.desk, keyring=world.keyring), home).install(
        monkeypatch
    )
    return SimpleNamespace(mac=mac, desk=desk)


def run(world, tool="node_sessions", text="", args=None, origin=None, workspace=None):
    return run_read_tool(
        get_read_tool(tool),
        workspace=workspace or world.firm,
        actor=world.owner,
        args=args or {},
        channel=CHANNEL_WEB,
        context=ToolCallContext(source_text=text, origin_node_id=origin, input_mode="text"),
    )


def rows_for(node):
    return NodeInvocation.objects.filter(node=node).count()


def last_audit():
    return AuditLog.objects.filter(action=AUDIT_ACTION).order_by("-created_at").first()


def test_the_node_reads_accept_the_context():
    for name in node_reads.TOOL_NAMES:
        assert get_read_tool(name).accepts_context


def test_the_owners_words_pick_the_node_and_the_other_receives_nothing(world, links):
    result = run(world, text="Masaüstündeki Claude oturumları ne durumda?")
    assert links.mac.received == []
    assert len(links.desk.received) == 2  # claude + codex lists
    assert rows_for(world.desk) == 2 and rows_for(world.mac) == 0
    assert result.text.endswith("node: Masaüstü")
    audit = last_audit()
    assert audit.metadata["node_id"] == str(world.desk.pk)
    assert audit.metadata["target_reason"] == "mention"


def test_unnamed_with_two_online_nodes_asks_and_calls_nobody(world, links):
    with pytest.raises(ReadToolError) as exc:
        run(world, text="Claude oturumlarım ne durumda?")
    assert exc.value.code == "node_ambiguous"
    assert exc.value.reply.startswith("Hangi bilgisayar: MacBook mu, Masaüstü mü?")
    assert NodeInvocation.objects.count() == 0
    assert links.mac.received == [] and links.desk.received == []
    audit = last_audit()
    assert audit.metadata["outcome"] == "node_ambiguous"
    assert set(audit.metadata["candidates"]) == {str(world.mac.pk), str(world.desk.pk)}


def test_a_named_offline_node_is_called_alone_and_records_why(world, links):
    links.desk.go_offline()
    result = run(world, text="Masaüstündeki oturumlar")
    assert links.mac.received == []
    assert [row.status for row in NodeInvocation.objects.filter(node=world.desk)] == [
        "node_offline",
        "node_offline",
    ]
    assert rows_for(world.mac) == 0
    assert "Mac'e şu an ulaşılamıyor" in result.text


def test_the_speaking_node_wins(world, links):
    run(world, text="oturumlarım", origin=str(world.desk.pk))
    assert links.mac.received == [] and rows_for(world.desk) == 2


def test_last_active_decides_when_nobody_is_named(world, links):
    from nodes.activity import mark_node_active

    mark_node_active(world.mac.pk)
    run(world, text="oturumlarım")
    assert links.desk.received == [] and rows_for(world.mac) == 2


def test_the_routers_node_argument_is_checked_not_trusted(world, links):
    with pytest.raises(ReadToolError) as exc:
        run(world, text="Masaüstündeki oturumlar", args={"node": "MacBook"})
    assert exc.value.code == "target_mismatch" and exc.value.reply
    with pytest.raises(ReadToolError) as exc:
        run(world, text="oturumlar", args={"node": "Theirs"})
    assert exc.value.code == "node_unknown"
    assert NodeInvocation.objects.count() == 0
    # Agreeing with the owner's words is fine.
    run(world, text="Masaüstündeki oturumlar", args={"node": "masaüstü"})
    assert rows_for(world.desk) == 2 and rows_for(world.mac) == 0


def test_a_router_node_naming_another_owners_node_is_refused(world, links):
    from tools.tests.conftest import foreign_node

    stranger = world.member
    theirs = foreign_node(stranger, name="Gizli")
    with pytest.raises(ReadToolError) as exc:
        run(world, text="oturumlar", args={"node": "Gizli"})
    assert exc.value.code == "node_unknown"
    assert NodeInvocation.objects.count() == 0
    assert theirs.pk not in {row.node_id for row in NodeInvocation.objects.all()}


def test_an_injected_mention_outside_the_owners_words_changes_nothing(world, links):
    """Only `source_text` counts: a fenced transcript or connector result
    that says "Masaüstünde çalıştır" is not the owner's message."""
    with pytest.raises(ReadToolError) as exc:
        run_read_tool(
            get_read_tool("node_sessions"),
            workspace=world.firm,
            actor=world.owner,
            args={"since_hours": 24},
            channel=CHANNEL_WEB,
            context=ToolCallContext(source_text="oturumlarım", input_mode="text"),
        )
    assert exc.value.code == "node_ambiguous"
    assert NodeInvocation.objects.count() == 0


def test_a_direct_call_without_context_uses_the_default_rules(world, links):
    links.desk.go_offline()
    get_read_tool("node_sessions").execute(
        workspace=world.firm, actor=world.owner, args={}, channel=CHANNEL_WEB
    )
    assert rows_for(world.mac) == 2 and rows_for(world.desk) == 0


def test_availability_is_any_usable_online_node(world, links):
    spec = get_read_tool("node_sessions")
    assert spec.is_available(world.firm)
    links.mac.go_offline()
    assert spec.is_available(world.firm)  # Masaüstü still serves
    links.desk.go_offline()
    assert not spec.is_available(world.firm)
