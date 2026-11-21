"""Workspace scoping of the node tables (CLAUDE.md rule 1, ADR-001; brief
follow-up 5): `Node`, `CapabilitySnapshot` and `NodeInvocation` all go
through `for_actor()`. A firm-only member never sees the owner's personal
rows, and the owner's personal scope never sees a firm row."""

import pytest

from accounts.models import User
from nodes.audit_chain import append
from nodes.capabilities import load_contracts
from nodes.models import CapabilitySnapshot, Node, NodeInvocation
from nodes.protocol import catalogue_sha256, sha256_hex
from workspaces.models import WorkspaceMember

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(owner, personal, firm, make_node):
    member = User.objects.create_user(email="member@test.dev")
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    node = make_node().node
    catalogue = [c.catalogue_entry() for c in load_contracts().values()]
    snapshot = CapabilitySnapshot.objects.create(
        workspace=personal, node=node, catalogue_sha256=catalogue_sha256(catalogue), entries=[]
    )
    common = {
        "node": node,
        "capability": "git.status",
        "capability_version": 1,
        "args_sha256": sha256_hex({}),
        "status": NodeInvocation.Status.NODE_OFFLINE,
        "channel": "web",
        "trace_id": "t",
        "actor": owner,
    }
    personal_row = append(workspace=personal, **common)
    firm_row = append(workspace=firm, **common)
    return member, node, snapshot, personal_row, firm_row


def test_the_node_and_its_snapshot_live_in_the_personal_workspace(setup, owner, personal, firm):
    member, node, snapshot, _, _ = setup
    assert list(Node.objects.for_actor(owner)) == [node]
    assert list(Node.objects.for_actor(owner, workspace=firm)) == []
    assert list(CapabilitySnapshot.objects.for_actor(owner, workspace=personal)) == [snapshot]
    assert not Node.objects.for_actor(member).exists()
    assert not CapabilitySnapshot.objects.for_actor(member).exists()


def test_invocations_are_isolated_by_workspace(setup, owner, personal, firm):
    member, _, _, personal_row, firm_row = setup
    assert set(NodeInvocation.objects.for_actor(owner)) == {personal_row, firm_row}
    assert list(NodeInvocation.objects.for_actor(owner, workspace=personal)) == [personal_row]
    assert list(NodeInvocation.objects.for_actor(owner, workspace=firm)) == [firm_row]
    # The firm-only member sees the firm's row, never the personal one.
    assert list(NodeInvocation.objects.for_actor(member)) == [firm_row]
    assert list(NodeInvocation.objects.for_actor(member, workspace=personal)) == []


def test_anonymous_sees_nothing(setup):
    for model in (Node, CapabilitySnapshot, NodeInvocation):
        assert not model.objects.for_actor(None).exists()
