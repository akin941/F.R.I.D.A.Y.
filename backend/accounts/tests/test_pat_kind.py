"""PersonalAccessToken `kind` (Phase 4 branch 6): minted as `cli` (default)
or `openai`; a `cli` token authenticates only the PAT-allowlisted views
(CHANNEL_CLI), an `openai` token only the OpenAI-compatible facade
(CHANNEL_OPENAI). A valid token of the wrong kind is a 403, never a
silent authentication."""

import io
import uuid

import pytest
from django.core.management import call_command
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from accounts.authentication import (
    FacadeTokenAuthentication,
    HookTokenAuthentication,
    PersonalAccessTokenAuthentication,
    hash_token,
)
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_HOOK, CHANNEL_OPENAI, CHANNEL_WEB, request_channel
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def mint(web_client, owner, workspace, **extra):
    return web_client(owner).post(
        "/api/v1/auth/tokens",
        {"workspace": str(workspace.id), "name": "t", **extra},
        format="json",
    )


def bearer_client(raw):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _authenticate(auth_class, raw):
    request = APIRequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {raw}")
    drf_request = Request(request, authenticators=[auth_class()])
    drf_request.user  # noqa: B018 -- triggers authentication
    return drf_request


# --- mint ---


def test_kind_defaults_to_cli(owner, workspace, web_client):
    response = mint(web_client, owner, workspace)
    assert response.status_code == 201
    assert response.data["kind"] == "cli"
    assert PersonalAccessToken.objects.get().kind == PersonalAccessToken.Kind.CLI


def test_mint_an_openai_token(owner, workspace, web_client):
    response = mint(web_client, owner, workspace, kind="openai")
    assert response.status_code == 201
    assert response.data["kind"] == "openai"
    listing = web_client(owner).get("/api/v1/auth/tokens").data
    assert listing[0]["kind"] == "openai"
    log = AuditLog.objects.get(action="accounts.pat.created")
    assert log.metadata["kind"] == "openai"


def test_unknown_kind_is_rejected(owner, workspace, web_client):
    response = mint(web_client, owner, workspace, kind="admin")
    assert response.status_code == 400
    assert "kind" in response.data
    assert not PersonalAccessToken.objects.exists()


def test_revoke_audit_records_the_kind(owner, workspace, web_client):
    token_id = mint(web_client, owner, workspace, kind="openai").data["id"]
    response = web_client(owner).post(f"/api/v1/auth/tokens/{token_id}/revoke")
    assert response.status_code == 200
    log = AuditLog.objects.get(action="accounts.pat.revoked")
    assert log.metadata == {"kind": "openai"}


def test_create_dev_pat_mints_the_requested_kind(owner, workspace, settings):
    settings.DEBUG = True
    out = io.StringIO()
    call_command("create_dev_pat", owner.email, workspace.name, "--kind", "openai", stdout=out)
    token = PersonalAccessToken.objects.get(token_hash=hash_token(out.getvalue().strip()))
    assert token.kind == PersonalAccessToken.Kind.OPENAI
    assert AuditLog.objects.get(action="accounts.pat.created").metadata["kind"] == "openai"


# --- kind -> channel ---


def test_each_authenticator_maps_its_own_kind_to_its_channel(owner, workspace, web_client):
    cli_raw = mint(web_client, owner, workspace).data["token"]
    openai_raw = mint(web_client, owner, workspace, kind="openai").data["token"]

    cli_request = _authenticate(PersonalAccessTokenAuthentication, cli_raw)
    assert request_channel(cli_request) == CHANNEL_CLI
    assert cli_request.auth.bound_workspace_id == str(workspace.id)

    openai_request = _authenticate(FacadeTokenAuthentication, openai_raw)
    assert request_channel(openai_request) == CHANNEL_OPENAI
    token = PersonalAccessToken.objects.get(token_hash=hash_token(openai_raw))
    assert openai_request.auth.token_id == str(token.id)


