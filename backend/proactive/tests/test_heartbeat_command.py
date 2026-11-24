"""`manage.py proactive_heartbeat`: one beat tick, due schedules only; it
creates no schedule."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from proactive.models import ProactiveRun, ProactiveSchedule
from proactive.tests.conftest import TUESDAY_10, make_schedule

pytestmark = pytest.mark.django_db


def test_runs_due_schedules_as_of_at(owner, workspace, fake_probe):
    make_schedule(workspace, owner, next_run_at=TUESDAY_10)
    out = StringIO()
    call_command("proactive_heartbeat", "--at", TUESDAY_10.isoformat(), stdout=out)
    assert json.loads(out.getvalue())["ran"] == 1
    assert ProactiveRun.objects.count() == 1
    assert ProactiveSchedule.objects.count() == 1


def test_nothing_due_and_a_naive_at_is_refused(owner, workspace, fake_probe):
    out = StringIO()
    call_command("proactive_heartbeat", stdout=out)
    assert json.loads(out.getvalue())["ran"] == 0
    with pytest.raises(CommandError):
        call_command("proactive_heartbeat", "--at", "2026-11-24T10:00:00")
