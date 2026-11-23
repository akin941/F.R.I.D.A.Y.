"""Helpers shared by the gateway tests (`test_gateway_*.py`); their
fixtures (`gateway`, `make_app`, `paired`, `link_node`, ...) live in this
package's conftest."""

import secrets

from accounts.authentication import hash_token
from accounts.models import TrustedDevice
from accounts.services import register_user
from nodes.keys import signing_keyring
from nodes.models import Node
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"


def personal_of(user):
    return Workspace.objects.get(owner=user, type=Workspace.Type.PERSONAL)


def direct_node(user, name="Direct", **device_fields):
    """A node row created directly (not through pairing): returns (node,
    raw_secret)."""
    raw = "jnd_" + secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=user,
        name=name,
        kind=TrustedDevice.Kind.NODE,
        secret_hash=hash_token(raw),
        enrolled_via=TrustedDevice.EnrolledVia.values[0],
        **device_fields,
    )
    node = Node.objects.create(
        id=device.pk,
        device=device,
        owner=user,
        workspace=personal_of(user),
        name=name,
        protocol_version=1,
        pinned_key_id=signing_keyring().key_id,
    )
    return node, raw


def other_user(email="other@test.dev"):
    return register_user(email=email, password=PASSWORD, name="Other")
