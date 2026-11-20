"""Phase 8A D8: the deterministic `self` block is re-assembled daily on the
compose worker's beat (not the runner's)."""

from config.celery import build_beat_schedule


def test_self_assembly_runs_on_the_compose_beat_daily():
    default = build_beat_schedule(runner_mode=False)
    entry = default["assistant-self-block-assembly"]
    assert entry["task"] == "assistant.tasks.assemble_self_block_task"
    assert entry["schedule"] == 24 * 60 * 60.0
    assert "assistant-self-block-assembly" not in build_beat_schedule(runner_mode=True)
