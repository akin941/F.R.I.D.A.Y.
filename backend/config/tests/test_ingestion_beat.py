"""Phase 8A branch 1 (d10): the episode TTL purge runs on the compose beat."""

from config.celery import build_beat_schedule


def test_episode_ttl_purge_is_scheduled_on_the_default_beat():
    default = {e["task"] for e in build_beat_schedule(runner_mode=False).values()}
    runner = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    assert "memory.ingestion.tasks.purge_expired_episodes_task" in default
    assert "memory.ingestion.tasks.purge_expired_episodes_task" not in runner
