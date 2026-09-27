"""Phase 5 branch 1 (ADR-010): `create_request(..., system_created=True)` is
the ONLY thing that separates a system-only gate (`user_invocable=False`:
the harness Gates A/B today, `github_open_pr` and `deploy` later) from a
user request. These tests pin the load-bearing invariant from both ends:

- the service refuses a system-only tool for every origin unless the caller
  passed the literal `system_created=True`;
- the HTTP view passes `system_created=False` unconditionally -- no body
  field, header or origin value can flip it;
- no view or serializer module anywhere in the backend passes anything but
  a literal `False` (an AST scan, so a future view cannot quietly opt in).
"""

import ast
import secrets
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from approvals import views as approvals_views
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_SYSTEM, CHANNEL_WEB
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
URL = "/api/v1/approvals/requests"
BACKEND = Path(__file__).resolve().parents[2]

_SYSTEM_ONLY = SimpleNamespace(
    gated=True,
    user_invocable=False,
    create_execution=lambda request: "exec",
    dispatch=lambda execution_id: None,
)
_USER_INVOCABLE = SimpleNamespace(
    gated=True, create_execution=lambda request: "exec", dispatch=lambda execution_id: None
)


def _resolve(name):
    return _SYSTEM_ONLY if name == "system_only_gate" else _USER_INVOCABLE


@pytest.fixture(autouse=True)
def _stub_tools():
    with mock.patch("approvals.services._resolve_tool", _resolve):
        yield


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def pat_client(user, workspace):
    from rest_framework.test import APIClient

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        user=user, workspace=workspace, name="cli", token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


# --- service ---


class TestService:
    @pytest.mark.parametrize("origin", ApprovalRequest.Origin.values)
    @pytest.mark.parametrize("channel", [CHANNEL_WEB, CHANNEL_CLI, CHANNEL_SYSTEM])
    def test_system_only_tool_is_refused_for_every_origin_by_default(
        self, workspace, owner, origin, channel
    ):
        with pytest.raises(InvalidTransition, match="only be requested by the system"):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="system_only_gate",
                origin=origin,
                channel=channel,
            )
        assert not ApprovalRequest.objects.exists()

    @pytest.mark.parametrize("origin", ApprovalRequest.Origin.values)
    def test_explicit_false_is_refused_for_every_origin(self, workspace, owner, origin):
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="system_only_gate",
                origin=origin,
                channel=CHANNEL_SYSTEM,
                system_created=False,
            )

    @pytest.mark.parametrize("truthy", [1, "true", "True", [True], {"x": 1}])
    def test_only_the_literal_true_opens_it(self, workspace, owner, truthy):
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="system_only_gate",
                origin=ApprovalRequest.Origin.HARNESS,
                channel=CHANNEL_SYSTEM,
                system_created=truthy,
            )

    def test_harness_origin_alone_no_longer_mints_a_system_only_gate(self, workspace, owner):
        """The Phase 3 check keyed on `origin == HARNESS`; ADR-010 moved it
        to `system_created`. A caller stating the harness origin without
        the explicit flag is refused."""
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="system_only_gate",
                origin=ApprovalRequest.Origin.HARNESS,
                channel=CHANNEL_SYSTEM,
            )

    @pytest.mark.parametrize("origin", ApprovalRequest.Origin.values)
    def test_system_created_mints_independently_of_origin(self, workspace, owner, origin):
        """`origin` keeps meaning "who produced the request": the owner
        clicking "Deploy" (Phase 5 branch 6) is a HUMAN-origin request for
        a system-created gate."""
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="system_only_gate",
            origin=origin,
            channel=CHANNEL_WEB,
            system_created=True,
        )
        assert request.origin == origin
        entry = AuditLog.objects.get(action="approval.requested", resource_id=str(request.id))
        assert entry.metadata["system_created"] is True
        assert entry.metadata["origin"] == origin

    def test_user_request_audit_records_system_created_false(self, workspace, owner):
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="some_gated_tool",
            origin=ApprovalRequest.Origin.HUMAN,
            channel=CHANNEL_WEB,
        )
        entry = AuditLog.objects.get(action="approval.requested", resource_id=str(request.id))
        assert entry.metadata["system_created"] is False

    def test_system_created_is_keyword_only(self):
        import inspect

        parameter = inspect.signature(create_request).parameters["system_created"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is False


# --- HTTP view ---

_SPOOF_BODIES = [
    {},
    {"system_created": True},
    {"system_created": "true"},
    {"origin": "harness"},
    {"origin": "harness", "system_created": True, "requested_channel": "system"},
    {"payload": {"system_created": True, "origin": "harness"}},
]
_SPOOF_HEADERS = [
    {},
    {"HTTP_X_SYSTEM_CREATED": "true"},
    {"HTTP_X_JARVIS_ORIGIN": "harness", "HTTP_X_SYSTEM_CREATED": "1"},
]


def _clients(owner, workspace, web_client):
    return {"web": web_client(owner), "cli": pat_client(owner, workspace)}


class TestHttpViewNeverPassesSystemCreated:
    @pytest.mark.parametrize("channel", ["web", "cli"])
    @pytest.mark.parametrize("extra", _SPOOF_BODIES)
    @pytest.mark.parametrize("headers", _SPOOF_HEADERS)
    def test_system_only_tool_is_refused_whatever_the_request_says(
        self, workspace, owner, web_client, channel, extra, headers
    ):
        client = _clients(owner, workspace, web_client)[channel]
        body = {"workspace": str(workspace.id), "tool_name": "system_only_gate", **extra}
        response = client.post(URL, body, format="json", **headers)
        assert response.status_code == 400
        assert response.json()["code"] == "approval.invalid_transition"
        assert not ApprovalRequest.objects.filter(tool_name="system_only_gate").exists()

    @pytest.mark.parametrize("channel", ["web", "cli"])
    @pytest.mark.parametrize("tool_name", ["system_only_gate", "some_gated_tool"])
    @pytest.mark.parametrize("extra", _SPOOF_BODIES)
    def test_view_passes_the_literal_false(
        self, workspace, owner, web_client, channel, tool_name, extra
    ):
        """Spy on the service call itself: the flag the view hands over is
        explicitly present and exactly `False`, for any tool, any channel,
        any body."""
        calls = []
        real = approvals_views.create_request

        def spy(**kwargs):
            calls.append(kwargs)
            return real(**kwargs)

        client = _clients(owner, workspace, web_client)[channel]
        body = {"workspace": str(workspace.id), "tool_name": tool_name, **extra}
        with mock.patch.object(approvals_views, "create_request", spy):
            client.post(
                URL, body, format="json", HTTP_X_SYSTEM_CREATED="true", HTTP_X_ORIGIN="harness"
            )
        assert len(calls) == 1
        assert "system_created" in calls[0]
        assert calls[0]["system_created"] is False
        assert calls[0]["origin"] == ApprovalRequest.Origin.HUMAN


# --- scan: no view or serializer ever passes system_created=True ---


def _is_literal_false(node):
    return isinstance(node, ast.Constant) and node.value is False


def system_created_violations(source, filename="<string>"):
    """Every place a module passes `system_created` as anything other than
    the literal `False`: a keyword argument (`f(system_created=...)`) or a
    dict literal key (`f(**{"system_created": ...})`). Returned as
    `filename:line` strings."""
    violations = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.keyword) and node.arg == "system_created":
            if not _is_literal_false(node.value):
                violations.append(f"{filename}:{node.value.lineno}")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "system_created"
                    and not _is_literal_false(value)
                ):
                    violations.append(f"{filename}:{key.lineno}")
    return violations


