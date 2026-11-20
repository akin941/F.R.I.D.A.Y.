"""Stable error codes on every API error body (Phase 4 branch 3).

Two halves: (1) the vocabulary -- every code the backend can emit is
listed in `core.error_codes` (the frontend's catalog test checks the other
direction: every listed code has a Turkish message); (2) the shape --
400/401/403/404/429 bodies all carry a string `code`, domain errors carry
their domain code, and `detail` is kept for compatibility.

Lives in `config/tests` (not `core/tests`): it imports every kernel app's
domain exceptions, which the "core must stay independent of kernel apps"
contract forbids for `core` itself.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework import exceptions as drf_exceptions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from accounts.authentication import hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from agents.execution import AgentServiceError
from approvals.models import ApprovalRequest
from approvals.services import ApprovalServiceError
from connectors.services import ConnectorServiceError
from core.api_errors import (
    STATUS_FALLBACK_CODES,
    CodedJSONRenderer,
    exception_handler,
    fallback_code,
)
from core.error_codes import DOMAIN_ERROR_CODES, ERROR_CODES, GENERIC_ERROR_CODES
from core.throttling import ThrottleBackendUnavailable
from deploys.services import DeployServiceError
from harness.services import HarnessServiceError
from knowledge.services import KnowledgeServiceError
from memory.services import MemoryServiceError
from tasks.services import TaskServiceError
from voice.services import VoiceServiceError
from workspaces.models import Workspace, WorkspaceMember
from workspaces.services import WorkspacePermissionDenied

PASSWORD = "Str0ngDevPass!42"

_DOMAIN_BASES = [
    AgentServiceError,
    ApprovalServiceError,
    ConnectorServiceError,
    DeployServiceError,
    HarnessServiceError,
    KnowledgeServiceError,
    MemoryServiceError,
    TaskServiceError,
    VoiceServiceError,
]


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def _domain_exception_classes():
    classes = [WorkspacePermissionDenied]
    for base in _DOMAIN_BASES:
        classes.extend(_all_subclasses(base))
    return classes


# --- vocabulary ---


def test_every_domain_exception_declares_a_registered_code():
    classes = _domain_exception_classes()
    assert len(classes) >= 17
    for cls in classes:
        assert isinstance(getattr(cls, "code", None), str), cls
        assert cls.code in DOMAIN_ERROR_CODES, cls


def test_every_domain_code_is_emitted_by_some_exception():
    emitted = {cls.code for cls in _domain_exception_classes()}
    assert emitted == set(DOMAIN_ERROR_CODES)


def test_every_generic_code_the_handler_can_emit_is_registered():
    drf_codes = {
        cls.default_code
        for cls in vars(drf_exceptions).values()
        if isinstance(cls, type) and issubclass(cls, drf_exceptions.APIException)
    }
    emitted = (
        drf_codes
        | set(STATUS_FALLBACK_CODES.values())
        | {fallback_code(500), ThrottleBackendUnavailable.default_code, "token_not_valid"}
    )
    assert emitted <= GENERIC_ERROR_CODES
    assert GENERIC_ERROR_CODES <= ERROR_CODES


def test_codes_are_flat_quoted_literals_the_frontend_can_parse():
    # frontend/src/lib/i18n/catalog.test.ts extracts every "..." literal
    # from core/error_codes.py; a code must be a plain dotted identifier.
    for code in ERROR_CODES:
        assert code.replace(".", "").replace("_", "").isalpha(), code


# --- handler / renderer units ---


def _render(response):
    renderer = CodedJSONRenderer()
    return renderer.render(response.data, renderer_context={"response": response})


def test_handler_codes_a_validation_error_and_keeps_field_errors():
    response = exception_handler(ValidationError({"title": ["required"]}), {})
    assert response.status_code == 400
    assert response.data == {"title": ["required"], "code": "invalid"}


def test_handler_wraps_a_bare_list_validation_error():
    response = exception_handler(ValidationError(["bad"]), {})
    assert response.data["code"] == "invalid"
    assert response.data["errors"] == ["bad"]


def test_handler_never_clobbers_a_field_literally_named_code():
    response = exception_handler(ValidationError({"code": ["Invalid pairing code."]}), {})
    assert response.data == {"code": ["Invalid pairing code."]}


def test_handler_uses_the_exception_default_code():
    response = exception_handler(ThrottleBackendUnavailable(), {})
    assert response.status_code == 503
    assert response.data["code"] == "throttle_backend_unavailable"


def test_handler_degrades_an_unregistered_code_to_the_status_fallback():
    exc = drf_exceptions.AuthenticationFailed("User is inactive", code="user_inactive")
    response = exception_handler(exc, {})
    assert response.data["code"] == "authentication_failed"


def test_handler_leaves_non_drf_exceptions_to_django():
    assert exception_handler(RuntimeError("boom"), {}) is None


def test_renderer_backfills_hand_built_error_bodies():
    assert _render(Response(status=404)) == b'{"code":"not_found"}'
    assert (
        _render(Response({"detail": "workspace query parameter is required."}, status=400))
        == b'{"detail":"workspace query parameter is required.","code":"invalid"}'
    )
    assert _render(Response({"detail": "x"}, status=418)) == b'{"detail":"x","code":"error"}'


def test_renderer_leaves_success_bodies_alone():
    assert _render(Response({"ok": True}, status=200)) == b'{"ok":true}'


# --- end-to-end shape over real endpoints ---


@pytest.fixture
def owner(db):
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def _pending_request(workspace, requested_by):
    return ApprovalRequest.objects.create(
        workspace=workspace, tool_name="some_gated_tool", requested_by=requested_by
    )


def test_400_validation_body_has_code_and_field_errors(owner, workspace, web_client):
    response = web_client(owner).post(
        "/api/v1/tasks", {"workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "invalid"
    assert "title" in body


def test_400_hand_built_body_has_code(owner, web_client):
    response = web_client(owner).get("/api/v1/tasks")
    assert response.status_code == 400
    assert response.json() == {
        "detail": "workspace query parameter is required.",
        "code": "invalid",
    }


def test_401_anonymous_has_code(db):
    response = APIClient().get("/api/v1/tasks")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert "detail" in response.json()


def test_401_bad_bearer_has_code(db):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-jwt")
    response = client.get("/api/v1/tasks")
    assert response.status_code == 401
    assert response.json()["code"] == "token_not_valid"


def test_404_bodiless_has_code(owner, web_client):
    response = web_client(owner).get(
        "/api/v1/tasks", {"workspace": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404
    assert response.json() == {"code": "not_found"}


def test_429_has_code(owner, workspace, monkeypatch, web_client):
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {**ScopedRateThrottle.THROTTLE_RATES, "cost": "0/min"}
    )
    response = web_client(owner).post(
        "/api/v1/agents", {"workspace": str(workspace.id)}, format="json"
    )
    assert response.status_code == 429
    assert response.json()["code"] == "throttled"


@mock.patch("approvals.services._resolve_tool", lambda name: SimpleNamespace(gated=True))
def test_403_role_denied_has_domain_code(owner, workspace, web_client):
    viewer = register_user(email="viewer@test.dev", password=PASSWORD, name="Viewer")
    WorkspaceMember.objects.create(workspace=workspace, user=viewer, role="viewer")
    request = _pending_request(workspace, owner)
    response = web_client(viewer).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 403
    assert response.json()["code"] == "approval.permission_denied"
    assert response.json()["detail"]


def test_403_channel_forbidden_has_domain_code(owner, workspace):
    admin = register_user(email="admin2@test.dev", password=PASSWORD, name="Admin2")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role="admin")
    raw = "jpat_errcode-raw-token-00000000000000"
    PersonalAccessToken.objects.create(
        user=admin, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    request = _pending_request(workspace, owner)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    response = client.post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 403
    # The view-level `IsWebChannel` guard refuses before the service's own
    # `ApprovalChannelForbidden` check is reached (Phase 4 branch 2).
    assert response.json()["code"] == "channel_forbidden"


def test_403_agent_forbidden_has_domain_code(owner, workspace, web_client):
    agent = register_user(email="agent@test.dev", password=PASSWORD, name="Agent")
    agent.is_agent = True
    agent.save(update_fields=["is_agent"])
    WorkspaceMember.objects.create(workspace=workspace, user=agent, role="admin")
    request = _pending_request(workspace, owner)
    response = web_client(agent).post(f"/api/v1/approvals/requests/{request.id}/reject")
    assert response.status_code == 403
    assert response.json()["code"] == "approval.agent_forbidden"


def test_400_expired_has_domain_code(owner, workspace, settings, web_client):
    admin = register_user(email="admin3@test.dev", password=PASSWORD, name="Admin3")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role="admin")
    request = _pending_request(workspace, owner)
    ApprovalRequest.objects.filter(id=request.id).update(
        created_at=request.created_at - timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS + 1)
    )
    response = web_client(admin).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 400
    assert response.json()["code"] == "approval.expired"


def test_400_invalid_transition_has_domain_code(owner, workspace, web_client):
    admin = register_user(email="admin4@test.dev", password=PASSWORD, name="Admin4")
    WorkspaceMember.objects.create(workspace=workspace, user=admin, role="admin")
    request = _pending_request(workspace, owner)
    ApprovalRequest.objects.filter(id=request.id).update(status="rejected")
    response = web_client(admin).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert response.status_code == 400
    assert response.json()["code"] == "approval.invalid_transition"
