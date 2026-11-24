"""One proactive run (Phase 12 D2, D4, D5).

A run is two steps. **Collect**: run the schedule kind's probes --
deterministic DB reads, no model call, no network. **Speak**: only for
`morning_digest`, only when a probe found something (the composition and
its one `proactive.digest` gateway call land with branch 2). A `heartbeat`
run never calls a model: it turns probe deltas into notices with
Friday-authored templates.

Everything inside the run executes in `core.unattended.unattended_run`:
every effect's owning service refuses (`UnattendedDenied`), the gateway
refuses every purpose outside the kind's allowlist (none for heartbeat),
and Celery dispatch outside `PROACTIVE_UNATTENDED_TASKS` is refused. The
run writes only its own rows -- `ProactiveRun`, `ProactiveNotice`,
`ProactiveProbeState` -- plus audit.

Transactions: the runner is not wrapped in an outer `atomic()`. Each probe
runs in its own `atomic()` block (so a denial or a failure rolls back only
that probe's work), and the `DenialRecorder` is flushed after every
probe's block has ended and again in a `finally`, so every denial is
audited exactly once whatever the probe's transaction did.
"""

import logging
import time as clock

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.unattended import DenialRecorder
from core import probes
from core.unattended import UnattendedDenied, unattended_run, unattended_step
from proactive.models import (
    ProactiveNotice,
    ProactiveProbeState,
    ProactiveRun,
    ProactiveSchedule,
)

logger = logging.getLogger(__name__)

# D2/D18: the gateway purposes each kind may call while unattended.
ALLOWED_PURPOSES = {
    ProactiveSchedule.Kind.HEARTBEAT: frozenset(),
    ProactiveSchedule.Kind.MORNING_DIGEST: frozenset({"proactive.digest"}),
}
# Friday-authored label for the notice that groups new, unlabelled keys.
OVERFLOW_LABEL = "diğer öğeler"
OVERFLOW_KEY = "overflow"


def workspace_data_class(workspace) -> str:
    return probes.DATA_CLASS_PERSONAL if workspace.type == "personal" else probes.DATA_CLASS_FIRM


def probes_for(kind):
    """`heartbeat`: the probes that emit notices; `morning_digest`: all."""
    specs = probes.list_probes()
    if kind == ProactiveSchedule.Kind.HEARTBEAT:
        return [spec for spec in specs if spec.notice_kind]
    return specs


def _max_keys():
    return int(settings.PROACTIVE_PROBE_MAX_KEYS)


def _notify_delta(*, run, schedule, spec, result):
    """D5: notify only `item_keys - seen_keys`, then replace the baseline.
    The first run for a (schedule, probe) pair records the baseline and
    notifies nothing. Returns the number of notices created."""
    keys = list(result.all_keys())
    state = ProactiveProbeState.objects.filter(schedule=schedule, probe=spec.name).first()
    if state is None:
        ProactiveProbeState.objects.create(
            workspace=schedule.workspace, schedule=schedule, probe=spec.name, seen_keys=keys
        )
        return 0
    seen = set(state.seen_keys or [])
    new_keys = [key for key in keys if key not in seen]
    if keys != list(state.seen_keys or []):
        state.seen_keys = keys
        state.save(update_fields=["seen_keys", "updated_at"])
    if not new_keys:
        return 0
    labelled = {item.key: item for item in result.items}
    created = 0
    unlabelled = [key for key in new_keys if key not in labelled]
    entries = [
        (key, labelled[key].label, labelled[key].link) for key in new_keys if key in labelled
    ]
    if unlabelled:
        entries.append((OVERFLOW_KEY, OVERFLOW_LABEL, ""))
    for key, label, link in entries:
        title = probes.render_notice_title(spec, label=label, count=result.count)
        try:
            with transaction.atomic():
                ProactiveNotice.objects.create(
                    workspace=schedule.workspace,
                    run=run,
                    kind=spec.notice_kind,
                    dedupe_key=key,
                    title=title,
                    count=result.count,
                    link=link or spec.link,
                    data_class=result.data_class,
                )
            created += 1
        except IntegrityError:
            # The partial unique constraint: an unread notice for this key
            # exists already (a racing run). One is enough.
            continue
    return created


