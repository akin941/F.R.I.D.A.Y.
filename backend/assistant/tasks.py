"""Assistant Celery tasks: the daily `self` block (Phase 8A D8) and async
read follow-ups (Phase 12 D11)."""

from celery import shared_task

from assistant.self_block import refresh_self_block


@shared_task
def assemble_self_block_task():
    """Daily: re-assemble the deterministic `self` block (no model call).
    Returns whether the block changed."""
    _block, changed = refresh_self_block()
    return changed


@shared_task
def compose_followup_task(followup_id):
    """Phase 12 D11: compose one async read's follow-up (at most one
    `chat.followup` completion; see `assistant.followups`). Never retried."""
    from assistant.followups import compose_followup

    status = compose_followup(followup_id)
    return str(status) if status else None


@shared_task
def recover_followups_task():
    """Every minute: wake follow-ups whose read ended without a notification
    and fail claims whose worker was lost (never a second completion)."""
    from assistant.followups import recover_followups

    return recover_followups()
