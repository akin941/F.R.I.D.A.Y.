"""Celery entry points of the `skills` app (Phase 12).

`distill_playbooks` is the nightly playbook distiller (D17): a fixed system
task with a static beat entry (`config/celery.py`), never an owner schedule.
Each repo's call runs inside its own unattended context
(`skills.playbooks.distill_repo`).
"""

from celery import shared_task

from skills import playbooks


@shared_task
def distill_playbooks():
    return playbooks.distill_playbooks()
