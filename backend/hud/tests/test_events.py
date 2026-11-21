"""`GET /api/v1/hud/events` (Phase 6 branch 5, brief A8): role x endpoint
against the five Phase 4 principals, workspace isolation (another
workspace's approvals and tasks never appear), the snapshot/delta/keepalive
/end protocol, the bounded stream, the health panel's provider-down and
budget-exhausted states, and "reads only". No real provider: rows are
created directly, the clock is fake."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import AIBudget, ModelProvider, ModelUsage
from approvals.models import ApprovalRequest
from hud.tests.conftest import EVENTS, PASSWORD, read_stream
from tasks.models import Task
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PRINCIPALS = [
    ("owner-web", 200),
    ("owner-pat-cli", 403),
    ("owner-pat-openai", 403),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


def _approval(
    workspace, owner, *, tool="vault_export", status=ApprovalRequest.Status.PENDING, **kw
):
    return ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name=tool,
        payload={"secret_payload_marker": "do-not-leak"},
        status=status,
        requested_by=owner,
        **kw,
    )


def _task(workspace, owner, title, *, due=None, status=Task.Status.OPEN):
    return Task.objects.create(
        workspace=workspace,
        title=title,
        due_date=due if due is not None else timezone.localdate(),
        status=status,
        created_by=owner,
    )


def _provider(name, capability=ModelProvider.Capability.CHAT, **kw):
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=capability,
        model_id=f"{name}-model",
        **kw,
    )


def _usage(workspace, provider=None, *, success=True, cost="0", purpose="chat", **kw):
    return ModelUsage.objects.create(
        workspace=workspace,
        provider=provider,
        purpose=purpose,
        success=success,
        estimated_cost=Decimal(cost),
        input_tokens=10,
        output_tokens=5,
        **kw,
    )


def _firm(owner, name="Firma"):
    firm = Workspace.objects.create(name=name, owner=owner, type="company")
    WorkspaceMember.objects.create(workspace=firm, user=owner, role="owner")
    return firm


# --- role x endpoint ---


@pytest.mark.parametrize(("principal", "expected"), PRINCIPALS)
def test_role_matrix(principal_client, workspace, principal, expected):
    response = principal_client(principal).get(EVENTS, {"workspace": str(workspace.id)})
    assert response.status_code == expected
    if expected == 200:
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache, no-store"
        frames = read_stream(response)
        assert frames[1]["type"] == "snapshot"
    else:
        assert not getattr(response, "streaming", False)
    if expected == 403 and principal.startswith("owner-pat"):
        assert response.json()["code"] in {"channel_forbidden", "token_kind_forbidden"}


def test_accept_event_stream_is_not_refused(web_client, owner, workspace):
    response = web_client(owner).get(
        EVENTS, {"workspace": str(workspace.id)}, HTTP_ACCEPT="text/event-stream"
    )
    assert response.status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_read_only(web_client, owner, workspace, method):
    response = getattr(web_client(owner), method)(f"{EVENTS}?workspace={workspace.id}")
    assert response.status_code == 405


def test_workspace_is_required_and_validated(web_client, owner):
    client = web_client(owner)
    assert client.get(EVENTS).status_code == 400
    assert client.get(EVENTS, {"workspace": "not-a-uuid"}).status_code == 400


def test_another_users_workspace_is_a_404(web_client, owner):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    theirs = Workspace.objects.get(owner=stranger)
    _approval(theirs, stranger)
    assert web_client(owner).get(EVENTS, {"workspace": str(theirs.id)}).status_code == 404
    assert web_client(owner).get(EVENTS, {"workspace": str(uuid.uuid4())}).status_code == 404


# --- isolation ---


def test_another_workspaces_rows_never_appear(stream, owner, workspace):
    mine = _approval(workspace, owner)
    _task(workspace, owner, "benim görevim")
    _usage(workspace, cost="0.010000")
    # The owner's own firm workspace: still another workspace for this stream.
    firm = _firm(owner)
    firm_approval = _approval(firm, owner, tool="github_open_pr")
    _task(firm, owner, "firma görevi")
    _usage(firm, cost="5.000000")
    # A stranger's workspace.
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    _approval(Workspace.objects.get(owner=stranger), stranger)

    snapshot = stream(workspace.id)[1]
    assert snapshot["workspace"] == str(workspace.id)
    assert [item["id"] for item in snapshot["approvals"]["items"]] == [str(mine.id)]
    assert snapshot["approvals"]["pending_count"] == 1
    assert [item["title"] for item in snapshot["tasks"]["items"]] == ["benim görevim"]
    assert snapshot["cost"]["estimated_cost"] == "0.010000"

    firm_snapshot = stream(firm.id)[1]
    assert [item["id"] for item in firm_snapshot["approvals"]["items"]] == [str(firm_approval.id)]
    assert firm_snapshot["cost"]["estimated_cost"] == "5.000000"


def test_approval_payload_never_leaves(stream, owner, workspace):
    _approval(workspace, owner)
    body = str(stream(workspace.id))
    assert "secret_payload_marker" not in body
    assert "do-not-leak" not in body


# --- sections ---


def test_snapshot_shape(stream, owner, workspace):
    frames = stream(workspace.id)
    assert frames[0] == "retry: 3000"
    snapshot = frames[1]
    assert snapshot["type"] == "snapshot"
    assert snapshot["interval_seconds"] == 5
    assert set(snapshot) >= {"approvals", "tasks", "cost", "health"}
    assert snapshot["approvals"] == {"pending_count": 0, "items": [], "tools_running": 0}
    assert snapshot["cost"]["calls"] == 0
    assert snapshot["cost"]["local_only"] is True
    assert snapshot["health"]["preflight"] is None
    # Phase 10 D12: the terminal section (no executions yet).
    assert snapshot["terminal"] == {"items": [], "running_count": 0}


def test_approvals_section_only_lists_fresh_pending(stream, owner, workspace, settings):
    fresh = _approval(workspace, owner)
    stale = _approval(workspace, owner)
    ApprovalRequest.objects.filter(id=stale.id).update(
        created_at=timezone.now() - timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS + 1)
    )
    _approval(workspace, owner, status=ApprovalRequest.Status.REJECTED)
    _approval(
        workspace,
        owner,
        status=ApprovalRequest.Status.APPROVED,
        decided_at=timezone.now(),
        decided_by=owner,
    )
    section = stream(workspace.id)[1]["approvals"]
    assert [item["id"] for item in section["items"]] == [str(fresh.id)]
    item = section["items"][0]
    assert set(item) == {"id", "tool_name", "origin", "requested_channel", "created_at"}
    # A just-approved request reads as a running tool.
    assert section["tools_running"] == 1


def test_list_limit(stream, owner, workspace, settings):
    settings.HUD_LIST_LIMIT = 2
    for _ in range(4):
        _approval(workspace, owner)
    section = stream(workspace.id)[1]["approvals"]
    assert section["pending_count"] == 4
    assert len(section["items"]) == 2


def test_tasks_section_is_today_and_overdue_open_tasks(stream, owner, workspace):
    today = timezone.localdate()
    _task(workspace, owner, "bugün")
    _task(workspace, owner, "dün", due=today - timedelta(days=1))
    _task(workspace, owner, "yarın", due=today + timedelta(days=1))
    _task(workspace, owner, "bitti", status=Task.Status.DONE)
    Task.objects.create(workspace=workspace, title="tarihsiz", created_by=owner)
    trashed = _task(workspace, owner, "çöpte")
    Task.all_objects.filter(id=trashed.id).update(deleted_at=timezone.now())

    section = stream(workspace.id)[1]["tasks"]
    assert section["date"] == today.isoformat()
    assert [(i["title"], i["overdue"]) for i in section["items"]] == [
        ("dün", True),
        ("bugün", False),
    ]
    assert section["open_count"] == 2


def test_tasks_section_honours_the_client_date(stream, owner, workspace):
    tomorrow = timezone.localdate() + timedelta(days=1)
    _task(workspace, owner, "yarın", due=tomorrow)
    section = stream(workspace.id, date=tomorrow.isoformat())[1]["tasks"]
    assert section["date"] == tomorrow.isoformat()
    assert [i["title"] for i in section["items"]] == ["yarın"]


def test_cost_section_is_todays_workspace_usage(stream, workspace):
    _usage(workspace, cost="0.020000")
    _usage(workspace, success=False, cost="0.000000")
    old = _usage(workspace, cost="9.000000")
    ModelUsage.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=2))
    cost = stream(workspace.id)[1]["cost"]
    assert cost["calls"] == 2
    assert cost["failed_calls"] == 1
    assert cost["input_tokens"] == 20
    assert cost["estimated_cost"] == "0.020000"
    assert cost["local_only"] is False


# --- health panel ---


def test_health_provider_states(stream, workspace, owner):
    up = _provider("chat-up")
    down = _provider("chat-down", priority=2)
    _provider("stt-unknown", ModelProvider.Capability.STT)
    _provider("tts-off", ModelProvider.Capability.TTS, is_active=False)
    _usage(workspace, up)
    _usage(workspace, down, success=False, error_type="timeout")
    # A later budget refusal says nothing about the provider being up.
    _usage(workspace, down, success=False, error_type="budget_exceeded")
    # Another workspace's success never masks this workspace's view.
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    _usage(Workspace.objects.get(owner=stranger), down, success=True)

    health = stream(workspace.id)[1]["health"]
    states = {p["name"]: p["status"] for p in health["providers"]}
    assert states == {
        "chat-up": "ok",
        "chat-down": "down",
        "stt-unknown": "unknown",
        "tts-off": "disabled",
    }
    assert health["providers_down"] == 1
    down_row = next(p for p in health["providers"] if p["name"] == "chat-down")
    assert down_row["last_call"]["error_type"] == "timeout"
    assert down_row["local"] is True


def test_health_counts_system_rows_but_ignores_stale_ones(stream, workspace, settings):
    provider = _provider("chat-a")
    _usage(None, provider, success=True, purpose="ops.preflight")
    assert stream(workspace.id)[1]["health"]["providers"][0]["status"] == "ok"
    ModelUsage.objects.update(
        created_at=timezone.now() - timedelta(minutes=settings.HUD_PROVIDER_WINDOW_MINUTES + 1)
    )
    assert stream(workspace.id)[1]["health"]["providers"][0]["status"] == "unknown"


def test_health_budget_exhausted(stream, workspace):
    AIBudget.objects.create(name="chat-daily", purposes=["chat"], daily_limit_usd=Decimal("0.05"))
    AIBudget.objects.create(
        name="voice-monthly", purposes=["voice.tts"], monthly_limit_usd=Decimal("10")
    )
    AIBudget.objects.create(
        name="inactive", purposes=["chat"], daily_limit_usd=Decimal("0"), is_active=False
    )
    _usage(workspace, cost="0.060000")
    health = stream(workspace.id)[1]["health"]
    rows = {b["name"]: b for b in health["budgets"]}
    assert set(rows) == {"chat-daily", "voice-monthly"}
    assert rows["chat-daily"]["exhausted"] is True
    assert rows["chat-daily"]["windows"] == [
        {"window": "daily", "limit_usd": "0.050000", "spent_usd": "0.060000", "exhausted": True}
    ]
    assert rows["voice-monthly"]["exhausted"] is False
    assert health["budgets_exhausted"] == 1


def test_health_last_preflight(stream, workspace, settings):
    cache.set(
        settings.HUD_PREFLIGHT_CACHE_KEY,
        {
            "status": "fail",
            "pass": 9,
            "fail": 1,
            "warn": 2,
            "at": "2026-11-20T08:00:00+00:00",
            "failed_checks": ["celery"],
            "detail": "should not be forwarded",
        },
    )
    preflight = stream(workspace.id)[1]["health"]["preflight"]
    assert preflight == {
        "status": "fail",
        "pass": 9,
        "fail": 1,
        "warn": 2,
        "at": "2026-11-20T08:00:00+00:00",
        "failed_checks": ["celery"],
    }


# --- stream protocol ---


def test_keepalive_then_end_when_nothing_changes(stream, workspace):
    frames = stream(workspace.id)
    # retry, snapshot, two quiet ticks (5 s, 10 s), end at 15 s.
    assert frames[0] == "retry: 3000"
    assert frames[1]["type"] == "snapshot"
    assert frames[2:4] == [": keepalive", ": keepalive"]
    assert frames[4] == {"type": "end", "reason": "max_age"}
    assert len(frames) == 5


def test_delta_carries_only_the_changed_sections(stream, workspace, owner, fake_clock):
    created = {}

    def add_approval():
        created["row"] = _approval(workspace, owner)

    fake_clock.ticks = [add_approval]
    frames = stream(workspace.id)
    delta = frames[2]
    assert delta["type"] == "delta"
    assert set(delta) == {"type", "approvals"}
    assert delta["approvals"]["items"][0]["id"] == str(created["row"].id)
    assert frames[3] == ": keepalive"


def test_stream_ends_when_membership_is_lost(stream, owner, fake_clock):
    firm = _firm(owner)

    def leave():
        WorkspaceMember.objects.filter(workspace=firm, user=owner).delete()

    fake_clock.ticks = [leave]
    frames = stream(firm.id)
    assert frames[-1] == {"type": "end", "reason": "scope_lost"}
    assert len(frames) == 3


def test_stream_writes_nothing(stream, workspace, owner):
    _approval(workspace, owner)
    counts = (ApprovalRequest.objects.count(), Task.objects.count(), ModelUsage.objects.count())
    stream(workspace.id)
    assert counts == (
        ApprovalRequest.objects.count(),
        Task.objects.count(),
        ModelUsage.objects.count(),
    )


def test_throttled(web_client, owner, workspace, monkeypatch):
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"hud_events": "1/min"})
    client = web_client(owner)
    assert client.get(EVENTS, {"workspace": str(workspace.id)}).status_code == 200
    assert client.get(EVENTS, {"workspace": str(workspace.id)}).status_code == 429
