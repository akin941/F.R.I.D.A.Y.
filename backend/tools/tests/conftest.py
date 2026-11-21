"""Shared fixtures of the Phase 10 node action tests (branch 3).

They live under `tools/tests`, not `nodes/tests`: these tests cross the
gate end to end (`approvals` -> `tools.node_actions` -> `nodes`), and the
`nodes` import contract forbids `nodes` (tests included) to reach
`approvals`, `tools` or `memory`.

`node_world` builds the owner's world: the installation owner with a
personal and a firm workspace, a paired node (v2 snapshot, present), the 8A
path rules (`~/work` -> firm, `~/code` -> personal), a firm member and a
firm curator (`admin`) who owns a second node, the server output scrubber
(the 8A scrubber, as `config.asgi_nodes` registers it) and the registered
trust-check stub. `link` connects a `nodes.gateway.NodeSession` in process
to a Python reference node v2 over a fixture home whose local policy maps
`~/work/acme` and `~/code/friday`.
"""

import json
import secrets
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from accounts import devices
from accounts.authentication import hash_token
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from memory.ingestion.scrubber import scrub_text
from memory.ingestion.services import ensure_default_sources, scrub_terms_for
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import action_rules, bus, output
from nodes.gateway import NodeSession
from nodes.keys import signing_keyring
from nodes.models import Node
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.confirm import ConfirmationWindowDouble, Decision, FakeClock
from nodes.reference.executor import FakeCommandScript, FakeExecutor
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.services import pair_node, record_capability_snapshot
from tools import node_action_ports
from tools.node_actions import _workspace_for_path
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"
REPO_ARGS = {"repo_path": "~/work/acme", "command_kind": "test"}


def server_scrubber(workspace):
    """What `config.asgi_nodes.workspace_scrubber` registers (tools tests
    may not import `config`; `config/tests/test_asgi_nodes.py` pins that
    the gateway registers exactly this)."""
    terms = tuple(scrub_terms_for(workspace)) if workspace is not None else ()
    return lambda text: scrub_text(text, terms=terms).text if text else ""


def _user(email, *, is_owner=False):
    user = register_user(email=email, password=PASSWORD, name=email.split("@")[0])
    if is_owner:
        user.is_owner = True
        user.save()
    return user


def personal_of(user):
    return Workspace.objects.get(owner=user, type=Workspace.Type.PERSONAL)


def foreign_node(user, name="Theirs"):
    """A node row owned by `user` (only the installation owner can pair
    through the service; this stands for another user's paired node)."""
    device = TrustedDevice.objects.create(
        user=user,
        name=name,
        kind=TrustedDevice.Kind.NODE,
        secret_hash=hash_token("jnd_" + secrets.token_urlsafe(24)),
        enrolled_via=TrustedDevice.EnrolledVia.values[0],
    )
    return Node.objects.create(
        id=device.pk,
        device=device,
        owner=user,
        workspace=personal_of(user),
        name=name,
        protocol_version=1,
        pinned_key_id=signing_keyring().key_id,
    )


def advertise(node, catalogue=None):
    """Record a v2 snapshot as the gateway does on `hello`."""
    catalogue = default_catalogue() if catalogue is None else catalogue
    return record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=2,
    )


@pytest.fixture
def node_world(db, settings):
    owner = _user("owner@test.dev", is_owner=True)
    personal = personal_of(owner)
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    member = _user("member@test.dev")
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    curator = _user("curator@test.dev")
    WorkspaceMember.objects.create(workspace=firm, user=curator, role=WorkspaceMember.Role.ADMIN)
    raw_code, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    paired = pair_node(raw_code=raw_code, node_name="MacBook", protocol_versions=[1])
    node = paired.node
    advertise(node)
    ensure_default_sources(workspace=personal, created_by=owner)
    hooks = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
    )
    rules = {}
    for pattern, target in (("~/work", firm), ("~/code", personal)):
        rules[pattern] = WorkspaceMappingRule.objects.create(
            workspace=personal,
            source=hooks,
            rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
            pattern=pattern,
            target_workspace=target,
            created_by=owner,
        )
    other_node = foreign_node(curator)
    action_rules.register_path_resolver(_workspace_for_path)
    output.register_scrubber(server_scrubber)
    node_action_ports.register_trust_check(node_action_ports.pending_trust_check)
    bus.mark_present(node.pk)
    world = SimpleNamespace(
        owner=owner,
        personal=personal,
        firm=firm,
        member=member,
        curator=curator,
        node=node,
        node_secret=paired.node_secret,
        other_node=other_node,
        rules=rules,
        keyring=signing_keyring(),
    )
    yield world
    for pk in (node.pk, other_node.pk):
        bus.clear_presence(pk)
        bus.clear_inbox(pk)
    output.reset_scrubber()
    node_action_ports.register_trust_check(node_action_ports.pending_trust_check)


@pytest.fixture(scope="session")
def node_home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("node-action-home"))


@dataclass
class Link:
    """A gateway session and a reference node, wired in process."""

    session: NodeSession
    node: ReferenceNode
    frames: list = field(default_factory=list)

    def connect(self):
        hello = self.node.connect(2)[0]
        self.session.hello(hello)
        return hello

    def deliver(self, envelope):
        """Hand one envelope straight to the node (a compromised server or
        a replay); the session does not see it."""
        return self._apply(self.node.receive(json.dumps(envelope)))

    def pump(self):
        """Forward the inbox to the node and the node's answers back."""
        for envelope in self.session.pull_invokes():
            self._apply(self.node.receive(json.dumps(envelope)))
        self._apply(self.node.tick())
        return self.frames

    def _apply(self, frames):
        for frame in frames:
            self.frames.append(frame)
            if frame["type"] == "heartbeat":
                self.session.heartbeat(frame)
            else:
                self.session.receive(frame)
        return frames


@pytest.fixture
def make_link(node_world, node_home):
    def make(*, decisions=(("approve", 0),), scripts=None, node_scrub=None, catalogue=None):
        clock = FakeClock(int(time.time()))
        window = ConfirmationWindowDouble(clock, [Decision(a, after) for a, after in decisions])
        executor = FakeExecutor(clock, scripts=list(scripts or []))
        keyring = node_world.keyring
        reference = ReferenceNode(
            node_id=str(node_world.node.pk),
            pinned={keyring.key_id: keyring.active.public_key_bytes},
            policy=fixture_policy(node_home),
            executor=executor,
            clock=clock,
            window=window,
            catalogue=catalogue,
            scrub=node_scrub,
        )
        link = Link(NodeSession(node_world.node, 2), reference)
        link.connect()
        return link

    return make


def script(*chunks, exit_code=0):
    return FakeCommandScript(chunks=[(s, d, 0) for s, d in chunks], exit_code=exit_code)
