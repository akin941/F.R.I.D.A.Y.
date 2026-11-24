"""Shared fixtures for the Phase 12 branch 1 tests: the owner, a personal
and a firm workspace (with a non-owner member), an isolated probe registry
and a small fake probe set whose items the test controls."""

import secrets
from datetime import UTC, datetime, time

import pytest

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from core import probes
from proactive.models import ProactiveSchedule
from workspaces.models import Workspace, WorkspaceMember

PASSWORD = "Str0ngDevPass!42"
# A Tuesday, 10:00 Europe/Istanbul (07:00 UTC).
TUESDAY_10 = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def member(db):
    return register_user(email="member@test.dev", password=PASSWORD, name="Member")


@pytest.fixture
def firm(owner, member):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    WorkspaceMember.objects.create(workspace=firm, user=member, role=WorkspaceMember.Role.MEMBER)
    return firm


@pytest.fixture(autouse=True)
def isolated_probes(monkeypatch):
    """Each test starts with an empty probe registry (real probes land in
    branch 2; the tests here run a fake set)."""
    monkeypatch.setattr(probes, "_REGISTRY", {})
    return probes


class FakeProbe:
    """A controllable probe: `items[workspace_id]` is the list of
    (key, label) pairs it reports for that workspace. `on_collect` may run
    an effect inside the run (to exercise the guard)."""

    def __init__(self, name="tasks.fake_due", section="tasks", notice=True, link="/tasks"):
        self.items = {}
        self.calls = []
        self.on_collect = None
        self.data_class = None
        self.spec = probes.ProbeSpec(
            name=name,
            owner_app=name.split(".", 1)[0],
            collect=self.collect,
            digest_section=section,
            notice_kind=name if notice else "",
            link=link,
            notice_template="Yeni: {label}",
        )

    def collect(self, workspace, owner, now):
        self.calls.append(workspace.pk)
        if self.on_collect is not None:
            self.on_collect(workspace, owner, now)
        pairs = self.items.get(workspace.pk, [])
        data_class = self.data_class or (
            probes.DATA_CLASS_PERSONAL if workspace.type == "personal" else probes.DATA_CLASS_FIRM
        )
        labelled = [probes.ProbeItem(key=key, label=label) for key, label in pairs[:5]]
        return probes.ProbeResult(
            count=len(pairs),
            data_class=data_class,
            items=tuple(labelled),
            item_keys=tuple(key for key, _ in pairs),
        )


@pytest.fixture
def fake_probe(isolated_probes):
    probe = FakeProbe()
    probes.register_probe(probe.spec)
    return probe


def make_schedule(workspace, owner, *, kind="heartbeat", enabled=True, next_run_at=None, **fields):
    defaults = (
        {"interval_minutes": 15}
        if kind == ProactiveSchedule.Kind.HEARTBEAT
        else {"local_time": time(7, 45), "weekdays": 0b0011111}
    )
    defaults.update(fields)
    schedule = ProactiveSchedule(
        workspace=workspace,
        kind=kind,
        enabled=enabled,
        created_by=owner,
        next_run_at=next_run_at,
        **defaults,
    )
    schedule.save()
    return schedule


def pat_client(owner, workspace, kind):
    from rest_framework.test import APIClient

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        workspace=workspace, user=owner, name=kind, kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client
