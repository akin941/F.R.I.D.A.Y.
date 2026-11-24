"""Celery entry points of the `skills` app (Phase 12).

Both are fixed system tasks with static beat entries (`config/celery.py`),
never owner schedules; nothing creates or edits them at run time.

- `skills.tasks.curate` (D16): Sunday 04:15 `TIME_ZONE`, after the 8B weekly
  reflection at 03:30. The curator it runs can only propose
  (`skills.curator`).
- `distill_playbooks` (D17): the nightly playbook distiller. Each repo's call
  runs inside its own unattended context (`skills.playbooks.distill_repo`).
"""

from celery import shared_task

from skills import curator, playbooks


@shared_task(name="skills.tasks.curate")
def curate():
    """One curator pass per workspace with usable skills, at most once per
    workspace per ISO week."""
    return curator.curate_all()


@shared_task
def distill_playbooks():
    return playbooks.distill_playbooks()