_CLI_SURFACES = [
    ("get", "/api/v1/ai/conversations"),
    ("post", "/api/v1/ai/conversations"),
    ("get", "/api/v1/ai/conversations/{id}"),
    ("patch", "/api/v1/ai/conversations/{id}"),
    ("get", "/api/v1/ai/conversations/{id}/messages"),
    ("post", "/api/v1/ai/conversations/{id}/messages"),
    ("get", "/api/v1/agents"),
    ("post", "/api/v1/agents"),
    ("get", "/api/v1/agents/{id}"),
    ("post", "/api/v1/agents/{id}/cancel"),
    ("get", "/api/v1/approvals/requests"),
    ("post", "/api/v1/approvals/requests"),
    ("post", "/api/v1/approvals/requests/{id}/approve"),
    ("get", "/api/v1/activity/summary"),
]


@pytest.mark.parametrize(("method", "path"), _CLI_SURFACES)
def test_openai_token_is_refused_on_every_cli_surface(owner, workspace, web_client, method, path):
    raw = mint(web_client, owner, workspace, kind="openai").data["token"]
    url = path.format(id=uuid.uuid4())
    response = getattr(bearer_client(raw), method)(url, {}, format="json")
    assert response.status_code == 403
    assert response.data["code"] == "token_kind_forbidden"
    token = PersonalAccessToken.objects.get(token_hash=hash_token(raw))
    assert token.last_used_at is None


def test_wrong_kind_is_audited_as_an_auth_failure(owner, workspace, web_client):
    raw = mint(web_client, owner, workspace, kind="openai").data["token"]
    bearer_client(raw).get("/api/v1/ai/conversations")
    log = AuditLog.objects.get(action="accounts.pat.auth_failed")
    assert log.metadata["reason"] == "wrong_kind"
    assert log.metadata["surface"] == "cli"
    assert raw not in str(log.metadata)


def test_cli_token_keeps_working_on_cli_surfaces(owner, workspace, web_client):
    raw = mint(web_client, owner, workspace).data["token"]
    response = bearer_client(raw).get("/api/v1/ai/conversations")
    assert response.status_code == 200


# --- hook kind (Phase 8A branch 3, D6) ---


def test_owner_mints_a_hook_token_bound_to_their_personal_workspace(owner, workspace, web_client):
    response = mint(web_client, owner, workspace, kind="hook")
    assert response.status_code == 201
    assert response.data["kind"] == "hook"
    request = _authenticate(HookTokenAuthentication, response.data["token"])
    assert request_channel(request) == CHANNEL_HOOK
    assert request.auth.bound_workspace_id == str(workspace.id)


def test_hook_token_cannot_be_bound_to_a_firm_workspace(owner, web_client):
    from workspaces.services import create_firm_workspace

    firm = create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)
    response = mint(web_client, owner, firm, kind="hook")
    assert response.status_code == 400
    assert not PersonalAccessToken.objects.exists()


def test_only_the_installation_owner_mints_a_hook_token(web_client):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    personal = Workspace.objects.get(owner=member)
    response = mint(web_client, member, personal, kind="hook")
    assert response.status_code == 400
    assert not PersonalAccessToken.objects.exists()
    # The same member may still mint an ordinary CLI token.
    assert mint(web_client, member, personal).status_code == 201


@pytest.mark.parametrize("kind", ["cli", "openai"])
def test_hook_authenticator_refuses_every_other_kind(owner, workspace, web_client, kind):
    raw = mint(web_client, owner, workspace, kind=kind).data["token"]
    with pytest.raises(PermissionDenied):
        _authenticate(HookTokenAuthentication, raw)


@pytest.mark.parametrize(
    "auth_class", [PersonalAccessTokenAuthentication, FacadeTokenAuthentication]
)
def test_other_authenticators_refuse_a_hook_token(owner, workspace, web_client, auth_class):
    raw = mint(web_client, owner, workspace, kind="hook").data["token"]
    with pytest.raises(PermissionDenied):
        _authenticate(auth_class, raw)
    assert PersonalAccessToken.objects.get(token_hash=hash_token(raw)).last_used_at is None
