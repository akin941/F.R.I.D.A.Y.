"""The child's protocol shell (brief D6, D12): fail-closed rules, the ring
buffer bound, keepalive and the stdio runner."""

import io
import os
import subprocess
import sys
import threading

import pytest

from friday_voice.child import (
    RING_BUFFER_BYTES,
    STATE_KEEPALIVE_MS,
    ChildCore,
    ChildLink,
    run_stdio,
)
from friday_voice.conformance.definitions import CREDENTIAL, audio, hello
from friday_voice.ports import ManualClock
from friday_voice.protocol.framing import decode_frame, encode_frame, read_frame
from friday_voice.protocol.messages import CHILD_TO_HOST, HOST_TO_CHILD


def _frame(header, payload=b""):
    return encode_frame(header, payload, direction=HOST_TO_CHILD)


def _headers(frames):
    return [decode_frame(frame, direction=CHILD_TO_HOST)[0] for frame in frames]


@pytest.fixture
def running():
    clock = ManualClock()
    core = ChildCore(clock, log_stream=io.StringIO())
    link = ChildLink(core)
    assert link.feed(_frame(hello())) == []
    out = _headers(link.feed(_frame(CREDENTIAL)))
    assert [h["type"] for h in out] == ["ready", "state"]
    return clock, core, link


def test_the_ring_buffer_never_holds_more_than_two_seconds(running):
    _clock, core, link = running
    for _ in range(200):  # 4 s of 20 ms frames
        link.feed(audio())
    assert len(core.ring) == RING_BUFFER_BYTES


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        ({"type": "link", "state": "down"}, "link_down"),
        ({"type": "mute", "muted": True}, "muted"),
        ({"type": "mute", "muted": "unreadable"}, "mute_unreadable"),
        ({"type": "mode", "mode": "off"}, "mode_off"),
        ({"type": "shutdown"}, "shutdown"),
    ],
)
def test_every_fail_closed_input_drops_buffers_and_releases(running, header, reason):
    _clock, core, link = running
    link.feed(_frame({"type": "ptt", "key": "down"}))
    for _ in range(10):
        link.feed(audio())
    assert core.buffered_bytes() > 0
    out = _headers(link.feed(_frame(header)))
    assert out[0] == {"type": "release_mic", "reason": reason}
    assert core.buffered_bytes() == 0
    link.feed(audio())
    assert core.buffered_bytes() == 0


def test_a_repeated_fail_closed_input_releases_again(running):
    _clock, _core, link = running
    for _ in range(2):
        out = _headers(link.feed(_frame({"type": "mute", "muted": True})))
        assert out[0]["type"] == "release_mic"


def test_keepalive_state_is_sent_inside_the_watchdog(running):
    clock, _core, link = running
    clock.advance(STATE_KEEPALIVE_MS - 1)
    assert link.tick() == []
    clock.advance(1)
    assert [h["type"] for h in _headers(link.tick())] == ["state"]


def test_state_carries_the_voice_state_fields(running):
    _clock, _core, link = running
    (header,) = _headers(link.feed(_frame({"type": "ptt", "key": "down"})))
    assert header == {
        "type": "state",
        "state": "listening",
        "mode": "push_to_talk",
        "session_open": False,
        "wake_accepts_today": 0,
        "wake_discards_today": 0,
        "storm_mutes_today": 0,
        "at_ms": ManualClock.DEFAULT_EPOCH_MS,
    }


def test_ptt_while_not_consuming_never_listens(running):
    _clock, core, link = running
    link.feed(_frame({"type": "mute", "muted": True}))
    assert link.feed(_frame({"type": "ptt", "key": "down"})) == []
    assert not core.ptt_down


def test_the_credential_is_held_in_memory_only_and_never_echoed(running):
    _clock, core, link = running
    rotated = {**CREDENTIAL, "token": "jpat_" + "R0tated" * 4}
    out = link.feed(_frame(rotated))
    assert core.token == rotated["token"]
    assert all(rotated["token"].encode() not in frame for frame in out)


def _pipes():
    host_to_child_r, host_to_child_w = os.pipe()
    child_to_host_r, child_to_host_w = os.pipe()
    return (
        os.fdopen(host_to_child_r, "rb", buffering=0),
        os.fdopen(host_to_child_w, "wb", buffering=0),
        os.fdopen(child_to_host_r, "rb", buffering=0),
        os.fdopen(child_to_host_w, "wb", buffering=0),
    )


def test_run_stdio_serves_a_host_and_exits_on_eof():
    child_in, host_out, host_in, child_out = _pipes()
    result = {}
    core = ChildCore(ManualClock(), log_stream=io.StringIO())
    thread = threading.Thread(
        target=lambda: result.update(
            code=run_stdio(child_in, child_out, core.clock, core=core, tick_seconds=0.01)
        )
    )
    thread.start()
    host_out.write(_frame(hello()) + _frame(CREDENTIAL))
    assert read_frame(host_in, direction=CHILD_TO_HOST)[0]["type"] == "ready"
    assert read_frame(host_in, direction=CHILD_TO_HOST)[0]["type"] == "state"
    host_out.close()
    assert read_frame(host_in, direction=CHILD_TO_HOST)[0] == {
        "type": "release_mic",
        "reason": "stdin_closed",
    }
    thread.join(timeout=5)
    assert result == {"code": 0}


def test_run_stdio_stops_on_a_host_protocol_error():
    child_in, host_out, host_in, child_out = _pipes()
    result = {}
    core = ChildCore(ManualClock(), log_stream=io.StringIO())
    thread = threading.Thread(
        target=lambda: result.update(
            code=run_stdio(child_in, child_out, core.clock, core=core, tick_seconds=0.01)
        )
    )
    thread.start()
    host_out.write(_frame(hello()) + _frame(CREDENTIAL))
    read_frame(host_in)
    read_frame(host_in)
    host_out.write(b"\x00\x20\x00\x01")  # over-length prefix
    headers = [read_frame(host_in, direction=CHILD_TO_HOST)[0] for _ in range(2)]
    assert headers == [
        {"type": "log", "code": "protocol.frame_too_large", "detail": ""},
        {"type": "release_mic", "reason": "protocol_error"},
    ]
    thread.join(timeout=5)
    assert result == {"code": 2}


def test_protect_stdout_keeps_stray_prints_off_the_protocol_stream():
    script = (
        "from friday_voice.child import protect_stdout\n"
        "out = protect_stdout()\n"
        "print('stray library output')\n"
        "out.write(b'FRAME')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=30, check=True
    )
    assert done.stdout == b"FRAME"
    assert b"stray library output" in done.stderr
