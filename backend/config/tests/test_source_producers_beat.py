"""Phase 8A branch 4 (brief D5): the git producer runs on the runner beat and
queue (it clones with the runner's credentials); the calendar producer on
the compose beat, like every connector read."""

from config.celery import build_beat_schedule


def test_commit_ingestion_runs_on_the_runner_beat_and_queue(settings):
    default = {e["task"] for e in build_beat_schedule(runner_mode=False).values()}
    runner_beat = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert "harness.tasks.ingest_repo_commits" in runner_beat
    assert "harness.tasks.ingest_repo_commits" not in default
    assert settings.CELERY_TASK_ROUTES["harness.tasks.*"]["queue"] == "harness"


def test_calendar_ingestion_runs_on_the_default_beat():
    default = {e["task"] for e in build_beat_schedule(runner_mode=False).values()}
    runner_beat = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert "connectors.tasks.ingest_calendar_metadata" in default
    assert "connectors.tasks.ingest_calendar_metadata" not in runner_beat
