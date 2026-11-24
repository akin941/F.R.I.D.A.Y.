"""The `assistant.followups_ready` probe (Phase 12 D1/D11, branch 4).

Follow-ups of async reads that ended in the last day and were written to a
conversation (`done` or `failed`): "Sonuç hazır: <tool label>" linked to the
conversation. The label is the read tool's Friday-authored label stored on
the follow-up -- never the result, never the model's reply. A `dropped`
follow-up (the conversation is gone) is never announced. The probe decides
nothing; the voice owner hears these through `notices.read`.
"""

from datetime import timedelta

from django.conf import settings

from assistant.models import PendingFollowUp
from core import probes

PROBE_NAME = "assistant.followups_ready"
WINDOW = timedelta(days=1)
Status = PendingFollowUp.Status


def collect(workspace, owner, now) -> probes.ProbeResult:
    ready = PendingFollowUp.objects.for_actor(owner, workspace=workspace).filter(
        status__in=[Status.DONE, Status.FAILED],
        followup_message__isnull=False,
        conversation__isnull=False,
        finished_at__gte=now - WINDOW,
    )
    rows = list(
        ready.order_by("-finished_at", "-id").values_list("id", "tool_label", "conversation_id")[
            : int(settings.PROACTIVE_PROBE_MAX_KEYS)
        ]
    )
    items = tuple(
        probes.ProbeItem(
            key=str(pk),
            label=probes.safe_label(label, "okuma"),
            link=f"/chat/{conversation_id}",
        )
        for pk, label, conversation_id in rows[: probes.MAX_ITEMS]
    )
    return probes.ProbeResult(
        count=ready.count(),
        data_class=probes.data_class_of(workspace),
        items=items,
        item_keys=tuple(str(pk) for pk, _, _ in rows),
    )


SPEC = probes.ProbeSpec(
    name=PROBE_NAME,
    owner_app="assistant",
    collect=collect,
    digest_section="followups",
    notice_kind=PROBE_NAME,
    link="/today",
    notice_template="Sonuç hazır: {label}",
)


def register():
    probes.register_probe(SPEC)
