"""The owner's Friday Node kill switch (Phase 9 D1) from the host shell.

    manage.py node_kill_switch on       # every node of the owner is cut off
    manage.py node_kill_switch off      # nodes reconnect, no re-pairing
    manage.py node_kill_switch status   # print the state

Engaging refuses every node authentication and node pairing of the owner
and tells the node gateway to close the owner's live node sockets.
Releasing restores the (non-revoked) nodes. Both are audited
(`accounts.node.kill_switch.engaged` / `.released`).
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.devices import (
    DeviceTrustError,
    engage_node_kill_switch,
    get_owner,
    node_kill_switch_state,
    release_node_kill_switch,
)
from accounts.models import NodeKillSwitch


class Command(BaseCommand):
    help = "Engage, release or show the owner's Friday Node kill switch."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["on", "off", "status"])

    def handle(self, *args, **options):
        owner = get_owner()
        if owner is None:
            raise CommandError("No owner account is configured.")
        action = options["action"]
        try:
            if action == "on":
                engage_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
            elif action == "off":
                release_node_kill_switch(owner=owner, via=NodeKillSwitch.Via.MANAGEMENT_COMMAND)
        except DeviceTrustError as exc:
            raise CommandError(str(exc)) from exc
        state = node_kill_switch_state(owner)
        if state["engaged"]:
            self.stdout.write(
                f"Node kill switch: ENGAGED since {state['engaged_at']:%Y-%m-%d %H:%M:%S %Z} "
                f"(via {state['engaged_via']}). Every node is refused."
            )
        else:
            self.stdout.write("Node kill switch: released. Paired nodes may connect.")
