from celery import shared_task

from tasks.services import purge_expired_trashed_tasks


@shared_task
def purge_expired_trashed_tasks_task():
    """Daily beat job (config/celery.py, ADR-009): permanently delete tasks
    that have sat in the trash longer than TRASH_RETENTION_DAYS."""
    return purge_expired_trashed_tasks()
