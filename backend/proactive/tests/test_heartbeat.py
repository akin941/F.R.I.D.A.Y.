"""Phase 12 D3/D4: the heartbeat runner on a manual clock -- due
selection, the `skip_locked` claim, `next_run_at` advanced before the run,
no catch-up, quiet hours, pause / inactive owner skips (audited once per
transition), run bookkeeping outside the context, and the script gate."""

import threading
from datetime import UTC, datetime, time, timedelta

import pytest
from django.db import connection, connections, transaction

from ai_core.gateway import InferenceGateway
from ai_core.models import ModelUsage
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION
from config.celery import build_beat_schedule
from core import probes
from proactive import tasks as proactive_tasks
from proactive.models import (
    ProactiveNotice,
    ProactivePause,
    ProactiveProbeState,
    ProactiveRun,
    ProactiveSchedule,
)
from proactive.tasks import heartbeat, run_due_schedules
from proactive.tests.conftest import TUESDAY_10, FakeProbe, make_schedule

pytestmark = pytest.mark.django_db

NOW = TUESDAY_10


def _reload(schedule):
    return ProactiveSchedule.objects.get(pk=schedule.pk)


def test_both_proactive_entries_are_on_the_worker_beat_only():
    worker = build_beat_schedule(runner_mode=False)
    runner = build_beat_schedule(runner_mode=True)
    assert worker["proactive-heartbeat"] == {"task": "proactive.tasks.heartbeat", "schedule": 60.0}
    assert worker["proactive-notice-prune"]["task"] == "proactive.tasks.prune"
    assert worker["proactive-notice-prune"]["schedule"] == 24 * 60 * 60.0
    assert "proactive-heartbeat" not in runner and "proactive-notice-prune" not in runner


def test_only_enabled_due_schedules_run(owner, workspace, firm, fake_probe):
    due = make_schedule(workspace, owner, next_run_at=NOW - timedelta(seconds=1))
    make_schedule(firm, owner, next_run_at=NOW + timedelta(minutes=1))
    make_schedule(
        workspace, owner, kind="morning_digest", enabled=False, next_run_at=NOW - timedelta(1)
    )
    summary = run_due_schedules(NOW)
    assert summary["ran"] == 1
    assert list(ProactiveRun.objects.values_list("schedule_id", flat=True)) == [due.pk]
    assert fake_probe.calls == [workspace.pk]


def test_next_run_is_advanced_before_the_run_so_a_crash_never_repeats_it(
    owner, workspace, fake_probe, monkeypatch
):
    schedule = make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    calls = []

    def crash(claimed, *, now):
        calls.append(_reload(claimed).next_run_at)
        raise RuntimeError("worker died")

    monkeypatch.setattr(proactive_tasks, "run_schedule", crash)
    run_due_schedules(NOW)
    assert calls == [NOW + timedelta(minutes=15)]  # already advanced when the run started
    assert run_due_schedules(NOW)["ran"] == 0
    assert len(calls) == 1
    assert _reload(schedule).next_run_at == NOW + timedelta(minutes=15)


def test_no_catch_up_after_a_48_hour_gap(owner, workspace, fake_probe):
    heartbeat_row = make_schedule(workspace, owner, next_run_at=NOW - timedelta(hours=48))
    digest_row = make_schedule(
        workspace, owner, kind="morning_digest", next_run_at=NOW - timedelta(hours=48)
    )
    assert run_due_schedules(NOW)["ran"] == 2
    assert ProactiveRun.objects.count() == 2
    assert _reload(heartbeat_row).next_run_at == NOW + timedelta(minutes=15)
    # Tuesday 10:00 local -> Wednesday 07:45 local.
    assert _reload(digest_row).next_run_at == datetime(2026, 11, 25, 4, 45, tzinfo=UTC)
    assert run_due_schedules(NOW + timedelta(minutes=1))["ran"] == 0


def test_quiet_hours_are_honoured(owner, workspace, fake_probe):
    late = datetime(2026, 11, 24, 20, 0, tzinfo=UTC)  # 23:00 local
    schedule = make_schedule(
        workspace,
        owner,
        next_run_at=late - timedelta(minutes=1),
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
    )
    summary = run_due_schedules(late)
    assert summary["ran"] == 0 and summary["skipped:quiet_hours"] == 1
    assert ProactiveRun.objects.count() == 0
    # Next: 07:00 local on Wednesday (04:00 UTC).
    assert _reload(schedule).next_run_at == datetime(2026, 11, 25, 4, 0, tzinfo=UTC)


def _skips():
    return list(
        AuditLog.objects.filter(action="proactive.schedule.skipped").values_list(
            "metadata__reason", flat=True
        )
    )


def test_a_paused_workspace_is_skipped_and_audited_once_per_transition(
    owner, workspace, fake_probe
):
    schedule = make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    pause = ProactivePause.objects.create(workspace=workspace, paused_by=owner)
    for step in range(3):
        at = NOW + timedelta(minutes=15 * step)
        assert run_due_schedules(at)["skipped:paused"] == 1
    assert ProactiveRun.objects.count() == 0
    assert _skips() == ["paused"]
    pause.delete()
    assert run_due_schedules(NOW + timedelta(minutes=45))["ran"] == 1
    assert _reload(schedule).last_skip_reason == ""
    ProactivePause.objects.create(workspace=workspace, paused_by=owner)
    assert run_due_schedules(NOW + timedelta(minutes=60))["skipped:paused"] == 1
    assert _skips() == ["paused", "paused"]


