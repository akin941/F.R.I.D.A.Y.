"""The Phase 13 two-node setup (brief "Test strategy": *Two-node fixtures*):
an owner with the room "Çalışma odası", a v2 "MacBook" (macOS, laptop,
every read and action contract invocable) and a v1 "Masaüstü" (Linux,
workstation, read contracts only), both rows created directly (no
pairing, no node process). Presence keys are the caller's to set (and to
clear: every created id is appended to `cleanup`)."""

from dataclasses import dataclass

from core.channels import CHANNEL_WEB
from core.speech import match_key
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.services import record_capability_snapshot
from nodes.tests.gateway_helpers import direct_node
from workspaces import rooms
from workspaces.models import Workspace


@dataclass
class TwoNodes:
    owner: object
    room: object
    macbook: object
    desktop: object

    @property
    def nodes(self):
        return (self.macbook, self.desktop)


def snapshot(node, *, protocol_version, reads_only=False):
    catalogue = [
        entry for entry in default_catalogue() if not reads_only or entry["class"] == "read"
    ]
    return record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=protocol_version,
    )


def labelled_node(owner, name, *, room=None, role="other", platform="macos", protocol_version=1):
    node, _ = direct_node(owner, name=name)
    node.name_key = match_key(name)
    node.room = room
    node.role = role
    node.platform = platform
    node.protocol_version = protocol_version
    node.save(update_fields=["name_key", "room", "role", "platform", "protocol_version"])
    return node


def make_two_nodes(owner, cleanup=None):
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    room = rooms.create_room(
        actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Çalışma odası"
    )
    macbook = labelled_node(
        owner, "MacBook", room=room, role="laptop", platform="macos", protocol_version=2
    )
    snapshot(macbook, protocol_version=2)
    desktop = labelled_node(
        owner, "Masaüstü", room=room, role="workstation", platform="linux", protocol_version=1
    )
    snapshot(desktop, protocol_version=1, reads_only=True)
    if cleanup is not None:
        cleanup.extend([macbook.pk, desktop.pk])
    return TwoNodes(owner=owner, room=room, macbook=macbook, desktop=desktop)
