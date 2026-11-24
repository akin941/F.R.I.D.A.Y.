"""Phase 12 D3/D5/D20: role x endpoint for the schedules, notices and pause
endpoints over the five Phase 4 principals, the `cli`/`openai`/`hook`
PATs, a node secret, a `node_voice` token, a kill-switched owner and a
firm workspace with a non-owner member; plus the pause flow."""

from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token, pair_node
from audit.models import AuditLog
from proactive.models import ProactiveNotice, ProactivePause, ProactiveRun, ProactiveSchedule
from proactive.tasks import run_due_schedules
from proactive.tests.conftest import make_schedule

pytestmark = pytest.mark.django_db

BASE = "/api/v1/proactive"


@pytest.fixture
def notice(workspace):
    return ProactiveNotice.objects.create(
        workspace=workspace,
        kind="tasks.fake_due",
        dedupe_key="a",
        title="Yeni: A",
        count=1,
        link="/tasks",
        data_class="personal",
    )


@pytest.fixture
def principals(owner, workspace, member, firm, web_client):
    _, secret = pair_node(owner)
    voice = mint_token(secret)

    def make(principal):
        if principal in ("owner-web", "owner-firm"):
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            kind = principal.removeprefix("owner-pat-")
            return bearer(make_pat(owner, workspace, kind)[0])
        if principal == "node-secret":
            return bearer(secret)
        if principal == "node-voice":
            return bearer(voice)
        if principal in ("non-owner", "firm-member"):
            return web_client(member)
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        return APIClient()

    return make


def _query(principal, workspace, firm):
    target = firm if principal in ("owner-firm", "firm-member") else workspace
    return f"?workspace={target.pk}"


ENDPOINTS = {
    "schedules:get": lambda c, q, n: c.get(f"{BASE}/schedules{q}"),
    "schedules:put": lambda c, q, n: c.put(
        f"{BASE}/schedules/heartbeat{q}", {"enabled": True, "interval_minutes": 15}, format="json"
    ),
    "notices:get": lambda c, q, n: c.get(f"{BASE}/notices{q}"),
    "notices:read": lambda c, q, n: c.post(f"{BASE}/notices/{n.pk}/read"),
    "notices:dismiss-all": lambda c, q, n: c.post(f"{BASE}/notices/dismiss-all{q}"),
    "pause:get": lambda c, q, n: c.get(f"{BASE}/pause{q}"),
    "pause:post": lambda c, q, n: c.post(f"{BASE}/pause{q}"),
    "pause:delete": lambda c, q, n: c.delete(f"{BASE}/pause{q}"),
}

