"""Celery tasks of the `skills` app (Phase 12 D16).

`skills.tasks.curate` is a static beat entry (`config/celery.py`, Sunday
04:15 `TIME_ZONE`, after the 8B weekly reflection at 03:30): a fixed
system task, not an owner schedule. Nothing creates or edits it at run
time, and the curator it runs can only propose (`skills.curator`).
"""

from celery import shared_task

from skills import curator


@shared_task(name="skills.tasks.curate")
def curate():
    """One curator pass per workspace with usable skills, at most once per
    workspace per ISO week."""
    return curator.curate_all()
