"""Assistant beat tasks (Phase 8A D8)."""

from celery import shared_task

from assistant.self_block import refresh_self_block


@shared_task
def assemble_self_block_task():
    """Daily: re-assemble the deterministic `self` block (no model call).
    Returns whether the block changed."""
    _block, changed = refresh_self_block()
    return changed
