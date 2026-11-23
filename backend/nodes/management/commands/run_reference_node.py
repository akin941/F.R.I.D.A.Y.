"""Pair the Python reference node and keep it connected to `node-gateway`
(Phase 9 closeout; DEBUG stacks only).

    manage.py issue_pairing_code --node --strong --plain    # -> CODE
    manage.py run_reference_node --code CODE [--gateway-host node-gateway]
                                 [--gateway-port 8002] [--seconds 0]

Until the Swift app exists (brief "Mac session (deferred)"), this is how the
dev stack gets a paired, connected node, so `manage.py preflight` can be
green with a node paired (the closeout's done criterion) and the chat's
node tools can be tried by hand. The node reads a generated fixture home
(fake Claude Code / Codex sessions and two small git repositories), never
the host's files, and its secret is never printed.

Refused unless `DEBUG` is on: a reference node paired on a production stack
would feed fixture rows into the owner's chats. Revoke it afterwards on
`/devices` (or `POST /api/v1/nodes/<id>/revoke`)."""

import tempfile
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts import devices
from nodes import services
from nodes.keys import SigningKeyUnavailable
from nodes.protocol.messages import SUBPROTOCOLS
from nodes.reference.executor import FakeExecutor
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.readers import Readers
from nodes.reference.transport import NodeClient


class _WallClock:
    def now(self) -> int:
        return int(time.time())


def build_node(result, home):
    """The reference node for a pairing result: it pins exactly the keys the
    pairing handed out and reads the fixture home with both directory
    grants on (window titles off)."""
    readers = Readers(
        home=home,
        policy=fixture_policy(home),
        grants={"claude": True, "codex": True},
        window_titles=False,
    )
    clock = _WallClock()
    keyring = result.keyring
    return ReferenceNode(
        node_id=str(result.node.pk),
        pinned={keyring.key_id: keyring.active.public_key_bytes},
        policy=fixture_policy(home),
        executor=FakeExecutor(clock),
        clock=clock,
        catalogue=default_catalogue(readers.availability()),
        readers=readers,
    )


class Command(BaseCommand):
    help = "DEBUG only: pair the Python reference node and keep it connected to node-gateway."

    def add_arguments(self, parser):
        parser.add_argument("--code", required=True, help="A node pairing code (--node).")
        parser.add_argument("--name", default="Reference node")
        parser.add_argument("--gateway-host", default="node-gateway")
        parser.add_argument("--gateway-port", type=int, default=8002)
        parser.add_argument(
            "--seconds",
            type=int,
            default=0,
            help="Stay connected this long (0 = until interrupted).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("run_reference_node runs only with DEBUG on (dev stacks).")
        try:
            result = services.pair_node(
                raw_code=options["code"],
                node_name=options["name"],
                protocol_versions=[1],
            )
        except SigningKeyUnavailable as exc:
            raise CommandError(f"the server signing key is unavailable ({exc.reason})") from exc
        except (devices.DeviceTrustError, services.UnsupportedProtocolVersion) as exc:
            raise CommandError(f"pairing refused ({type(exc).__name__})") from exc
        self.stdout.write(f"paired reference node {result.node.pk} ({options['name']})")
        home = build_fixture_home(tempfile.mkdtemp(prefix="friday-reference-node-"))
        client = NodeClient(
            build_node(result, home),
            options["gateway_host"],
            options["gateway_port"],
            offer=(SUBPROTOCOLS[1],),
            secret=result.node_secret,
        ).start()
        deadline = time.monotonic() + options["seconds"] if options["seconds"] else None
        try:
            while client.alive and (deadline is None or time.monotonic() < deadline):
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            client.stop()
        if client.rejected or client.error is not None:
            raise CommandError(
                "the gateway refused or dropped the reference node "
                f"({type(client.error).__name__ if client.error else 'rejected'})"
            )
        self.stdout.write("reference node disconnected")