def test_an_inactive_owner_skips_every_schedule_audited_once(owner, workspace, fake_probe):
    make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    owner.is_active = False
    owner.save()
    assert run_due_schedules(NOW)["skipped:owner_inactive"] == 1
    assert run_due_schedules(NOW + timedelta(minutes=15))["skipped:owner_inactive"] == 1
    assert _skips() == ["owner_inactive"]
    assert fake_probe.calls == []


@pytest.mark.parametrize("scenario", ["quiet", "notices", "failed", "denied"])
def test_bookkeeping_after_every_outcome(owner, workspace, fake_probe, scenario):
    schedule = make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    # A baseline run first, so the scenario's run is a delta run.
    run_due_schedules(NOW)
    fake_probe.items[workspace.pk] = [("t1", "Faturayı öde")] if scenario == "notices" else []
    if scenario == "failed":
        fake_probe.on_collect = lambda *a: 1 / 0
    if scenario == "denied":
        fake_probe.on_collect = lambda ws, actor, now: ProactiveSchedule.objects.get(
            pk=schedule.pk
        ).save()
    later = NOW + timedelta(minutes=15)
    run_due_schedules(later)
    run = ProactiveRun.objects.order_by("-created_at").first()
    assert run.outcome == scenario
    row = _reload(schedule)
    assert row.last_run_at is not None and row.last_run_at >= run.started_at
    assert row.next_run_at == later + timedelta(minutes=15)
    if scenario == "denied":
        assert AuditLog.objects.filter(action=DENIAL_ACTION).get().metadata["effect"] == (
            "schedule_write"
        )
    else:
        assert not AuditLog.objects.filter(action=DENIAL_ACTION).exists()


def test_a_heartbeat_never_calls_a_model(owner, workspace, fake_probe):
    """Script gate: probes non-empty, notices made, no ModelUsage row; a
    probe that tries a gateway call -- even `proactive.digest` -- is denied
    (a heartbeat allows no purpose at all)."""
    make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    run_due_schedules(NOW)
    fake_probe.items[workspace.pk] = [("t1", "Faturayı öde"), ("t2", "Kira")]
    attempts = []

    def try_model(ws, actor, now):
        attempts.append(1)
        InferenceGateway().complete("proactive.digest", [{"role": "user", "content": "x"}])

    run_due_schedules(NOW + timedelta(minutes=15))
    assert ProactiveNotice.objects.count() == 2
    fake_probe.on_collect = try_model
    run_due_schedules(NOW + timedelta(minutes=30))
    assert attempts == [1]
    assert ModelUsage.objects.count() == 0
    assert AuditLog.objects.get(action=DENIAL_ACTION).metadata["effect"] == "gateway_call"


def test_a_quiet_run_writes_only_its_run_row(owner, workspace, fake_probe):
    make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    run_due_schedules(NOW)  # the baseline
    state_stamp = ProactiveProbeState.objects.get().updated_at
    audit_before = AuditLog.objects.count()
    run_due_schedules(NOW + timedelta(minutes=15))
    assert ProactiveRun.objects.filter(outcome="quiet").count() == 2
    assert ProactiveNotice.objects.count() == 0
    assert ProactiveProbeState.objects.get().updated_at == state_stamp
    assert AuditLog.objects.count() == audit_before


def test_a_fake_probe_set_turns_into_notices_through_the_real_heartbeat_task(
    owner, workspace, fake_probe, frozen_now
):
    """Done-when: the beat's task, dispatched like the beat does."""
    make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    frozen_now(NOW)
    heartbeat.delay()
    fake_probe.items[workspace.pk] = [("a1", "Onay: dağıtım")]
    frozen_now(NOW + timedelta(minutes=15))
    heartbeat.delay()
    notice = ProactiveNotice.objects.get()
    assert notice.title == "Yeni: Onay: dağıtım"
    assert notice.link == "/tasks" and notice.data_class == "personal"


@pytest.mark.django_db(transaction=True)
def test_a_locked_schedule_is_skipped_not_waited_for(owner, workspace, fake_probe):
    locked = make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    holding = threading.Event()
    release = threading.Event()

    def hold_lock():
        try:
            with transaction.atomic():
                ProactiveSchedule.objects.select_for_update().get(pk=locked.pk)
                holding.set()
                release.wait(timeout=10)
        finally:
            connections.close_all()

    thread = threading.Thread(target=hold_lock)
    thread.start()
    try:
        assert holding.wait(timeout=10)
        assert run_due_schedules(NOW)["ran"] == 0  # skip_locked: no wait, no run
    finally:
        release.set()
        thread.join()
    assert run_due_schedules(NOW)["ran"] == 1
    connection.close()


def test_a_hung_probe_is_cancelled_by_the_statement_timeout(owner, workspace, fake_probe, settings):
    """`PROACTIVE_PROBE_TIMEOUT_MS` only flags; the per-probe statement
    timeout bounds a hung probe query: it is recorded as `probe_timeout`,
    the next probe still runs, and the cap does not outlive the probe."""
    settings.PROACTIVE_PROBE_STATEMENT_TIMEOUT_MS = 100

    def hang(ws, actor, now):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(5)")

    fake_probe.on_collect = hang
    healthy = FakeProbe(name="tasks.fake_healthy")
    probes.register_probe(healthy.spec)
    healthy.items[workspace.pk] = [("t1", "Kira")]
    make_schedule(workspace, owner, next_run_at=NOW - timedelta(minutes=1))
    run_due_schedules(NOW)
    run = ProactiveRun.objects.get()
    assert run.probe_failures == {"tasks.fake_due": "probe_timeout"}
    assert "tasks.fake_due" in run.slow_probes
    assert run.probe_counts == {"tasks.fake_healthy": 1}
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('statement_timeout')")
        assert cursor.fetchone()[0] != "100ms"
