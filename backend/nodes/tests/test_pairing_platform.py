"""Phase 13 D3: pairing records `platform` (optional; absent = macOS so the
existing Swift app pairs unchanged; invalid = 400 before any code is
consumed), a blank name gets the platform's default, and a duplicate name
is suffixed ("MacBook 2") -- never refused. A revoked node's name is free
again; a kill-switched node keeps its name reserved."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from audit.models import AuditLog
from nodes.models import Node
from nodes.services import pair_node, relabel_node
from nodes.tests.conftest import node_code

pytestmark = pytest.mark.django_db

PAIR_URL = "/api/v1/nodes/pair"


def pair(code, **extra):
    body = {"code": code, "node_name": "MacBook", "protocol_versions": [1], **extra}
    return APIClient().post(PAIR_URL, body, format="json")


def test_platform_absent_means_macos(owner, personal):
    response = pair(node_code(owner))
    assert response.status_code == 201, response.content
    node = Node.objects.get(pk=response.json()["node_id"])
    assert node.platform == "macos" and node.name == "MacBook" and node.name_key == "macbook"
    assert node.role == "other" and node.room is None and node.aliases == []
    assert set(response.json()) == {
        "node_id",
        "node_secret",
        "server_signing_keys",
        "protocol_version",
        "gateway_path",
        "rotate_after_days",
    }  # the response shape is unchanged


@pytest.mark.parametrize("platform", ["macos", "linux", "windows"])
def test_platform_is_recorded_and_audited(owner, personal, platform):
    response = pair(node_code(owner), node_name="Masaüstü", platform=platform)
    assert response.status_code == 201, response.content
    node = Node.objects.get(pk=response.json()["node_id"])
    assert node.platform == platform
    row = AuditLog.objects.get(action="nodes.node.paired")
    assert row.metadata["platform"] == platform
    assert row.metadata["node_id"] == str(node.pk)
    assert row.metadata["name_key"] == "masaüstü"


@pytest.mark.parametrize("platform", ["ios", "Linux", "", None, 3])
def test_an_invalid_platform_is_400_and_consumes_no_code(owner, personal, platform):
    code = node_code(owner)
    response = pair(code, platform=platform)
    assert response.status_code == 400
    assert not Node.objects.exists()
    assert DevicePairingCode.objects.get().consumed_at is None
    assert pair(code).status_code == 201  # the code still works


def test_a_blank_name_gets_the_platform_default(owner, personal):
    response = pair(node_code(owner), node_name="  ", platform="linux")
    node = Node.objects.get(pk=response.json()["node_id"])
    assert node.name == "Linux düğümü"
    response = pair(node_code(owner), node_name="")
    assert Node.objects.get(pk=response.json()["node_id"]).name == "Mac düğümü"


def test_a_duplicate_name_is_suffixed_never_refused(owner, personal):
    names = []
    for _ in range(3):
        response = pair(node_code(owner), node_name="MacBook")
        assert response.status_code == 201
        names.append(Node.objects.get(pk=response.json()["node_id"]).name)
    assert names == ["MacBook", "MacBook 2", "MacBook 3"]
    keys = list(Node.objects.order_by("created_at").values_list("name_key", flat=True))
    assert keys == ["macbook", "macbook 2", "macbook 3"]
    # The device row carries the same (suffixed) name.
    assert set(TrustedDevice.objects.filter(kind="node").values_list("name", flat=True)) == set(
        names
    )


def test_case_and_punctuation_count_as_duplicates(owner, personal):
    pair(node_code(owner), node_name="MacBook")
    response = pair(node_code(owner), node_name="MACBOOK!")
    assert Node.objects.get(pk=response.json()["node_id"]).name == "MACBOOK! 2"


def test_an_alias_of_another_node_is_also_a_duplicate(owner, personal):
    first = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    relabel_node(actor=owner, node=first, channel="web", aliases=["Laptop"])
    second = pair_node(raw_code=node_code(owner), node_name="laptop", protocol_versions=[1]).node
    assert second.name == "laptop 2"


def test_a_long_duplicate_name_stays_within_100_chars(owner, personal):
    long_name = "M" * 100
    pair_node(raw_code=node_code(owner), node_name=long_name, protocol_versions=[1])
    second = pair_node(raw_code=node_code(owner), node_name=long_name, protocol_versions=[1]).node
    assert len(second.name) == 100 and second.name.endswith(" 2")


def test_a_revoked_nodes_name_may_be_reused(owner, personal, fake_revocation_redis):
    first = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    devices.revoke_device(device=first.device, actor=owner)
    second = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    assert second.name == "MacBook"


def test_an_expired_nodes_name_may_be_reused(owner, personal):
    first = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    TrustedDevice.objects.filter(pk=first.device_id).update(
        secret_issued_at=timezone.now() - timedelta(days=3650),
        last_seen_at=timezone.now() - timedelta(days=3650),
    )
    second = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    assert second.name == "MacBook"


def test_another_owners_nodes_do_not_count(owner, personal):
    from accounts.tests.node_voice_helpers import other_user

    _, device = other_user()
    other_node_owner = device.user
    from workspaces.models import Workspace

    Node.objects.create(
        id=device.pk,
        device=device,
        owner=other_node_owner,
        workspace=Workspace.objects.get(owner=other_node_owner),
        name="MacBook",
        name_key="macbook",
        pinned_key_id="k",
    )
    mine = pair_node(raw_code=node_code(owner), node_name="MacBook", protocol_versions=[1]).node
    assert mine.name == "MacBook"


def test_pairing_during_the_kill_switch_stays_refused(owner, personal):
    code = node_code(owner)
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert pair(code).status_code == 409
    assert not Node.objects.exists()
