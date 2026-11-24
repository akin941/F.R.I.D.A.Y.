"""Phase 12 D20 (branch 2): `manage.py proactive_checkpoint` over an
audited enable/disable history and the digests' opened/spoken data."""

import json
from datetime import UTC, datetime, time
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from audit.models import AuditLog
from proactive import services
from proactive.checkpoint import build_checkpoint
from proactive.models import DigestRun, ProactiveRun, ProactiveSchedule

pytestmark = pytest.mark.django_db


def _save(owner, workspace, enabled):
    return services.save_schedule(
        actor=owner,
        channel="web",
        workspace=workspace,
        kind="morning_digest",
        enabled=enabled,
        local_time=time(7, 45),
        weekdays=0b0011111,
    )


def test_checkpoint_reads_the_audited_history_and_digest_telemetry(owner, workspace, frozen_now):
    # The audit log is append-only: the history is written at frozen times.
    frozen_now(datetime(2026, 11, 1, 8, 0, tzinfo=UTC))
    _save(owner, workspace, True)
    frozen_now(datetime(2026, 11, 4, 8, 0, tzinfo=UTC))
    _save(owner, workspace, False)
    frozen_now(datetime(2026, 11, 10, 8, 0, tzinfo=UTC))
    _save(owner, workspace, True)
    assert AuditLog.objects.filter(action__startswith="proactive.schedule.").exists()
    # An unrelated heartbeat enable never counts.
    services.save_schedule(
        actor=owner,
        channel="web",
        workspace=workspace,
        kind="heartbeat",
        enabled=True,
        interval_minutes=15,
    )

    schedule = ProactiveSchedule.objects.get(workspace=workspace, kind="morning_digest")
    for index in range(2):
        run = ProactiveRun.objects.create(
            workspace=workspace, schedule=schedule, kind="morning_digest", started_at=timezone.now()
        )
        DigestRun.objects.create(
            workspace=workspace,
            run=run,
            schedule=schedule,
            spoken_text="Günaydın.",
            composed_by="model" if index else "template",
            data_class="personal",
            opened_at=timezone.now() if index else None,
            spoken_at=timezone.now() if index else None,
            spoken_channel="node_voice" if index else "",
        )

    (row,) = build_checkpoint(now=datetime(2026, 11, 12, 8, 0, tzinfo=UTC))
    assert row["workspace"] == str(workspace.pk)
    assert row["enabled_now"] is True
    assert row["first_enabled_at"].startswith("2026-11-01T08:00")
    assert [t["event"] for t in row["transitions"]] == ["enabled", "disabled", "enabled"]
    # 3 days (1st -> 4th) + 2 days (10th -> 12th).
    assert row["days_enabled"] == 5.0
    assert row["digests"] == 2
    assert row["composed_by_model"] == 1
    assert row["opened"] == 1
    assert row["spoken_node_voice"] == 1 and row["spoken_web"] == 0


def test_the_command_prints_text_and_json(owner, workspace):
    _save(owner, workspace, True)
    out = StringIO()
    call_command("proactive_checkpoint", stdout=out)
    assert "days enabled" in out.getvalue()
    out = StringIO()
    call_command("proactive_checkpoint", "--json", stdout=out)
    rows = json.loads(out.getvalue())
    assert rows[0]["enabled_now"] is True


def test_the_command_without_a_schedule(db):
    out = StringIO()
    call_command("proactive_checkpoint", stdout=out)
    assert "No morning_digest schedule" in out.getvalue()
