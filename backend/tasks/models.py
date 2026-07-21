"""Manual to-do items (MVP 4 §1).

Postgres is the source of truth; tasks are workspace-scoped, auditable and
plain CRUD. `due_date` is a calendar day, not an instant, so "is this due
today" is a date comparison against the client's local date, never a
timezone-sensitive one computed server-side (see the Tasks/Today UI branch).

MVP 6 §3 adds `source` (below) — a formal, on-the-record amendment to MVP 4's
Open Decisions entry (`docs/MASTER_PLAN.md` §O), not a silent reinterpretation
of it: that entry's actual goal (never let an AI-drafted task pass as an
ordinary one; never silently change what old tasks are eligible for) is fully
met by this field + its backfill. Its literal mechanism (route task creation
through the tools/approval-gate machinery like a gated tool call) is
deliberately narrowed to NOT apply here — ADR-004 gates write/delete/deploy/
external-send-class actions, and creating a `Task` row in one's own workspace
(fully reversible, self-scoped) isn't in that risk class. See
`docs/prompts/mvp-06/brief.md` §3 for the full reasoning.
"""

from django.conf import settings
from django.db import models

from core.db import WorkspaceScopedModel
from core.models import TimestampedModel


class Task(WorkspaceScopedModel, TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open"
        DONE = "done"
        CANCELLED = "cancelled"

    class Priority(models.TextChoices):
        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"

    class Source(models.TextChoices):
        USER_CREATED = "user_created"
        AI_SUGGESTED = "ai_suggested"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    due_date = models.DateField(null=True, blank=True)
    # Provenance (MVP 6 §3, mirrors Memory.Source's exact vocabulary).
    # Server-controlled only: TaskCreateSerializer never accepts this as a
    # client-asserted value — it is set internally, only when the create
    # request carries a verified `draft_message_id` reference (see
    # tasks/serializers.py). Every pre-MVP-6 row backfills to `user_created`
    # (this migration's default), and nothing here changes the "own tasks"
    # role predicate, which stays `created_by = actor` regardless of source.
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.USER_CREATED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks_created",
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["workspace", "status"]),
            models.Index(fields=["workspace", "due_date"]),
        ]

    def __str__(self):
        return f"task {self.id} ({self.status})"
