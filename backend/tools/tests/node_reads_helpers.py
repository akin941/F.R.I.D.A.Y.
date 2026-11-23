"""The owner's world for the Friday Node read tool tests (Phase 9 brief
branch 5), shared by `tools/tests/test_node_reads.py` and the chat-turn e2e
`config/tests/test_node_read_turns.py`.

`make_world()` builds: the installation owner with a personal workspace and
two firm workspaces (`Acme`, `Secret`), the 8A hooks path rules
(`~/work` -> Acme, `~/code` -> personal, and the nested
`~/work/acme-secret` -> Secret), a firm member, and a paired node.

`ReadLink` connects the node in process: a `nodes.gateway.NodeSession`
(protocol v1, the real gateway read path: placement, per-socket in-flight
set, result validation, the Redis result key) and the Python reference node
over the fixture home (both directory grants on, the readers' clock fixed at
the fixture time). `install(monkeypatch)` wraps `nodes.bus.wait_result`
so that, while `invoke_read` waits, the session forwards the inbox to the
node and the node's answer back -- every envelope the node received is kept
in `received`. Nothing leaves the process but the test Redis.

`HostileReaders` makes the node lie: it can ignore `cwd_prefixes` (rows of
every workspace come back) or rewrite an output (a different session, an
inflated aggregate). The server must not trust either.
"""

import dataclasses
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource, WorkspaceMappingRule
from nodes import bus
from nodes.conformance.cases import NOW as FIXTURE_NOW
from nodes.gateway import NodeSession
from nodes.keys import signing_keyring
from nodes.reference.confirm import FakeClock
from nodes.reference.executor import FakeExecutor
from nodes.reference.fixtures.factory import fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.readers import Readers
from nodes.services import pair_node
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"


def _user(email, *, is_owner=False):
    user = register_user(email=email, password=PASSWORD, name=email.split("@")[0])
    if is_owner:
        user.is_owner = True
        user.save()
    return user


def _rule(owner, hooks, pattern, target, personal):
    return WorkspaceMappingRule.objects.create(
        workspace=personal,
        source=hooks,
        rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
        pattern=pattern,
        target_workspace=target,
        created_by=owner,
    )


def _firm(owner, name):
    firm = Workspace.objects.create(name=name, type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    return firm


def make_world(*, nested=True):
    owner = _user("owner@test.dev", is_owner=True)
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    firm = _firm(owner, "Acme")
    secret = _firm(owner, "Secret")
    member = _user("member@test.dev")
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    raw_code, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    node = pair_node(raw_code=raw_code, node_name="MacBook", protocol_versions=[1]).node
    ensure_default_sources(workspace=personal, created_by=owner)
    hooks = IngestionSource.objects.get(
        workspace=personal, kind=IngestionSource.Kind.CLAUDE_CODE_HOOKS
    )
    rules = {
        "~/work": _rule(owner, hooks, "~/work", firm, personal),
        "~/code": _rule(owner, hooks, "~/code", personal, personal),
    }
    if nested:
        rules["~/work/acme-secret"] = _rule(owner, hooks, "~/work/acme-secret", secret, personal)
    return SimpleNamespace(
        owner=owner,
        personal=personal,
        firm=firm,
        secret=secret,
        member=member,
        node=node,
        hooks=hooks,
        rules=rules,
        keyring=signing_keyring(),
    )


class HostileReaders:
    """The reference readers with the readers' clock fixed at the fixture
    time, optionally lying (see the module docstring)."""

    def __init__(self, readers, *, ignore_prefixes=False, rewrite=None):
        self.readers = readers
        self.ignore_prefixes = ignore_prefixes
        self.rewrite = rewrite or {}

    @property
    def calls(self):
        return self.readers.calls

    def availability(self):
        return self.readers.availability()

    def read(self, capability, args, *, now):
        if self.ignore_prefixes:
            args = {k: v for k, v in args.items() if k != "cwd_prefixes"}
        outcome = self.readers.read(capability, args, now=FIXTURE_NOW)
        rewrite = self.rewrite.get(capability)
        if rewrite is not None and outcome.status == "ok":
            outcome = dataclasses.replace(outcome, output=rewrite(outcome.output))
        return outcome


@dataclass
class ReadLink:
    session: NodeSession
    node: ReferenceNode
    readers: HostileReaders
    received: list = field(default_factory=list)
    answered: list = field(default_factory=list)

    def pump(self):
        for envelope in self.session.pull_invokes():
            self.received.append(envelope)
            for frame in self.node.receive(json.dumps(envelope)):
                self.answered.append(frame)
                self.session.receive(frame)

    def install(self, monkeypatch):
        real = bus.wait_result

        def wait_result(invocation_id, timeout_seconds):
            self.pump()
            return real(invocation_id, timeout_seconds)

        monkeypatch.setattr(bus, "wait_result", wait_result)
        return self

    def go_offline(self):
        self.session.disconnect()
        bus.clear_presence(self.session.node_id)


def connect(world, home, *, grants=None, window_titles=False, **hostile):
    """A v1 session for the world's node: `hello` records the snapshot and
    marks the node present, exactly as the gateway does."""
    keyring = world.keyring
    clock = FakeClock(int(time.time()))
    readers = HostileReaders(
        Readers(
            home=home,
            policy=fixture_policy(home),
            grants={"claude": True, "codex": True, **(grants or {})},
            window_titles=window_titles,
        ),
        **hostile,
    )
    reference = ReferenceNode(
        node_id=str(world.node.pk),
        pinned={keyring.key_id: keyring.active.public_key_bytes},
        policy=fixture_policy(home),
        executor=FakeExecutor(clock),
        clock=clock,
        catalogue=default_catalogue(readers.availability()),
        readers=readers,
    )
    session = NodeSession(world.node, 1)
    session.hello(reference.connect(1)[0])
    return ReadLink(session=session, node=reference, readers=readers)


def cleanup(world):
    bus.clear_presence(world.node.pk)
    bus.clear_inbox(world.node.pk)
