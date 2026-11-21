"""Phase 8B branch 8 (brief D13): the transcript pull runs on the compose
worker beat every MEMORY_TRANSCRIPT_PULL_MINUTES (15), never on the runner
beat (the node is reached from the worker, like every node read)."""

from config.celery import build_beat_schedule


def test_the_transcript_pull_is_on_the_worker_beat_every_15_minutes(settings):
    worker = build_beat_schedule(runner_mode=False)
    runner = build_beat_schedule(runner_mode=True)
    entry = worker["memory-pull-transcripts"]
    assert entry["task"] == "tools.tasks.pull_transcripts_task"
    assert entry["schedule"] == settings.MEMORY_TRANSCRIPT_PULL_MINUTES * 60.0 == 900.0
    assert "memory-pull-transcripts" not in runner
