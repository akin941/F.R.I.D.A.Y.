"""The e2e child (Phase 11 closeout): `friday-voice run` with the scripted
detector stack in place of the owner's pinned models, for the live
self-trigger scenario (`friday_voice.e2e`, scenario `selftrigger`).

    python -m friday_voice.e2e_child --wake-model-sha256 <hex>

Everything but the detectors is `run`'s: `run_stdio` on the real clock,
the HTTPS/loopback client, the thread worker and the REAL arming policy
(D11), which arms wake mode only when the server permits always-on for the
scripted detector's model hash. The detectors are the selftest's: an
energy VAD (fixture speech is tones, silence is zero), a turn-end detector
that always agrees, and a wake detector that fires once 400 ms of Friday's
first reply has played -- as if the wake model accepted whatever reached
it. Never installed as a console script; the Swift host launches `run`.
"""

import argparse
import os
import sys

from .reference.host import FRAME_MS
from .replay import FIXTURE_WAKE_MODEL_SHA256
from .selftest import WAKE_AFTER_MS


def make_pipeline(clock, model_sha256):
    from .arming import ArmingPolicy
    from .client import HttpFridayClient
    from .pipeline import VoicePipeline
    from .turn import EnergyVad, ScriptedTurnEnd
    from .wake import ScriptedWakeDetector
    from .workers import ThreadWorker

    holder = {"played_ms": 0, "pipeline": None}

    def scores(_index, _pcm):
        pipeline = holder["pipeline"]
        turn = pipeline.turn if pipeline is not None else None
        if turn is None or turn.number != 1 or not turn.playing:
            return 0
        holder["played_ms"] += FRAME_MS
        return 900 if holder["played_ms"] >= WAKE_AFTER_MS else 0

    def build(post):
        pipeline = VoicePipeline(
            clock,
            HttpFridayClient(),
            ThreadWorker(post),
            vad=EnergyVad(),
            turn_end=ScriptedTurnEnd([True]),
            wake_detector=ScriptedWakeDetector(scores, model_sha256=model_sha256),
            arming=ArmingPolicy(models_verified=True),
        )
        holder["pipeline"] = pipeline
        return pipeline

    return build


def main(argv=None) -> int:
    from .child import protect_stdout, run_stdio
    from .ports import RealClock

    parser = argparse.ArgumentParser(prog="friday_voice.e2e_child")
    parser.add_argument("--wake-model-sha256", default=FIXTURE_WAKE_MODEL_SHA256)
    args = parser.parse_args(argv)
    stdout = protect_stdout()
    clock = RealClock()
    code = run_stdio(
        sys.stdin.buffer,
        stdout,
        clock,
        make_pipeline=make_pipeline(clock, args.wake_model_sha256),
    )
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":  # pragma: no cover -- a child process
    main()
