"""Phase 12 D3: schedules are owner-made over the web; cron cannot create
cron. The services refuse every channel but web and every actor but the
workspace owner; the model refuses a save inside a run; nothing
registered -- tool, read tool, intent, MVP 8 skill, connector manifest --
reaches a schedule or the pause."""

import inspect
from datetime import UTC, datetime, time

import pytest

from accounts.models import User
from assistant.models import Message
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION, recorded_unattended_run
from connectors.manifest import list_manifests
from core.channels import (
    CHANNEL_CLI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_OPENAI,
    CHANNEL_SYSTEM,
    CHANNEL_TELEGRAM,
    CHANNEL_UNKNOWN,
    CHANNEL_WEB,
)
from core.unattended import UnattendedDenied
from proactive import services
from proactive.models import ProactivePause, ProactiveSchedule
from proactive.tests.conftest import TUESDAY_10, make_schedule
from skills import catalog
from tools import registry

pytestmark = pytest.mark.django_db

LOW_TRUST = [
    CHANNEL_CLI,
    CHANNEL_OPENAI,
    CHANNEL_HOOK,
    CHANNEL_NODE,
    CHANNEL_NODE_VOICE,
    CHANNEL_TELEGRAM,
    CHANNEL_SYSTEM,
    CHANNEL_UNKNOWN,
]


def _save(actor, workspace, channel=CHANNEL_WEB, **fields):
    values = {"enabled": True, "local_time": time(7, 45), "weekdays": 0b0011111}
    values.update(fields)
    return services.save_schedule(
        actor=actor, channel=channel, workspace=workspace, kind="morning_digest", **values
    )


def _actions():
    return sorted(
        AuditLog.objects.filter(action__startswith="proactive.").values_list("action", flat=True)
    )


def test_the_owner_creates_a_morning_digest_over_the_web(owner, workspace, frozen_now):
    frozen_now(TUESDAY_10)
    schedule = _save(owner, workspace)
    assert schedule.kind == "morning_digest" and schedule.enabled
    # Tuesday 10:00 local -> Wednesday 07:45 local (04:45 UTC).
    assert schedule.next_run_at == datetime(2026, 11, 25, 4, 45, tzinfo=UTC)
    assert _actions() == ["proactive.schedule.created", "proactive.schedule.enabled"]


def test_one_row_per_workspace_and_kind_and_enable_history_is_audited(owner, workspace):
    first = _save(owner, workspace)
    second = _save(owner, workspace, enabled=False)
    third = _save(owner, workspace, enabled=True, local_time=time(8, 0))
    assert first.pk == second.pk == third.pk
    assert ProactiveSchedule.objects.count() == 1
    assert third.local_time == time(8, 0)
    transitions = AuditLog.objects.filter(
        action__in=["proactive.schedule.enabled", "proactive.schedule.disabled"]
    ).order_by("created_at")
    assert [row.action for row in transitions] == [
        "proactive.schedule.enabled",
        "proactive.schedule.disabled",
        "proactive.schedule.enabled",
    ]
    assert second.next_run_at is None


def test_a_heartbeat_schedule_with_quiet_hours(owner, workspace, frozen_now):
    frozen_now(TUESDAY_10)
    schedule = services.save_schedule(
        actor=owner,
        channel=CHANNEL_WEB,
        workspace=workspace,
        kind="heartbeat",
        enabled=True,
        interval_minutes=30,
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
    )
    assert schedule.next_run_at == datetime(2026, 11, 24, 7, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("weekly_report", {}),
        ("morning_digest", {"interval_minutes": 30}),
        ("heartbeat", {"local_time": time(7, 0)}),
        ("heartbeat", {"interval_minutes": 45}),
        ("heartbeat", {"interval_minutes": 15, "quiet_start": time(22, 0)}),
        ("morning_digest", {"weekdays": 0}),
        ("morning_digest", {"weekdays": 128}),
        ("heartbeat", {}),
    ],
)
def test_invalid_schedules_are_refused(owner, workspace, kind, fields):
    with pytest.raises(services.ProactiveInvalid):
        services.save_schedule(
            actor=owner, channel=CHANNEL_WEB, workspace=workspace, kind=kind, enabled=True, **fields
        )
    assert ProactiveSchedule.objects.count() == 0


def test_a_digest_without_a_time_cannot_be_enabled(owner, workspace):
    with pytest.raises(services.ProactiveInvalid):
        services.save_schedule(
            actor=owner,
            channel=CHANNEL_WEB,
            workspace=workspace,
            kind="morning_digest",
            enabled=True,
        )


