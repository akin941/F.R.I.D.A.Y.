"""Phase 6 A5: the voice-trace prune is on the default (compose worker)
beat, not the runner's (lives in config/tests: kernel-app tests may not
import the project config)."""

from config.celery import build_beat_schedule


def test_voice_trace_prune_is_on_the_default_beat():
    default = {entry["task"] for entry in build_beat_schedule(runner_mode=False).values()}
    runner = {entry["task"] for entry in build_beat_schedule(runner_mode=True).values()}
    assert "voice.tasks.prune_voice_turn_traces_task" in default
    assert "voice.tasks.prune_voice_turn_traces_task" not in runner
