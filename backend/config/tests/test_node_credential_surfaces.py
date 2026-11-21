"""A Friday Node secret reaches exactly one DRF endpoint (Phase 9 spike
S9.0 criterion 5; release gate "a node credential accepted on any surface
but its own, or yielding CHANNEL_WEB").

Walks every routed DRF view with a valid `jnd_` node secret: every route
whose view authenticates at all answers 401/403, and the node is never
authenticated (`last_seen_at` is never refreshed) anywhere but
`POST /api/v1/auth/nodes/credential/rotate`. The anonymous device-session
mint refuses it too. (The node WebSocket arrives in Phase 9 branch 4 and is
not a DRF view.)

Modelled on test_hook_token_surfaces.py.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import NodeCredentialAuthentication
from accounts.devices import enroll_node_device, issue_pairing_code
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from accounts.views import NodeCredentialRotateView
from config.tests.test_hook_token_surfaces import _routes

pytestmark = pytest.mark.django_db

ROTATE_URL = "/api/v1/auth/nodes/credential/rotate"


@pytest.fixture
def node():
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    raw_code, _ = issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    device, secret = enroll_node_device(raw_code=raw_code, node_name="Mac", client_ip="10.0.0.2")
    return device, secret


def test_the_rotate_view_is_the_only_one_using_the_node_authenticator():
    users = {view for _, view, auth in _routes() if NodeCredentialAuthentication in auth}
    assert users == {NodeCredentialRotateView}
    rotate = [auth for _, view, auth in _routes() if view is NodeCredentialRotateView]
    assert rotate == [[NodeCredentialAuthentication]]


def test_a_node_secret_is_refused_on_every_other_route(node):
    device, secret = node
    # Older than the touch interval: any successful node authentication on
    # the walk would move it.
    seen_before = timezone.now() - timedelta(minutes=30)
    TrustedDevice.objects.filter(pk=device.pk).update(last_seen_at=seen_before)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    checked = 0
    for url, view_class, auth in _routes():
        if view_class is NodeCredentialRotateView:
            continue
        for method in ("get", "post", "patch", "delete"):
            response = getattr(client, method)(url, {}, format="json")
            if auth:
                assert response.status_code in (401, 403), (method, url, response.status_code)
                checked += 1
    assert checked > 100  # the walk really covered the API
    device.refresh_from_db()
    assert device.last_seen_at == seen_before


def test_a_node_secret_mints_no_web_session(node):
    _, secret = node
    response = APIClient().post(
        "/api/v1/auth/devices/session", {"device_secret": secret}, format="json"
    )
    assert response.status_code == 401


def test_a_node_secret_is_accepted_on_the_rotate_endpoint(node):
    device, secret = node
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {secret}")
    response = client.post(ROTATE_URL)
    assert response.status_code == 200, response.content
    assert response.json()["node_id"] == str(device.pk)