def _collect_one(*, run, schedule, spec, now, owner):
    """Run one probe in its own transaction. Returns (count, notices)."""
    with transaction.atomic():
        result = spec.collect(schedule.workspace, owner, now)
        result = probes.validate_result(spec, result, max_keys=_max_keys())
        if result.data_class != workspace_data_class(schedule.workspace):
            raise probes.ProbeResultInvalid("data_class_mismatch")
        notices = 0
        if schedule.kind == ProactiveSchedule.Kind.HEARTBEAT and spec.notice_kind:
            notices = _notify_delta(run=run, schedule=schedule, spec=spec, result=result)
        return result.count, notices


def compose_digest(*, run, schedule, counts):
    """The Speak step (branch 2: `DigestRun`, one number-checked
    `proactive.digest` call, template fallback). Until then a
    `morning_digest` run collects only. Returns the outcome or None."""
    return None


def run_schedule(schedule, *, now=None):
    """Execute one claimed schedule. The caller has already advanced
    `next_run_at` (outside the context); this writes `last_run_at` after
    the context has exited, through `queryset.update()`. Returns the
    `ProactiveRun`."""
    now = now or timezone.now()
    workspace = schedule.workspace
    owner = workspace.owner
    started = clock.monotonic()
    run = ProactiveRun.objects.create(
        workspace=workspace, schedule=schedule, kind=schedule.kind, started_at=now
    )
    recorder = DenialRecorder(workspace=workspace, source=f"proactive.{schedule.kind}")
    counts, failures, slow = {}, {}, []
    denials = notices = 0
    outcome = None
    timeout_ms = int(settings.PROACTIVE_PROBE_TIMEOUT_MS)
    try:
        with unattended_run(
            str(run.id),
            allowed_purposes=ALLOWED_PURPOSES.get(schedule.kind, frozenset()),
            on_denied=recorder,
        ):
            for spec in probes_for(schedule.kind):
                probe_started = clock.monotonic()
                with unattended_step(spec.name):
                    try:
                        count, created = _collect_one(
                            run=run, schedule=schedule, spec=spec, now=now, owner=owner
                        )
                        counts[spec.name] = count
                        notices += created
                    except UnattendedDenied:
                        denials += 1
                        failures[spec.name] = "denied"
                    except probes.ProbeResultInvalid as exc:
                        failures[spec.name] = f"probe_invalid:{exc}"[:60]
                        logger.warning("Probe %s returned an invalid result", spec.name)
                    except Exception as exc:
                        failures[spec.name] = "probe_failed"
                        logger.warning("Probe %s failed (%s)", spec.name, exc.__class__.__name__)
                elapsed_ms = int((clock.monotonic() - probe_started) * 1000)
                if elapsed_ms > timeout_ms:
                    slow.append(spec.name)
                recorder.flush()
            if schedule.kind == ProactiveSchedule.Kind.MORNING_DIGEST and any(counts.values()):
                try:
                    outcome = compose_digest(run=run, schedule=schedule, counts=counts)
                except UnattendedDenied:
                    denials += 1
                    failures["digest"] = "denied"
    except UnattendedDenied:
        denials += 1
        outcome = ProactiveRun.Outcome.DENIED
    except Exception as exc:
        logger.warning("Proactive run %s failed (%s)", run.id, exc.__class__.__name__)
        outcome = ProactiveRun.Outcome.FAILED
    finally:
        recorder.flush()
    if outcome is None:
        if notices:
            outcome = ProactiveRun.Outcome.NOTICES
        elif denials:
            outcome = ProactiveRun.Outcome.DENIED
        elif failures and not counts:
            outcome = ProactiveRun.Outcome.FAILED
        else:
            outcome = ProactiveRun.Outcome.QUIET
    finished = timezone.now()
    ProactiveRun.objects.filter(pk=run.pk).update(
        outcome=outcome,
        finished_at=finished,
        duration_ms=int((clock.monotonic() - started) * 1000),
        probe_counts=counts,
        probe_failures=failures,
        slow_probes=slow,
        denials=denials,
        notices_created=notices,
    )
    ProactiveSchedule.objects.filter(pk=schedule.pk).update(last_run_at=finished)
    run.refresh_from_db()
    return run
