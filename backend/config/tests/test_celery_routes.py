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
    assert runner and all(
        e["task"].startswith(("harness.tasks.", "deploys.tasks.")) for e in runner.values()
    )
    assert not any(e["task"].startswith("harness.tasks.") for e in default.values())
    assert default


def test_deploy_tasks_are_routed_to_the_deploy_queue(settings):
    """Phase 5 branch 6 (brief D10): deploy runs, status refreshes and the
    recovery scan run only on the runner, which consumes `-Q
    harness,deploy` and alone holds DEPLOY_SSH_KEY_PATH."""
    from config.celery import app

    for task in (
        "deploys.tasks.run_deploy",
        "deploys.tasks.refresh_deploy_target_status",
        "deploys.tasks.recover_stale_deploy_runs",
    ):
        route = app.amqp.router.route({}, task)
        assert route["queue"].name == settings.DEPLOY_CELERY_QUEUE == "deploy"


def test_deploy_scan_is_scheduled_only_by_the_runner_beat():
    from config.celery import build_beat_schedule

    runner = {e["task"] for e in build_beat_schedule(runner_mode=True).values()}
    default = {e["task"] for e in build_beat_schedule(runner_mode=False).values()}
    assert "deploys.tasks.recover_stale_deploy_runs" in runner
    assert not any(task.startswith("deploys.tasks.") for task in default)