MATRIX = [
    ("owner-web", 200),
    ("owner-firm", 200),
    ("kill-switched-owner", 200),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, 401),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, 401),
    ("node-secret", 401),
    ("node-voice", 401),
    ("non-owner", 403),
    ("firm-member", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_role_matrix(principals, workspace, firm, notice, endpoint, principal, expected):
    client = principals(principal)
    response = ENDPOINTS[endpoint](client, _query(principal, workspace, firm), notice)
    assert response.status_code == expected, response.content
    if expected != 200:
        assert not ProactiveSchedule.objects.exists()
        assert not ProactivePause.objects.exists()
        notice.refresh_from_db()
        assert notice.read_at is None and notice.dismissed_at is None


def test_the_node_voice_token_of_a_kill_switched_owner_is_refused(owner, workspace):
    _, secret = pair_node(owner)
    voice = bearer(mint_token(secret))
    devices.engage_node_kill_switch(owner=owner, via="test")
    assert voice.get(f"{BASE}/notices").status_code == 401


def test_another_workspace_is_404(owner, web_client, member):
    from workspaces.models import Workspace

    foreign = Workspace.objects.get(owner=member)
    client = web_client(owner)
    for url in ("schedules", "notices", "pause"):
        assert client.get(f"{BASE}/{url}?workspace={foreign.pk}").status_code == 404
    assert client.get(f"{BASE}/pause?workspace=not-a-uuid").status_code == 404


def test_another_workspaces_notice_is_404(owner, web_client, member):
    from workspaces.models import Workspace

    foreign = ProactiveNotice.objects.create(
        workspace=Workspace.objects.get(owner=member),
        kind="tasks.fake_due",
        dedupe_key="x",
        title="t",
        data_class="personal",
    )
    assert web_client(owner).post(f"{BASE}/notices/{foreign.pk}/read").status_code == 404


def test_schedule_put_round_trip_and_strict_fields(owner, workspace, web_client):
    client = web_client(owner)
    response = client.put(
        f"{BASE}/schedules/morning_digest",
        {"enabled": True, "local_time": "07:45", "weekdays": 31, "speak_on_open": True},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["local_time"] == "07:45:00"
    listing = client.get(f"{BASE}/schedules").json()
    assert listing["paused"] is False
    assert [row["kind"] for row in listing["schedules"]] == ["morning_digest"]
    bad = client.put(
        f"{BASE}/schedules/morning_digest", {"enabled": True, "cron": "* * * * *"}, format="json"
    )
    assert bad.status_code == 400
    wrong_kind = client.put(
        f"{BASE}/schedules/heartbeat", {"enabled": True, "local_time": "07:45"}, format="json"
    )
    assert wrong_kind.status_code == 400 and wrong_kind.json()["code"] == "invalid"
    assert client.put(f"{BASE}/schedules/nightly", {"enabled": True}).status_code == 404


def test_notice_list_read_and_dismiss_all(owner, workspace, web_client, notice):
    client = web_client(owner)
    body = client.get(f"{BASE}/notices").json()
    assert body["unread"] == 1 and body["notices"][0]["title"] == "Yeni: A"
    assert client.post(f"{BASE}/notices/{notice.pk}/read").json()["read_at"] is not None
    assert client.get(f"{BASE}/notices").json()["unread"] == 0
    assert len(client.get(f"{BASE}/notices?state=all").json()["notices"]) == 1
    assert client.get(f"{BASE}/notices?state=bogus").status_code == 400
    ProactiveNotice.objects.create(
        workspace=workspace, kind="k.k", dedupe_key="b", title="t", data_class="personal"
    )
    assert client.post(f"{BASE}/notices/dismiss-all").json() == {"dismissed": 1}
    assert AuditLog.objects.filter(action="proactive.notices.dismissed").count() == 1


def _pause_rows(action):
    return AuditLog.objects.filter(action=action).count()


def test_pause_flow(owner, workspace, firm, web_client, fake_probe):
    from proactive.tests.conftest import TUESDAY_10 as now

    client = web_client(owner)
    schedule = make_schedule(workspace, owner, next_run_at=now - timedelta(minutes=1))
    firm_schedule = make_schedule(firm, owner, next_run_at=now - timedelta(minutes=1))
    assert client.post(f"{BASE}/pause").json()["paused"] is True
    assert client.post(f"{BASE}/pause").json()["paused"] is True  # idempotent
    assert _pause_rows("proactive.paused") == 1
    assert client.get(f"{BASE}/pause").json()["paused"] is True
    assert client.get(f"{BASE}/pause?workspace={firm.pk}").json()["paused"] is False
    summary = run_due_schedules(now)
    assert summary["skipped:paused"] == 1 and summary["ran"] == 1
    assert list(ProactiveRun.objects.values_list("schedule_id", flat=True)) == [firm_schedule.pk]
    assert client.delete(f"{BASE}/pause").json()["paused"] is False
    assert client.delete(f"{BASE}/pause").json()["paused"] is False
    assert _pause_rows("proactive.resumed") == 1
    ProactiveSchedule.objects.filter(pk=schedule.pk).update(next_run_at=now)
    assert run_due_schedules(now + timedelta(minutes=1))["ran"] == 1
