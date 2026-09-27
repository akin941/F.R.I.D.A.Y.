"""ADR-009: both trash retention purges are on beat (lives in config/tests:
kernel-app tests may not import the project config)."""

from config.celery import build_beat_schedule


def test_beat_schedules_both_retention_purges():
    tasks = {entry["task"] for entry in build_beat_schedule(runner_mode=False).values()}
    assert "tasks.tasks.purge_expired_trashed_tasks_task" in tasks
    assert "knowledge.tasks.purge_expired_trashed_documents_task" in tasks
