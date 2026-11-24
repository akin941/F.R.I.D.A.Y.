"""Phase 12 D5: notices from probe deltas against `ProactiveProbeState` --
the first-run baseline, one notice per key while it stays present (read,
dismissed or pruned notwithstanding), a new one only after the key left and
re-entered, failures leave the baseline, the partial unique constraint,
Friday-template titles and the retention prune."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import probes
from proactive.models import ProactiveNotice, ProactiveProbeState, ProactiveRun
from proactive.runs import OVERFLOW_KEY, run_schedule
from proactive.tasks import prune_notices
from proactive.tests.conftest import FakeProbe, make_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def schedule(owner, workspace):
    return make_schedule(workspace, owner)


def _run(schedule):
    return run_schedule(schedule)


def _keys():
    return sorted(ProactiveNotice.objects.values_list("dedupe_key", flat=True))


def test_the_first_run_records_the_baseline_and_notifies_nothing(schedule, workspace, fake_probe):
    fake_probe.items[workspace.pk] = [(f"k{i}", f"Görev {i}") for i in range(200)]
    run = _run(schedule)
    assert ProactiveNotice.objects.count() == 0
    state = ProactiveProbeState.objects.get(schedule=schedule, probe=fake_probe.spec.name)
    assert len(state.seen_keys) == 200
    assert run.outcome == ProactiveRun.Outcome.QUIET
    assert run.probe_counts == {fake_probe.spec.name: 200}


def test_the_second_run_notifies_only_keys_new_since_the_baseline(schedule, workspace, fake_probe):
    fake_probe.items[workspace.pk] = [("a", "A"), ("b", "B")]
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "A"), ("b", "B"), ("c", "Yeni görev")]
    run = _run(schedule)
    [notice] = ProactiveNotice.objects.all()
    assert notice.dedupe_key == "c"
    assert notice.title == "Yeni: Yeni görev"
    assert notice.kind == fake_probe.spec.name and notice.count == 3
    assert notice.run_id == run.id
    assert run.outcome == ProactiveRun.Outcome.NOTICES and run.notices_created == 1


def test_a_key_that_stays_present_is_notified_once(schedule, workspace, fake_probe):
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    _run(schedule)
    assert _keys() == ["a"]


@pytest.mark.parametrize("handled", ["read", "dismissed", "pruned"])
def test_a_handled_notice_whose_item_is_still_pending_is_not_renotified(
    schedule, workspace, fake_probe, handled, settings
):
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    notice = ProactiveNotice.objects.get()
    long_ago = timezone.now() - timedelta(days=settings.PROACTIVE_NOTICE_RETENTION_DAYS + 1)
    if handled == "read":
        ProactiveNotice.objects.filter(pk=notice.pk).update(read_at=timezone.now())
    elif handled == "dismissed":
        ProactiveNotice.objects.filter(pk=notice.pk).update(dismissed_at=timezone.now())
    else:
        ProactiveNotice.objects.filter(pk=notice.pk).update(read_at=long_ago)
        assert prune_notices() == 1
    _run(schedule)
    _run(schedule)
    assert ProactiveNotice.objects.count() == (0 if handled == "pruned" else 1)


def test_a_key_that_leaves_and_reenters_is_notified_again(schedule, workspace, fake_probe):
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    ProactiveNotice.objects.update(read_at=timezone.now())
    fake_probe.items[workspace.pk] = []
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    assert ProactiveNotice.objects.count() == 2
    assert ProactiveNotice.objects.filter(read_at__isnull=True).count() == 1


def test_a_failing_probe_leaves_its_baseline_unchanged(schedule, workspace, fake_probe):
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    fake_probe.on_collect = lambda *args: (_ for _ in ()).throw(RuntimeError("db gone"))
    fake_probe.items[workspace.pk] = []
    run = _run(schedule)
    assert run.probe_failures == {fake_probe.spec.name: "probe_failed"}
    assert run.outcome == ProactiveRun.Outcome.FAILED
    assert ProactiveProbeState.objects.get().seen_keys == ["a"]
    # The failure was not "left": "a" coming back is not new.
    fake_probe.on_collect = None
    fake_probe.items[workspace.pk] = [("a", "A")]
    _run(schedule)
    assert ProactiveNotice.objects.count() == 0


def test_new_unlabelled_keys_become_one_overflow_notice(schedule, workspace, fake_probe):
    _run(schedule)
    fake_probe.items[workspace.pk] = [(f"k{i}", f"Görev {i}") for i in range(8)]
    _run(schedule)
    assert _keys() == sorted([f"k{i}" for i in range(5)] + [OVERFLOW_KEY])
    overflow = ProactiveNotice.objects.get(dedupe_key=OVERFLOW_KEY)
    assert overflow.title == "Yeni: diğer öğeler"


def test_one_unread_notice_per_key(schedule, workspace):
    values = {
        "workspace": workspace,
        "kind": "tasks.fake_due",
        "dedupe_key": "a",
        "title": "Yeni: A",
        "data_class": "personal",
    }
    ProactiveNotice.objects.create(**values)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ProactiveNotice.objects.create(**values)
    ProactiveNotice.objects.update(read_at=timezone.now())
    ProactiveNotice.objects.create(**values)  # a read one does not block a new one
    assert ProactiveNotice.objects.count() == 2


def test_two_racing_runs_leave_one_unread_notice(owner, workspace, fake_probe):
    """Two schedules' runs (the race the partial constraint belts) both see
    key `a` as new: only one unread notice exists."""
    first = make_schedule(workspace, owner)
    second = make_schedule(workspace, owner, kind="morning_digest")
    second.kind = "heartbeat"  # bypass the one-row-per-kind rule only in memory
    for target in (first, second):
        run_schedule(target)
    fake_probe.items[workspace.pk] = [("a", "A")]
    run_schedule(first)
    run_schedule(second)
    assert ProactiveNotice.objects.filter(dedupe_key="a").count() == 1


def test_a_label_with_markup_or_a_url_is_refused_and_creates_nothing(
    schedule, workspace, fake_probe
):
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "<img src=x>"), ("b", "bkz https://evil.example")]
    run = _run(schedule)
    assert ProactiveNotice.objects.count() == 0
    assert run.probe_failures[fake_probe.spec.name].startswith("probe_invalid")
    assert ProactiveProbeState.objects.get().seen_keys == []


def test_a_probe_with_a_link_outside_the_closed_set_cannot_register(isolated_probes):
    with pytest.raises(ValueError):
        probes.register_probe(FakeProbe(name="tasks.bad_link", link="https://evil.example").spec)


def test_titles_come_from_the_friday_template_only(schedule, workspace, fake_probe):
    _run(schedule)
    fake_probe.items[workspace.pk] = [("a", "Faturayı öde")]
    _run(schedule)
    notice = ProactiveNotice.objects.get()
    assert notice.title == "Yeni: Faturayı öde"
    assert len(notice.title) <= 160


def test_retention_prunes_only_old_handled_notices(workspace, settings):
    days = settings.PROACTIVE_NOTICE_RETENTION_DAYS
    old = timezone.now() - timedelta(days=days + 1)
    recent = timezone.now() - timedelta(days=days - 1)
    base = {
        "workspace": workspace,
        "kind": "tasks.fake_due",
        "title": "t",
        "data_class": "personal",
    }
    ProactiveNotice.objects.create(dedupe_key="old-read", read_at=old, **base)
    ProactiveNotice.objects.create(dedupe_key="old-dismissed", dismissed_at=old, **base)
    ProactiveNotice.objects.create(dedupe_key="recent-read", read_at=recent, **base)
    ProactiveNotice.objects.create(dedupe_key="unread", **base)
    assert prune_notices() == 2
    assert _keys() == ["recent-read", "unread"]


def test_a_morning_digest_run_writes_no_notice(owner, workspace, fake_probe):
    digest = make_schedule(workspace, owner, kind="morning_digest")
    fake_probe.items[workspace.pk] = [("a", "A")]
    run_schedule(digest)
    fake_probe.items[workspace.pk] = [("a", "A"), ("b", "B")]
    run = run_schedule(digest)
    assert ProactiveNotice.objects.count() == 0
    assert run.probe_counts == {fake_probe.spec.name: 2}
    assert run.model_usage_id is None
