"""Channel matrix (Phase 4 branch 2): pins the definition of the high-trust
web channel. Every authenticator any routed view uses must map to a known
channel -- a future authenticator that forgets its channel fails here --
and anything that is not a trusted device's session is never CHANNEL_WEB.

This module deliberately force-authenticates non-web principals (it is the
one allowlisted exception in test_web_client_fixture_guard.py)."""

import secrets

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.exceptions import PermissionDenied
from rest_framework.settings import api_settings
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from accounts.authentication import (
    TOKEN_PREFIX,
    DeviceJWTAuthentication,
    FacadeTokenAuthentication,
    HookTokenAuthentication,
    NodeCredentialAuthentication,
    PersonalAccessTokenAuthentication,
    hash_token,
)
from accounts.devices import (
    enroll_device,
    enroll_node_device,
    issue_pairing_code,
    issue_session_tokens,
)
from accounts.models import DevicePairingCode, PersonalAccessToken, TrustedDevice
from accounts.services import register_user
from approvals.models import ApprovalRequest
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_OPENAI,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
    ChannelPrincipal,
    WebSessionPrincipal,
    request_channel,
)
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

# Every authenticator class the project uses, with the channel it yields.
KNOWN_AUTHENTICATORS = {
    DeviceJWTAuthentication: CHANNEL_WEB,
    PersonalAccessTokenAuthentication: CHANNEL_CLI,
    FacadeTokenAuthentication: CHANNEL_OPENAI,
    HookTokenAuthentication: CHANNEL_HOOK,
    NodeCredentialAuthentication: CHANNEL_NODE,
}


def routed_api_views(resolver=None):
    """(view class, the authentication classes it actually runs with) for
    every routed DRF view -- including the ones injected through
    `as_view(authentication_classes=...)` by the composition root."""
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            yield from routed_api_views(pattern)
        elif isinstance(pattern, URLPattern):
            view_class = getattr(pattern.callback, "view_class", None)
            if view_class is not None and issubclass(view_class, APIView):
                initkwargs = getattr(pattern.callback, "view_initkwargs", {}) or {}
                yield (
                    view_class,
                    list(
                        initkwargs.get("authentication_classes", view_class.authentication_classes)
                    ),
                )


def test_every_routed_authenticator_has_a_known_channel():
    seen = set()
    for view_class, auth_classes in routed_api_views():
        for auth_class in auth_classes:
            seen.add(auth_class)
            assert auth_class in KNOWN_AUTHENTICATORS, (
                f"{view_class.__name__} uses {auth_class.__name__}, which has no entry in the "
                "channel matrix -- decide its channel (default-deny) and add it here."
            )
    assert seen == set(KNOWN_AUTHENTICATORS)


def test_the_device_session_is_the_only_default_authenticator():
    assert list(api_settings.DEFAULT_AUTHENTICATION_CLASSES) == [DeviceJWTAuthentication]


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def _device_tokens(owner):
    raw, _ = issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.PIN,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
    )
    device, _ = enroll_device(raw_code=raw, device_name="Mac", client_ip="10.0.0.2")
    return device, issue_session_tokens(device)


def _authenticate(auth_class, header):
    request = APIRequestFactory().get("/", HTTP_AUTHORIZATION=header)
    from rest_framework.request import Request

    drf_request = Request(request, authenticators=[auth_class()])
    drf_request.user  # noqa: B018 -- triggers authentication
    return drf_request


def test_device_jwt_yields_web(owner):
    device, tokens = _device_tokens(owner)
    request = _authenticate(DeviceJWTAuthentication, f"Bearer {tokens['access']}")
    assert request.user == owner
    assert request.auth == WebSessionPrincipal(device_id=str(device.pk))
    assert request_channel(request) == CHANNEL_WEB


def test_pat_yields_cli(owner, workspace):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name="cli", token_hash=hash_token(raw)
    )
    request = _authenticate(PersonalAccessTokenAuthentication, f"Bearer {raw}")
    assert request_channel(request) == CHANNEL_CLI


def test_openai_pat_yields_openai_on_the_facade_only(owner, workspace):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace,
        user=owner,
        name="open webui",
        kind=PersonalAccessToken.Kind.OPENAI,
        token_hash=hash_token(raw),
    )
    request = _authenticate(FacadeTokenAuthentication, f"Bearer {raw}")
    assert request_channel(request) == CHANNEL_OPENAI
    with pytest.raises(PermissionDenied):
        _authenticate(PersonalAccessTokenAuthentication, f"Bearer {raw}")


def test_cli_pat_is_refused_by_the_facade_authenticator(owner, workspace):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name="cli", token_hash=hash_token(raw)
    )
    with pytest.raises(PermissionDenied):
        _authenticate(FacadeTokenAuthentication, f"Bearer {raw}")


def test_node_secret_yields_node_never_web(owner, workspace):
    raw, _ = issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.PIN,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    device, secret = enroll_node_device(raw_code=raw, node_name="Mac", client_ip="10.0.0.2")
    request = _authenticate(NodeCredentialAuthentication, f"Bearer {secret}")
    assert request.user == owner
    assert request_channel(request) == CHANNEL_NODE
    assert request.auth == ChannelPrincipal(
        channel=CHANNEL_NODE, bound_workspace_id=str(workspace.pk), token_id=str(device.pk)
    )


@pytest.mark.parametrize(
    "auth",
    [None, "a-plain-token", object()],
    ids=["none", "string", "unknown-object"],
)
def test_anything_else_is_unknown_and_low_trust(auth):
    request = type("R", (), {"auth": auth})()
    assert request_channel(request) == CHANNEL_UNKNOWN


def test_a_channel_principal_claiming_web_is_not_web():
    request = type("R", (), {"auth": ChannelPrincipal(channel=CHANNEL_WEB, bound_workspace_id="w")})
    assert request_channel(request()) == CHANNEL_UNKNOWN


def test_forgotten_channel_cannot_decide_an_approval(owner, workspace):
    """A request authenticated without any principal (an authenticator that
    forgot its channel) is CHANNEL_UNKNOWN: approval decisions refuse it."""
    approval = ApprovalRequest.objects.create(
        workspace=workspace, tool_name="vault_export", requested_by=owner
    )
    client = APIClient()
    client.force_authenticate(user=owner)  # no WebSessionPrincipal
    response = client.post(f"/api/v1/approvals/requests/{approval.id}/approve")
    assert response.status_code == 403
    approval.refresh_from_db()
    assert approval.status == ApprovalRequest.Status.PENDING
