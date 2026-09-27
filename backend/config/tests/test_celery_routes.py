"""Celery task routing (Phase 3 branch 8)."""


def test_harness_tasks_are_routed_to_the_runner_queue(settings):
    """Phase 3 branch 8: only a runner-host worker (with Docker) consumes
    harness tasks; the compose worker listens on the default queue only."""
    from config.celery import app

    route = app.amqp.router.route({}, "harness.tasks.run_harness_implementation_job")
    assert route["queue"].name == settings.HARNESS_CELERY_QUEUE == "harness"
    other = app.amqp.router.route({}, "memory.tasks.extract_memories")
    assert other["queue"].name == "celery"


def test_harness_scans_are_scheduled_only_by_the_runner_beat():
    """Phase 3 branch 8 review P1: the compose beat never publishes harness
    scans (they'd pile up unconsumed while no runner is up); the runner's
    own beat schedules only them."""
    from config.celery import build_beat_schedule

    default = build_beat_schedule(runner_mode=False)
    runner = build_beat_schedule(runner_mode=True)
    assert runner and all(e["task"].startswith("harness.tasks.") for e in runner.values())
    assert not any(e["task"].startswith("harness.tasks.") for e in default.values())
    assert default