@pytest.mark.parametrize("channel", LOW_TRUST)
def test_save_schedule_and_set_pause_refuse_every_channel_but_web(owner, workspace, channel):
    with pytest.raises(services.ProactivePermissionDenied):
        _save(owner, workspace, channel=channel)
    with pytest.raises(services.ProactivePermissionDenied):
        services.set_pause(actor=owner, channel=channel, workspace=workspace, paused=True)
    assert not ProactiveSchedule.objects.exists() and not ProactivePause.objects.exists()


@pytest.fixture
def principals(owner, member, firm):
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    inactive = User.objects.create_user(email="gone@test.dev")
    inactive.is_active = False
    inactive.save()
    return {"agent": agent, "member": member, "inactive": inactive}


@pytest.mark.parametrize("who", ["agent", "member", "inactive", "anonymous"])
def test_save_schedule_and_set_pause_refuse_every_actor_but_the_owner(
    owner, workspace, firm, principals, who
):
    from django.contrib.auth.models import AnonymousUser

    actor = AnonymousUser() if who == "anonymous" else principals[who]
    for target in (workspace, firm):
        with pytest.raises(services.ProactivePermissionDenied):
            _save(actor, target)
        with pytest.raises(services.ProactivePermissionDenied):
            services.set_pause(actor=actor, channel=CHANNEL_WEB, workspace=target, paused=True)
    assert not ProactiveSchedule.objects.exists() and not ProactivePause.objects.exists()


def test_the_owner_may_schedule_a_firm_workspace_they_own(owner, firm):
    assert _save(owner, firm).workspace == firm


def test_an_inactive_owner_is_refused(owner, workspace):
    owner.is_active = False
    owner.save()
    with pytest.raises(services.ProactivePermissionDenied):
        _save(owner, workspace)


def _denied(fn):
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("run-c"):
            fn()


def test_schedule_and_pause_writes_raise_inside_a_run(owner, workspace):
    schedule = make_schedule(workspace, owner)
    pause = ProactivePause.objects.create(workspace=workspace, paused_by=owner)

    _denied(lambda: schedule.save())
    _denied(lambda: schedule.delete())
    _denied(lambda: ProactiveSchedule(workspace=workspace, kind="morning_digest").save())
    _denied(lambda: pause.save())
    _denied(lambda: pause.delete())
    _denied(lambda: _save(owner, workspace))
    _denied(lambda: services.set_pause(actor=owner, channel="web", workspace=workspace, paused=0))
    assert ProactivePause.objects.filter(pk=pause.pk).exists()
    assert ProactiveSchedule.objects.count() == 1
    assert AuditLog.objects.filter(action=DENIAL_ACTION).count() == 7


# --- cron cannot create cron: the walk -----------------------------------

_FORBIDDEN_WORDS = ("proactive", "schedule", "pause")


def _callables(obj):
    for value in vars(obj).values() if hasattr(obj, "__dict__") else ():
        if callable(value):
            yield value


def _reaches_proactive(value):
    module = getattr(value, "__module__", "") or ""
    if module.startswith("proactive"):
        return True
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError):
        return False
    return "proactive" in source


def test_no_gated_tool_reaches_schedules_or_the_pause():
    for name in registry.list_tool_names():
        assert not any(word in name for word in _FORBIDDEN_WORDS), name
        spec = registry.get_tool(name)
        for value in _callables(spec):
            assert not _reaches_proactive(value), (name, value)


def test_no_read_tool_reaches_schedules_or_the_pause():
    for name in registry.list_read_tool_names():
        assert not any(word in name for word in _FORBIDDEN_WORDS), name
        for value in _callables(registry.get_read_tool(name)):
            assert not _reaches_proactive(value), (name, value)


def test_no_router_intent_names_a_schedule():
    for value in Message.Intent.values:
        assert not any(word in value for word in _FORBIDDEN_WORDS), value


def test_no_mvp8_skill_step_names_a_schedule():
    for name in catalog.list_skill_names():
        spec = catalog.get_skill(name)
        assert not any(word in repr(spec).lower() for word in ("proactive", "pause")), name
        for step in spec.steps:
            assert "schedule" not in repr(step.payload).lower(), name


def test_no_connector_manifest_names_a_schedule():
    for manifest in list_manifests():
        text = repr(manifest).lower()
        assert "proactive" not in text, manifest.name
        for tool in manifest.tools:
            assert not any(word in tool.name for word in _FORBIDDEN_WORDS), tool.name
