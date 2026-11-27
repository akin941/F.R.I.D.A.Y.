"""Record the reference host's side of the barge-in and echo scripts for the
Swift host's differential test (Phase 11 Mac session M1 contract test 5;
runbook B7).

`friday-voice replay` plays `node/friday-voice/fixtures/barge_in.json` with
the reference host's paced playback (`HostCore(paced_playback=True)`), its
EchoDouble and the real child. This script runs the same scenarios with a
recording `HostCore` and writes, per scenario, every call the host received
(the bytes the child wrote, decoded to headers and payload lengths; ticks;
capture frames; push-to-talk) with the clock at that moment, and every batch
of frames the host wrote back. `VoiceHostTraceTests` replays the calls into
`VoiceHostCore` on a manual clock and requires the same frames, the same
`stopped_at` times and the same end state: the part of barge-in the host is
responsible for (paced playback, stop within the frame, the mic) is then
exactly the reference's. The child's behaviour (detection, cancellation,
pre-roll) stays pinned by node/friday-voice/tests/test_barge_in.py.

    cd node/friday-voice
    uv run python ../friday-node/scripts/voice_host_traces.py           # write
    uv run python ../friday-node/scripts/voice_host_traces.py --check   # fail on drift

Codes, counts and frame headers only: no audio is stored (PCM payloads are
recorded as lengths; the Swift side feeds zeros of that length).
"""

import argparse
import json
import sys
from pathlib import Path

from friday_voice import replay
from friday_voice.protocol.framing import FrameDecoder, decode_frame
from friday_voice.reference.host import HostCore

OUTPUT = Path(__file__).resolve().parents[1] / "fixtures" / "voice_host_traces.json"


def _frames(data):
    frames, error = FrameDecoder().feed(data)
    if error:
        raise RuntimeError(f"the real child wrote a bad frame: {error}")
    return [[header, len(payload)] for header, payload in frames]


def _header(frame):
    header, _payload = decode_frame(frame)
    return header


class RecordingHostCore(HostCore):
    """Records every external call (nested calls, such as start_child's own
    send_credential, are part of the outer one)."""

    calls: list

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self._depth = 0

    def _record(self, op, *args):
        if self._depth == 0:
            self.calls.append([op, self.clock.monotonic_ms(), self.clock.now_ms(), *args])

    def _wrap(self, op, args, call):
        self._record(op, *args)
        self._depth += 1
        try:
            return call()
        finally:
            self._depth -= 1

    def start_child(self):
        return self._wrap("start_child", [], super().start_child)

    def send_credential(self, credential):
        return self._wrap(
            "send_credential",
            [credential],
            lambda: super(RecordingHostCore, self).send_credential(credential),
        )

    def child_bytes(self, data):
        return self._wrap(
            "child_bytes", [_frames(data)], lambda: super(RecordingHostCore, self).child_bytes(data)
        )

    def child_stream_closed(self):
        return self._wrap("child_stream_closed", [], super().child_stream_closed)

    def child_exited(self, code=0):
        return self._wrap(
            "child_exited", [code], lambda: super(RecordingHostCore, self).child_exited(code)
        )

    def tick(self):
        return self._wrap("tick", [], super().tick)

    def feed_audio(self, pcm, *, aec="on"):
        return self._wrap(
            "feed_audio",
            [len(pcm), aec],
            lambda: super(RecordingHostCore, self).feed_audio(pcm, aec=aec),
        )

    def set_link(self, up):
        return self._wrap(
            "set_link", [bool(up)], lambda: super(RecordingHostCore, self).set_link(up)
        )

    def set_mute(self, muted):
        return self._wrap(
            "set_mute", [muted], lambda: super(RecordingHostCore, self).set_mute(muted)
        )

    def set_mode(self, mode):
        return self._wrap("set_mode", [mode], lambda: super(RecordingHostCore, self).set_mode(mode))

    def ptt(self, key):
        return self._wrap("ptt", [key], lambda: super(RecordingHostCore, self).ptt(key))

    def shutdown(self):
        return self._wrap("shutdown", [], super().shutdown)

    def take_outbound(self):
        frames = super().take_outbound()
        if frames and self._depth == 0:
            self.calls.append(["out", [_header(frame) for frame in frames]])
        return frames


def record():
    original = replay.HostCore
    hosts = []

    class Capture(RecordingHostCore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            hosts.append(self)

    replay.HostCore = Capture
    traces = []
    try:
        for scenario in replay.load_barge_in_scenarios():
            hosts.clear()
            result = replay.run_barge_in_scenario(scenario)
            if result["failures"]:
                raise RuntimeError(f"{scenario['id']}: {result['failures']}")
            (host,) = hosts
            traces.append(
                {
                    "id": scenario["id"],
                    "paced_playback": host.paced_playback,
                    "mode": "push_to_talk",
                    "credential": replay.DEV_CREDENTIAL,
                    "calls": host.calls,
                    "end": {
                        "stopped_at": [list(item) for item in host.stopped_at],
                        "mic_held": host.mic_held,
                        "indicator": host.indicator,
                        "dropped_play": host.dropped_play,
                        "dropped_stop": host.dropped_stop,
                        "audio_sent": host.audio_sent,
                        "releases": host.releases,
                        "earcons": host.earcons,
                        "failures": [code for code, _at in host.failures],
                    },
                    "result": {
                        k: result[k] for k in ("stopped", "chat_turns", "discarded", "stop_ms")
                    },
                }
            )
    finally:
        replay.HostCore = original
    return {
        "$comment": (
            "Generated by node/friday-node/scripts/voice_host_traces.py from "
            "node/friday-voice/fixtures/barge_in.json -- do not edit."
        ),
        "format": 1,
        "traces": traces,
    }


def render():
    return json.dumps(record(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT.name} is out of date; run {Path(__file__).name}")
            return 1
        print("voice host traces are current")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
