"""The shared multi-node world of Phase 13 (brief "Test strategy":
"two-node fixtures"), used by the resolver tests here, the tools tests of
branch 2 (`tools/tests/test_node_targeting_*`, `test_one_node_invariant`)
and later the HUD presence tests.

`make_two_nodes(owner, personal)` builds, for an installation owner:
- rooms "Çalışma odası" (sort 0) and "Salon" (sort 1);
- "MacBook": a v2 snapshot (read + action capabilities), macOS, `laptop`,
  in the study, alias "iş bilgisayarı";
- "Masaüstü": a v1 snapshot (read capabilities only; the v2 action
  contracts are not invocable on a v1 node), Linux, `workstation`, in the
  study;
- with `third=True`, "Medya": v1, Linux, `media`, in the salon;
- every node present in the test Redis (`bus.mark_present`).

Nothing here imports `tools`, `approvals` or `memory` (the `nodes` import
contract covers its tests).
"""

from types import SimpleNamespace

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from core.channels import CHANNEL_WEB
from nodes import bus
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.services import pair_node, record_capability_snapshot, relabel_node
from workspaces import rooms


def pairing_code(owner):
    raw, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    return raw


def advertise(node, *, protocol_version, catalogue=None):
    """Record a snapshot as the gateway does on `hello`."""
    catalogue = default_catalogue() if catalogue is None else catalogue
    return record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=protocol_version,
    )


def add_node(owner, *, name, platform, role, room=None, aliases=(), protocol_version=1):
    paired = pair_node(
        raw_code=pairing_code(owner), node_name=name, protocol_versions=[1], platform=platform
    )
    node = relabel_node(
        actor=owner,
        node=paired.node,
        channel=CHANNEL_WEB,
        room_id=room.pk if room is not None else None,
        role=role,
        aliases=list(aliases),
    )
    advertise(node, protocol_version=protocol_version)
    bus.mark_present(node.pk)
    node.refresh_from_db()
    node.secret = paired.node_secret
    return node


def make_two_nodes(owner, personal, *, third=False):
    study = rooms.create_room(
        actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Çalışma odası", sort_order=0
    )
    salon = rooms.create_room(
        actor=owner, workspace=personal, channel=CHANNEL_WEB, name="Salon", sort_order=1
    )
    mac = add_node(
        owner,
        name="MacBook",
        platform="macos",
        role="laptop",
        room=study,
        aliases=("iş bilgisayarı",),
        protocol_version=2,
    )
    desk = add_node(owner, name="Masaüstü", platform="linux", role="workstation", room=study)
    media = None
    if third:
        media = add_node(owner, name="Medya", platform="linux", role="media", room=salon)
    nodes = [node for node in (mac, desk, media) if node is not None]
    return SimpleNamespace(
        owner=owner,
        personal=personal,
        study=study,
        salon=salon,
        mac=mac,
        desk=desk,
        media=media,
        nodes=nodes,
    )


def cleanup(world):
    for node in world.nodes:
        bus.clear_presence(node.pk)
        bus.clear_inbox(node.pk)
