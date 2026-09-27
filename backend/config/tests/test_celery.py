"""Celery app wiring (config/celery.py). Lives in config's own tests: kernel
apps' tests may not import the project config (import-linter)."""


def test_harness_tasks_are_routed_to_the_runner_queue(settings):
    """Phase 3 branch 8: only a runner-host worker (with Docker) consumes
    harness tasks; the compose worker listens on the default queue only."""
    from config.celery import app

    route = app.amqp.router.route({}, "harness.tasks.run_harness_implementation_job")
    assert route["queue"].name == settings.HARNESS_CELERY_QUEUE == "harness"
    other = app.amqp.router.route({}, "memory.tasks.extract_memories")
    assert other["queue"].name == "celery"


def test_token_flush_runs_daily_from_beat():
    """Phase 4 branch 1: the refresh-token blacklist tables are flushed."""
    from config.celery import app

    entry = app.conf.beat_schedule["jwt-flush-expired-tokens"]
    assert entry["task"] == "accounts.tasks.flush_expired_tokens"
    assert entry["schedule"] == 24 * 60 * 60.0
