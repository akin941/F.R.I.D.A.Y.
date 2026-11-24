"""Proactive runs (Phase 12 D3-D5, ADR-016).

- `ProactiveSchedule`: owner-made over the web only, a closed set of kinds,
  at most one row per (workspace, kind). `save()` raises inside an
  unattended context ("cron cannot create cron"); the runner's own
  bookkeeping (`next_run_at` claim, `last_run_at`) goes through
  `queryset.update()` outside the context.
- `ProactivePause`: one row per paused workspace; `save()` and `delete()`
  raise inside an unattended context too.
- `ProactiveRun`: one row per run -- probe counts, failures, slow probes,
  denials, outcome, duration, the `ModelUsage` id of its one call if any.
- `ProactiveNotice`: created only by runs from probe deltas; a partial
  unique constraint keeps at most one unread notice per key.
- `ProactiveProbeState`: the D5 delta baseline per (schedule, probe).
"""

import uuid

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel
from core.unattended import deny_if_unattended


class ProactiveSchedule(WorkspaceScopedModel, TimestampedModel):
    class Kind(models.TextChoices):
        MORNING_DIGEST = "morning_digest"
        HEARTBEAT = "heartbeat"

    class SkipReason(models.TextChoices):
        NONE = ""
        PAUSED = "paused"
        OWNER_INACTIVE = "owner_inactive"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    enabled = models.BooleanField(default=False)
    # morning_digest: local time (TIME_ZONE) and a weekday mask (bit 0 =
    # Monday ... bit 6 = Sunday).
    local_time = models.TimeField(null=True, blank=True)
    weekdays = models.PositiveSmallIntegerField(default=0b0011111)
    speak_on_open = models.BooleanField(default=False)
    # heartbeat: interval and quiet hours (local time, may wrap midnight).
    interval_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    quiet_start = models.TimeField(null=True, blank=True)
    quiet_end = models.TimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    # The last skip reason, so a pause / inactive owner is audited once per
    # transition, not once per beat.
    last_skip_reason = models.CharField(
        max_length=20, choices=SkipReason.choices, default="", blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "kind"], name="proactive_schedule_one_kind"
            )
        ]
        indexes = [models.Index(fields=["enabled", "next_run_at"])]

    def save(self, *args, **kwargs):
        deny_if_unattended("schedule_write")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        deny_if_unattended("schedule_write")
        return super().delete(*args, **kwargs)


class ProactivePause(WorkspaceScopedModel, TimestampedModel):
    paused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["workspace"], name="proactive_pause_one_per_workspace")
        ]

    def save(self, *args, **kwargs):
        deny_if_unattended("pause_write")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        deny_if_unattended("pause_write")
        return super().delete(*args, **kwargs)


class ProactiveRun(WorkspaceScopedModel, TimestampedModel):
    class Outcome(models.TextChoices):
        RUNNING = "running"
        QUIET = "quiet"
        NOTICES = "notices"
        DIGEST = "digest"
        DENIED = "denied"
        FAILED = "failed"

    schedule = models.ForeignKey(
        ProactiveSchedule, on_delete=models.SET_NULL, null=True, related_name="runs"
    )
    kind = models.CharField(max_length=20, choices=ProactiveSchedule.Kind.choices)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.RUNNING)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    # {probe name: count}; {probe name: closed code}; [probe name, ...].
    probe_counts = models.JSONField(default=dict, blank=True)
    probe_failures = models.JSONField(default=dict, blank=True)
    slow_probes = models.JSONField(default=list, blank=True)
    denials = models.PositiveIntegerField(default=0)
    notices_created = models.PositiveIntegerField(default=0)
    model_usage_id = models.UUIDField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["workspace", "-started_at"])]


class ProactiveNotice(WorkspaceScopedModel):
    """D5. `title` is a Friday-authored template filled with D1 labels;
    `link` is from the closed set in `core.probes.LINK_PATTERNS`."""

    class DataClass(models.TextChoices):
        FIRM = "firm"
        PERSONAL = "personal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(ProactiveRun, on_delete=models.SET_NULL, null=True, related_name="+")
    kind = models.CharField(max_length=64)
    dedupe_key = models.CharField(max_length=128)
    title = models.CharField(max_length=160)
    count = models.PositiveIntegerField(default=0)
    link = models.CharField(max_length=100, blank=True, default="")
    data_class = models.CharField(max_length=10, choices=DataClass.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "kind", "dedupe_key"],
                condition=models.Q(read_at__isnull=True, dismissed_at__isnull=True),
                name="proactive_notice_one_unread_per_key",
            )
        ]
        indexes = [models.Index(fields=["workspace", "-created_at"])]


class ProactiveProbeState(WorkspaceScopedModel):
    """D5 delta baseline: the item keys a probe reported on the schedule's
    previous completed run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(
        ProactiveSchedule, on_delete=models.CASCADE, related_name="probe_states"
    )
    probe = models.CharField(max_length=80)
    seen_keys = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["schedule", "probe"], name="proactive_probe_state_one")
        ]
