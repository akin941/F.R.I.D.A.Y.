"""Celery task routing (Phase 3 branch 8)."""


def test_harness_tasks_are_routed_to_the_runner_queue(settings):
    """Phase 3 branch 8: only a runner-host worker (with Docker) consumes
    harness tasks; the compose worker listens on the default queue only."""
    from config.celery import app

    route = app.amqp.router.route({}, "harness.tasks.run_harness_implementation_job")
    assert route["queue"].name == settings.HARNESS_CELERY_QUEUE == "harness"
    other = app.amqp.router.route({}, "memory.tasks.extract_memories")
    assert other["queue"].name == "celery"