def _http_surface_modules():
    """`views.py`/`serializers.py` in every app, plus any module or package
    whose name says it is one (`*views*.py`, `views/`, ...) -- tests and
    the virtualenv excluded."""
    modules = []
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND)
        parts = relative.parts
        if parts[0].startswith(".") or "tests" in parts or "migrations" in parts:
            continue
        names = [*parts[:-1], path.stem]
        if any("views" in name or "serializers" in name for name in names):
            modules.append(path)
    return modules


def test_scan_covers_the_http_surface():
    relative = {str(path.relative_to(BACKEND)) for path in _http_surface_modules()}
    assert "approvals/views.py" in relative
    assert "approvals/serializers.py" in relative
    assert "harness/views.py" in relative
    assert len(relative) >= 16


def test_no_view_or_serializer_passes_system_created_true():
    violations = []
    for path in _http_surface_modules():
        violations += system_created_violations(path.read_text(), str(path.relative_to(BACKEND)))
    assert violations == []


def test_approvals_view_passes_the_flag_explicitly_as_false():
    tree = ast.parse((BACKEND / "approvals/views.py").read_text())
    flags = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.keyword) and node.arg == "system_created"
    ]
    assert len(flags) == 1
    assert _is_literal_false(flags[0].value)


@pytest.mark.parametrize(
    "source",
    [
        "create_request(system_created=True)",
        "create_request(system_created=flag)",
        "create_request(system_created=request.data.get('system_created', False))",
        "create_request(**{'system_created': True})",
        "kwargs = {'system_created': is_system}",
    ],
)
def test_scanner_catches_violations(source):
    assert system_created_violations(source)


@pytest.mark.parametrize(
    "source",
    ["create_request(system_created=False)", "create_request(origin=HUMAN)", "x = {'a': True}"],
)
def test_scanner_allows_the_literal_false(source):
    assert system_created_violations(source) == []
