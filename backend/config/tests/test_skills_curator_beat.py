"""Phase 12 D16: the weekly skill curator is a static entry on the compose
worker beat, Sunday 04:15 in the app timezone (after the 8B reflection at
03:30), and its task is registered."""

from config.celery import app as celery_app
from config.celery import build_beat_schedule


def test_the_curator_runs_sunday_0415_on_the_default_beat_and_is_registered():
    default = build_beat_schedule(runner_mode=False)
    entry = default["skills-curator-weekly"]
    assert entry["task"] == "skills.tasks.curate"
    schedule = entry["schedule"]
    assert schedule.day_of_week == {0} and schedule.hour == {4} and schedule.minute == {15}
    reflection = default["memory-learning-weekly-reflection"]["schedule"]
    assert (min(reflection.hour), min(reflection.minute)) < (4, 15)
    runner = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert entry["task"] not in runner
    celery_app.loader.import_default_modules()
    assert "skills.tasks.curate" in celery_app.tasks
