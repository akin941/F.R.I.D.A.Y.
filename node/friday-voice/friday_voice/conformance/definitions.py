"""The `friday-voice/v1` conformance cases and frame vectors, defined once
here and rendered to JSON by `build.py` (brief D6). The JSON files are what
the Swift host's test target loads (Mac session M1); `friday-voice
conformance --check` fails when they drift from these definitions.

Two families:
- **host** cases: the host under test (the reference host here, the Swift
  `VoiceChildHost` later) driven by a scripted hostile child;
- **child** cases: the child under test (`friday_voice.child`, later with
  the pipeline behind it) driven by a scripted hostile host.

Step vocabulary (every frame is base64 of its exact bytes, so malformed
frames are expressible):

host family
  child_send {frame_b64}        the hostile child writes these bytes
  child_close_stdout            the child's stdout ends (it exits)
  child_exit {code}             the child process exits
  host_local {event, value}     a local host event: link up/down, mute
                                true/false/"unreadable", mode, ptt down/up
  host_capture {frames}         the host captures N silent 20 ms frames
  advance_ms {ms}               time passes (the host ticks; a due restart
                                spawns the next scripted child)
  expect {mic?, link?, indicator?, failure?, restart_in_ms?,
          dropped_play?, to_child?}
                                `to_child` is the exact sequence of frame
                                types the host wrote since the previous
                                expect

child family
  host_send {frame_b64}         the hostile host writes these bytes
  close_stdin                   the host closes the child's stdin
  advance_ms {ms}               time passes (manual clock only)
  expect_frames {frames}        the exact frames the child wrote since the
                                previous expectation (type + listed fields)
  expect_exit {code}            the child ended with this exit code
  expect_running                the child has not ended
  expect_buffered {bytes}       audio held in memory (in-process runs only)

A case with `"clock": "manual"` needs a manual clock (watchdog, backoff,
keepalive, expiry) and is skipped by the live, real-time runs.
"""

import base64

from ..protocol.framing import LENGTH, encode_frame, raw_frame
from ..protocol.messages import (
    CHILD_TO_HOST,
    HOST_TO_CHILD,
    MAX_FRAME_BYTES,
    PROTOCOL,
)

CASES_FORMAT = 1
SILENCE_20MS = b"\x00" * 640

CREDENTIAL = {
    "type": "credential",
    "token": "jpat_" + "c0nf0rmanceT0ken" * 2,
    "expires_at": "2099-01-01T00:00:00Z",
    "server_url": "https://friday.example",
    "ca_sha256": "ab" * 32,
}
# The manual clock starts at 2027-01-15T08:00:00Z (ports.ManualClock).
CREDENTIAL_10S = {**CREDENTIAL, "expires_at": "2027-01-15T08:00:10Z"}
CREDENTIAL_EXPIRED = {**CREDENTIAL, "expires_at": "2000-01-01T00:00:00Z"}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hello(**overrides):
    return {
        "type": "hello",
        "protocol": PROTOCOL,
        "host_version": "1.0.0",
        "sample_rate": 16000,
        "mode": "push_to_talk",
        "muted": False,
        "link_up": True,
        **overrides,
    }


READY = {"type": "ready", "protocol": PROTOCOL, "child_version": "0.1.0"}


def state(name="armed_ptt", **overrides):
    return {
        "type": "state",
        "state": name,
        "mode": "push_to_talk",
        "session_open": False,
        "wake_accepts_today": 0,
        "wake_discards_today": 0,
        "storm_mutes_today": 0,
        "at_ms": 1_800_000_000_000,
        **overrides,
    }


def play(utterance_id, seq, *, last=False, pcm=SILENCE_20MS):
    return encode_frame(
        {"type": "play", "utterance_id": utterance_id, "seq": seq, "last": last},
        pcm,
        direction=CHILD_TO_HOST,
    )


def audio(pcm=SILENCE_20MS, seq=0):
    return encode_frame(
        {"type": "audio", "seq": seq, "captured_at_ms": 1_800_000_000_000, "aec": "on"},
        pcm,
        direction=HOST_TO_CHILD,
    )


OVERSIZE_PREFIX = LENGTH.pack(MAX_FRAME_BYTES + 1)
EMPTY_PREFIX = LENGTH.pack(0)


# --- host family ---


def child_send(frame, describe=""):
    if isinstance(frame, dict):
        frame = encode_frame(frame, direction=CHILD_TO_HOST)
    step = {"op": "child_send", "frame_b64": b64(frame)}
    if describe:
        step["describe"] = describe
    return step


def expect(**fields):
    return {"op": "expect", **fields}


HOST_PRELUDE = [
    expect(mic="released", link="open", to_child=["hello", "credential"]),
    child_send(READY),
    child_send(state("armed_ptt")),
    expect(mic="held", link="open", indicator="armed_ptt", to_child=[]),
]


def _dropped(failure, restart_in_ms=1000):
    return expect(
        mic="released",
        link="dropped",
        indicator="off",
        failure=failure,
        restart_in_ms=restart_in_ms,
        to_child=[],
    )


def host_case(case_id, title, steps, *, clock="any", host=None):
    return {
        "format": CASES_FORMAT,
        "protocol": PROTOCOL,
        "family": "host",
        "id": case_id,
        "title": title,
        "clock": clock,
        "host": host or {"mode": "push_to_talk", "muted": False, "link_up": True},
        "steps": steps,
    }


def _exit_backoff_steps():
    steps = [*HOST_PRELUDE]
    for backoff in (1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000):
        steps += [
            {"op": "child_exit", "code": 1},
            expect(
                mic="released",
                link="dropped",
                indicator="off",
                failure="child_exit",
                restart_in_ms=backoff,
            ),
            {"op": "advance_ms", "ms": backoff - 1},
            expect(link="dropped", to_child=[]),
            {"op": "advance_ms", "ms": 1},
            expect(mic="released", link="open", to_child=["hello", "credential"]),
        ]
    return steps


def host_cases():
    return [
        host_case(
            "host.handshake",
            "The host greets with hello + credential and holds the mic only after the "
            "child reports an armed state",
            [*HOST_PRELUDE],
        ),
        host_case(
            "host.oversize_frame",
            "A length prefix above 1 MiB is refused before its body is read",
            [*HOST_PRELUDE, child_send(OVERSIZE_PREFIX), _dropped("frame_too_large")],
        ),
        host_case(
            "host.empty_frame",
            "A zero-length frame is a protocol error",
            [*HOST_PRELUDE, child_send(EMPTY_PREFIX), _dropped("frame_empty")],
        ),
        host_case(
            "host.unknown_type",
            "A type outside the frozen child->host set (open_url) is refused",
            [
                *HOST_PRELUDE,
                child_send(raw_frame({"type": "open_url", "url": "https://evil.example"})),
                _dropped("unknown_type"),
            ],
        ),
        host_case(
            "host.policy_shaped_type",
            "A policy-shaped type name (policy.set) is refused",
            [
                *HOST_PRELUDE,
                child_send(raw_frame({"type": "policy.set", "mode": "always_on"})),
                _dropped("unknown_type"),
            ],
        ),
        host_case(
            "host.policy_shaped_property",
            "A policy-shaped property on a known type (state.config) is refused",
            [
                *HOST_PRELUDE,
                child_send(raw_frame({**state(), "config": {"mode": "always_on"}})),
                _dropped("schema_invalid"),
            ],
        ),
        host_case(
            "host.host_type_from_child",
            "The child cannot send a host->child type (mode): it cannot change the mic mode",
            [
                *HOST_PRELUDE,
                child_send(raw_frame({"type": "mode", "mode": "always_on"})),
                _dropped("unknown_type"),
            ],
        ),
        host_case(
            "host.play_unknown_utterance",
            "play for an unknown, closed or out-of-order utterance is dropped and counted; "
            "the link stays up",
            [
                *HOST_PRELUDE,
                child_send(play("u-never-opened", 3)),
                expect(mic="held", link="open", dropped_play=1, to_child=[]),
                child_send(play("u1", 0)),
                expect(dropped_play=1, to_child=["playback"]),
                child_send(play("u1", 2)),
                expect(link="open", dropped_play=2, to_child=[]),
                child_send(play("u1", 1, last=True)),
                expect(dropped_play=2, to_child=["playback"]),
                child_send(play("u1", 2)),
                child_send(play("u1", 0)),
                expect(mic="held", link="open", dropped_play=4, to_child=[]),
            ],
        ),
        host_case(
            "host.play_odd_pcm",
            "PCM of odd length is a protocol error",
            [
                *HOST_PRELUDE,
                child_send(
                    raw_frame(
                        {
                            "type": "play",
                            "utterance_id": "u1",
                            "seq": 0,
                            "last": True,
                            "pcm_bytes": 3,
                        },
                        b"\x00\x01\x02",
                    )
                ),
                _dropped("pcm_odd_length"),
            ],
        ),
        host_case(
            "host.play_length_mismatch",
            "pcm_bytes must equal the payload length",
            [
                *HOST_PRELUDE,
                child_send(
                    raw_frame(
                        {
                            "type": "play",
                            "utterance_id": "u1",
                            "seq": 0,
                            "last": True,
                            "pcm_bytes": 4,
                        },
                        b"\x00\x01",
                    )
                ),
                _dropped("payload_length_mismatch"),
            ],
        ),
        host_case(
            "host.invalid_utf8",
            "A header that is not UTF-8 is refused",
            [
                *HOST_PRELUDE,
                child_send(raw_frame(b'{"type":"st\xffate"}')),
                _dropped("invalid_utf8"),
            ],
        ),
        host_case(
            "host.duplicate_key",
            "A header with a duplicate key is refused",
            [
                *HOST_PRELUDE,
                child_send(raw_frame(b'{"state":"off","state":"armed_wake","type":"state"}')),
                _dropped("duplicate_key"),
            ],
        ),
        host_case(
            "host.float_number",
            "Numbers are integers only",
            [
                *HOST_PRELUDE,
                child_send(raw_frame(b'{"at_ms":1.5,"type":"state"}')),
                _dropped("number_not_integer"),
            ],
        ),
        host_case(
            "host.ready_expected",
            "Anything before ready is a protocol error",
            [
                expect(mic="released", link="open", to_child=["hello", "credential"]),
                child_send(state("armed_ptt")),
                _dropped("ready_expected"),
            ],
        ),
        host_case(
            "host.version_unsupported",
            "A ready for another protocol version is refused",
            [
                expect(mic="released", link="open", to_child=["hello", "credential"]),
                child_send(raw_frame({**READY, "protocol": "friday-voice/v2"})),
                _dropped("version_unsupported"),
            ],
        ),
        host_case(
            "host.duplicate_ready",
            "A second ready is a protocol error",
            [*HOST_PRELUDE, child_send(READY), _dropped("duplicate_ready")],
        ),
        host_case(
            "host.watchdog",
            "No state for 10 s releases the mic, shows off and restarts the child after 1 s",
            [
                *HOST_PRELUDE,
                {"op": "advance_ms", "ms": 9999},
                expect(mic="held", link="open", to_child=[]),
                {"op": "advance_ms", "ms": 1},
                _dropped("watchdog"),
                {"op": "advance_ms", "ms": 1000},
                expect(mic="released", link="open", to_child=["hello", "credential"]),
                child_send(READY),
                child_send(state("armed_ptt")),
                expect(mic="held", link="open", indicator="armed_ptt", to_child=[]),
            ],
            clock="manual",
        ),
        host_case(
            "host.state_keeps_watchdog_quiet",
            "A state every few seconds keeps the watchdog quiet",
            [
                *HOST_PRELUDE,
                {"op": "advance_ms", "ms": 9000},
                child_send(state("armed_ptt")),
                {"op": "advance_ms", "ms": 9000},
                child_send(state("armed_ptt")),
                {"op": "advance_ms", "ms": 9000},
                expect(mic="held", link="open", failure=None, to_child=[]),
            ],
            clock="manual",
        ),
        host_case(
            "host.child_exit_backoff",
            "Child exit releases the mic and restarts it with backoff 1 s doubling to 60 s",
            _exit_backoff_steps(),
            clock="manual",
        ),
        host_case(
            "host.child_stdout_short_read",
            "The child's stdout ending inside a frame is a short read",
            [
                *HOST_PRELUDE,
                child_send(encode_frame(state(), direction=CHILD_TO_HOST)[:9]),
                {"op": "child_close_stdout"},
                _dropped("short_read"),
            ],
        ),
        host_case(
            "host.child_release_mic",
            "release_mic from the child releases the mic; the link stays up",
            [
                *HOST_PRELUDE,
                child_send({"type": "release_mic", "reason": "storm"}),
                expect(mic="released", link="open", to_child=[]),
                child_send(state("storm_muted")),
                expect(mic="released", link="open", indicator="storm_muted", to_child=[]),
                child_send(state("armed_ptt")),
                expect(mic="held", indicator="armed_ptt", to_child=[]),
            ],
        ),
        host_case(
            "host.local_link_down",
            "The node WebSocket going down releases the mic at once, whatever the child says",
            [
                *HOST_PRELUDE,
                {"op": "host_local", "event": "link", "value": "down"},
                expect(mic="released", link="open", indicator="off", to_child=["link"]),
                child_send(state("armed_ptt")),
                expect(mic="released", indicator="off", to_child=[]),
                {"op": "host_local", "event": "link", "value": "up"},
                expect(mic="released", to_child=["link"]),
                child_send(state("armed_ptt")),
                expect(mic="held", indicator="armed_ptt", to_child=[]),
            ],
        ),
        host_case(
            "host.local_mute_unreadable",
            "An unreadable mute switch is muted: the mic is released whatever the child says",
            [
                *HOST_PRELUDE,
                {"op": "host_local", "event": "mute", "value": "unreadable"},
                expect(mic="released", indicator="muted", to_child=["mute"]),
                child_send(state("armed_ptt")),
                expect(mic="released", indicator="muted", to_child=[]),
                {"op": "host_local", "event": "mute", "value": False},
                child_send(state("armed_ptt")),
                expect(mic="held", indicator="armed_ptt", to_child=["mute"]),
            ],
        ),
        host_case(
            "host.local_mute_on",
            "Muting releases the mic whatever the child says",
            [
                *HOST_PRELUDE,
                {"op": "host_local", "event": "mute", "value": True},
                expect(mic="released", indicator="muted", to_child=["mute"]),
                child_send(state("listening")),
                expect(mic="released", indicator="muted", to_child=[]),
            ],
        ),
        host_case(
            "host.local_mode_off",
            "Mode off releases the mic whatever the child says",
            [
                *HOST_PRELUDE,
                {"op": "host_local", "event": "mode", "value": "off"},
                expect(mic="released", indicator="off", to_child=["mode"]),
                child_send(state("armed_ptt")),
                expect(mic="released", indicator="off", to_child=[]),
            ],
        ),
        host_case(
            "host.capture_only_while_held",
            "Capture frames reach the child only while the mic is held",
            [
                expect(mic="released", to_child=["hello", "credential"]),
                {"op": "host_capture", "frames": 2},
                expect(mic="released", to_child=[]),
                child_send(READY),
                child_send(state("armed_ptt")),
                expect(mic="held", to_child=[]),
                {"op": "host_capture", "frames": 2},
                expect(mic="held", to_child=["audio", "audio"]),
                child_send({"type": "release_mic", "reason": "error"}),
                expect(mic="released", to_child=[]),
                {"op": "host_capture", "frames": 2},
                expect(mic="released", to_child=[]),
            ],
        ),
        host_case(
            "host.ptt_relayed",
            "The push-to-talk hotkey reaches the child as ptt frames",
            [
                *HOST_PRELUDE,
                {"op": "host_local", "event": "ptt", "value": "down"},
                {"op": "host_local", "event": "ptt", "value": "up"},
                expect(mic="held", to_child=["ptt", "ptt"]),
            ],
        ),
        host_case(
            "host.stop_playback",
            "stop_playback closes an open utterance; later play for it is dropped",
            [
                *HOST_PRELUDE,
                child_send(play("u1", 0)),
                expect(to_child=["playback"]),
                child_send({"type": "stop_playback", "utterance_id": "u1"}),
                expect(to_child=["playback"], dropped_play=0),
                child_send(play("u1", 1)),
                child_send({"type": "stop_playback", "utterance_id": "u-unknown"}),
                expect(mic="held", link="open", dropped_play=1, to_child=[]),
            ],
        ),
    ]


# --- child family ---


def host_send(frame, describe=""):
    if isinstance(frame, dict):
        frame = encode_frame(frame, direction=HOST_TO_CHILD)
    step = {"op": "host_send", "frame_b64": b64(frame)}
    if describe:
        step["describe"] = describe
    return step


def frames(*items):
    return {"op": "expect_frames", "frames": list(items)}


def child_case(case_id, title, steps, *, clock="any"):
    return {
        "format": CASES_FORMAT,
        "protocol": PROTOCOL,
        "family": "child",
        "id": case_id,
        "title": title,
        "clock": clock,
        "steps": steps,
    }


CHILD_PRELUDE = [
    host_send(hello()),
    frames(),
    host_send(CREDENTIAL),
    frames({"type": "ready", "protocol": PROTOCOL}, {"type": "state", "state": "armed_ptt"}),
]

EXIT_PROTOCOL = 2


def _protocol_error(code):
    return [
        frames(
            {"type": "log", "code": f"protocol.{code}"},
            {"type": "release_mic", "reason": "protocol_error"},
        ),
        {"op": "expect_exit", "code": EXIT_PROTOCOL},
    ]


def child_cases():
    return [
        child_case(
            "child.nothing_before_credential",
            "The child sends nothing before the first valid credential, whatever arrives",
            [
                host_send(hello()),
                frames(),
                host_send(audio()),
                host_send({"type": "ptt", "key": "down"}),
                host_send({"type": "mute", "muted": False}),
                host_send({"type": "link", "state": "up"}),
                frames(),
                {"op": "expect_buffered", "bytes": 0},
                host_send(CREDENTIAL),
                frames(
                    {"type": "ready", "protocol": PROTOCOL},
                    {"type": "state", "state": "armed_ptt"},
                ),
                {"op": "expect_running"},
            ],
        ),
        child_case(
            "child.expired_credential_is_missing",
            "A credential that has already expired counts as no credential",
            [
                host_send(hello()),
                host_send(CREDENTIAL_EXPIRED),
                frames(),
                host_send(CREDENTIAL),
                frames(
                    {"type": "ready", "protocol": PROTOCOL},
                    {"type": "state", "state": "armed_ptt"},
                ),
            ],
        ),
        child_case(
            "child.hello_expected",
            "The first frame must be hello; the child stops without sending anything",
            [host_send(CREDENTIAL), frames(), {"op": "expect_exit", "code": EXIT_PROTOCOL}],
        ),
        child_case(
            "child.version_unsupported",
            "A hello for another protocol version stops the child silently",
            [
                host_send(raw_frame(hello(protocol="friday-voice/v2"))),
                frames(),
                {"op": "expect_exit", "code": EXIT_PROTOCOL},
            ],
        ),
        child_case(
            "child.duplicate_hello",
            "A second hello is a protocol error",
            [*CHILD_PRELUDE, host_send(hello()), *_protocol_error("duplicate_hello")],
        ),
        child_case(
            "child.link_down_releases",
            "Node WebSocket down: drop buffers, release the mic, report off",
            [
                *CHILD_PRELUDE,
                host_send(audio()),
                host_send({"type": "link", "state": "down"}),
                frames(
                    {"type": "release_mic", "reason": "link_down"},
                    {"type": "state", "state": "off"},
                ),
                {"op": "expect_buffered", "bytes": 0},
                host_send(audio()),
                frames(),
                {"op": "expect_buffered", "bytes": 0},
                host_send({"type": "link", "state": "up"}),
                frames({"type": "state", "state": "armed_ptt"}),
            ],
        ),
        child_case(
            "child.mute_releases",
            "Mute on: drop buffers, release the mic, report muted",
            [
                *CHILD_PRELUDE,
                host_send(audio()),
                host_send({"type": "mute", "muted": True}),
                frames(
                    {"type": "release_mic", "reason": "muted"}, {"type": "state", "state": "muted"}
                ),
                {"op": "expect_buffered", "bytes": 0},
                host_send(audio()),
                {"op": "expect_buffered", "bytes": 0},
                host_send({"type": "mute", "muted": False}),
                frames({"type": "state", "state": "armed_ptt"}),
            ],
        ),
        child_case(
            "child.mute_unreadable_releases",
            "An unreadable mute is muted: release the mic",
            [
                *CHILD_PRELUDE,
                host_send({"type": "mute", "muted": "unreadable"}),
                frames(
                    {"type": "release_mic", "reason": "mute_unreadable"},
                    {"type": "state", "state": "muted"},
                ),
                {"op": "expect_buffered", "bytes": 0},
            ],
        ),
        child_case(
            "child.mode_off_releases",
            "Mode off: release the mic, report off",
            [
                *CHILD_PRELUDE,
                host_send({"type": "mode", "mode": "off"}),
                frames(
                    {"type": "release_mic", "reason": "mode_off"}, {"type": "state", "state": "off"}
                ),
            ],
        ),
        child_case(
            "child.always_on_not_armed",
            "Always-on is not armed in this protocol shell: the arming policy is not passed",
            [
                host_send(hello(mode="always_on")),
                host_send(CREDENTIAL),
                frames(
                    {"type": "ready", "protocol": PROTOCOL},
                    {"type": "state", "state": "armed_ptt", "mode": "always_on"},
                ),
            ],
        ),
        child_case(
            "child.starts_muted",
            "A host that starts muted or with its link down gets a non-armed state",
            [
                host_send(hello(muted="unreadable", link_up=False)),
                host_send(CREDENTIAL),
                frames({"type": "ready"}, {"type": "state", "state": "muted"}),
                host_send(audio()),
                frames(),
                {"op": "expect_buffered", "bytes": 0},
            ],
        ),
        child_case(
            "child.ptt_listening",
            "The push-to-talk key opens and closes listening",
            [
                *CHILD_PRELUDE,
                host_send({"type": "ptt", "key": "down"}),
                frames({"type": "state", "state": "listening"}),
                host_send(audio()),
                frames(),
                host_send({"type": "ptt", "key": "up"}),
                frames({"type": "state", "state": "armed_ptt"}),
            ],
        ),
        child_case(
            "child.oversize_frame",
            "An over-length host frame: log, release, stop",
            [*CHILD_PRELUDE, host_send(OVERSIZE_PREFIX), *_protocol_error("frame_too_large")],
        ),
        child_case(
            "child.unknown_host_type",
            "A type outside the frozen host->child set: log, release, stop",
            [
                *CHILD_PRELUDE,
                host_send(raw_frame({"type": "open_url", "url": "https://evil.example"})),
                *_protocol_error("unknown_type"),
            ],
        ),
        child_case(
            "child.policy_shaped_host_type",
            "A policy-shaped type from the host is refused like any unknown type",
            [
                *CHILD_PRELUDE,
                host_send(raw_frame({"type": "config.set", "wake_threshold": 1})),
                *_protocol_error("unknown_type"),
            ],
        ),
        child_case(
            "child.child_type_from_host",
            "The host cannot send a child->host type",
            [*CHILD_PRELUDE, host_send(raw_frame(READY)), *_protocol_error("unknown_type")],
        ),
        child_case(
            "child.odd_pcm",
            "Capture PCM of odd length is a protocol error",
            [
                *CHILD_PRELUDE,
                host_send(
                    raw_frame(
                        {
                            "type": "audio",
                            "seq": 0,
                            "captured_at_ms": 1,
                            "aec": "on",
                            "pcm_bytes": 3,
                        },
                        b"\x00\x01\x02",
                    )
                ),
                *_protocol_error("pcm_odd_length"),
            ],
        ),
        child_case(
            "child.schema_invalid",
            "A known type with an extra property is refused",
            [
                *CHILD_PRELUDE,
                host_send(raw_frame({"type": "mute", "muted": False, "hotkey": "F5"})),
                *_protocol_error("schema_invalid"),
            ],
        ),
        child_case(
            "child.stdin_eof",
            "stdin EOF: release the mic and exit",
            [
                *CHILD_PRELUDE,
                {"op": "close_stdin"},
                frames({"type": "release_mic", "reason": "stdin_closed"}),
                {"op": "expect_exit", "code": 0},
            ],
        ),
        child_case(
            "child.stdin_short_read",
            "stdin ending inside a frame is a protocol error",
            [
                *CHILD_PRELUDE,
                host_send(
                    encode_frame({"type": "ptt", "key": "down"}, direction=HOST_TO_CHILD)[:7]
                ),
                {"op": "close_stdin"},
                *_protocol_error("short_read"),
            ],
        ),
        child_case(
            "child.shutdown",
            "shutdown: release the mic and exit",
            [
                *CHILD_PRELUDE,
                host_send({"type": "shutdown"}),
                frames({"type": "release_mic", "reason": "shutdown"}),
                {"op": "expect_exit", "code": 0},
            ],
        ),
        child_case(
            "child.state_keepalive",
            "A state frame at least every 5 s keeps the host watchdog quiet",
            [
                *CHILD_PRELUDE,
                {"op": "advance_ms", "ms": 4999},
                frames(),
                {"op": "advance_ms", "ms": 1},
                frames({"type": "state", "state": "armed_ptt"}),
            ],
            clock="manual",
        ),
        child_case(
            "child.credential_expiry",
            "A credential that expires while running releases the mic",
            [
                host_send(hello()),
                host_send(CREDENTIAL_10S),
                frames({"type": "ready"}, {"type": "state", "state": "armed_ptt"}),
                {"op": "advance_ms", "ms": 4000},
                frames(),
                {"op": "advance_ms", "ms": 6000},
                frames(
                    {"type": "release_mic", "reason": "credential_expired"},
                    {"type": "state", "state": "error"},
                ),
                host_send(CREDENTIAL),
                frames({"type": "state", "state": "armed_ptt"}),
            ],
            clock="manual",
        ),
    ]


# --- frame vectors ---


def _valid(name, direction, header, payload=b""):
    frame = encode_frame(header, payload, direction=direction)
    decoded = dict(header)
    if header["type"] in ("audio", "play"):
        decoded["pcm_bytes"] = len(payload)
    return {
        "name": name,
        "direction": direction,
        "header": decoded,
        "payload_b64": b64(payload),
        "frame_b64": b64(frame),
    }


def _invalid(name, direction, frame, error):
    return {"name": name, "direction": direction, "frame_b64": b64(frame), "error": error}


def frame_vectors():
    pcm = bytes(range(256)) * 2 + b"\x10\x27" * 64  # 640 bytes, even
    valid = [
        _valid("hello", HOST_TO_CHILD, hello()),
        _valid("hello_muted_unreadable", HOST_TO_CHILD, hello(muted="unreadable", link_up=False)),
        _valid("credential", HOST_TO_CHILD, CREDENTIAL),
        _valid(
            "credential_loopback_dev",
            HOST_TO_CHILD,
            {**CREDENTIAL, "server_url": "http://127.0.0.1:8001", "ca_sha256": None},
        ),
        _valid(
            "audio",
            HOST_TO_CHILD,
            {"type": "audio", "seq": 7, "captured_at_ms": 1_800_000_000_020, "aec": "on"},
            pcm,
        ),
        _valid(
            "playback",
            HOST_TO_CHILD,
            {"type": "playback", "utterance_id": "u-1", "state": "started", "at_ms": 5},
        ),
        _valid("mode", HOST_TO_CHILD, {"type": "mode", "mode": "always_on"}),
        _valid("mute", HOST_TO_CHILD, {"type": "mute", "muted": True}),
        _valid("ptt", HOST_TO_CHILD, {"type": "ptt", "key": "down"}),
        _valid("link", HOST_TO_CHILD, {"type": "link", "state": "down"}),
        _valid("shutdown", HOST_TO_CHILD, {"type": "shutdown"}),
        _valid("ready", CHILD_TO_HOST, READY),
        _valid("state", CHILD_TO_HOST, state("speaking", session_open=True, wake_accepts_today=3)),
        _valid(
            "play",
            CHILD_TO_HOST,
            {"type": "play", "utterance_id": "u-1", "seq": 0, "last": False},
            pcm,
        ),
        _valid(
            "play_empty_last",
            CHILD_TO_HOST,
            {"type": "play", "utterance_id": "u-1", "seq": 1, "last": True},
        ),
        _valid("stop_playback", CHILD_TO_HOST, {"type": "stop_playback", "utterance_id": "u-1"}),
        _valid("earcon", CHILD_TO_HOST, {"type": "earcon", "earcon": "wake"}),
        _valid("release_mic", CHILD_TO_HOST, {"type": "release_mic", "reason": "link_down"}),
        _valid(
            "log_non_ascii",
            CHILD_TO_HOST,
            {"type": "log", "code": "http.status", "detail": "çağrı 429 — ışık"},
        ),
    ]
    ok_state = encode_frame(state(), direction=CHILD_TO_HOST)
    invalid = [
        _invalid("empty_frame", CHILD_TO_HOST, EMPTY_PREFIX, "frame_empty"),
        _invalid("over_length", CHILD_TO_HOST, OVERSIZE_PREFIX, "frame_too_large"),
        _invalid("short_prefix", CHILD_TO_HOST, b"\x00\x00", "short_read"),
        _invalid("short_body", CHILD_TO_HOST, ok_state[:-3], "short_read"),
        _invalid(
            "unterminated_header", CHILD_TO_HOST, LENGTH.pack(4) + b"{}{}", "header_unterminated"
        ),
        _invalid("invalid_utf8", CHILD_TO_HOST, raw_frame(b'{"type":"\xc3"}'), "invalid_utf8"),
        _invalid("invalid_json", CHILD_TO_HOST, raw_frame(b'{"type":'), "invalid_json"),
        _invalid("nan", CHILD_TO_HOST, raw_frame(b'{"at_ms":NaN,"type":"state"}'), "invalid_json"),
        _invalid(
            "duplicate_key",
            CHILD_TO_HOST,
            raw_frame(b'{"type":"earcon","type":"earcon","earcon":"wake"}'),
            "duplicate_key",
        ),
        _invalid(
            "float", CHILD_TO_HOST, raw_frame(b'{"at_ms":0.5,"type":"state"}'), "number_not_integer"
        ),
        _invalid("header_array", CHILD_TO_HOST, raw_frame(b'["state"]'), "header_not_object"),
        _invalid(
            "odd_pcm",
            HOST_TO_CHILD,
            raw_frame(
                {"type": "audio", "seq": 0, "captured_at_ms": 0, "aec": "on", "pcm_bytes": 1},
                b"\x00",
            ),
            "pcm_odd_length",
        ),
        _invalid(
            "pcm_length_mismatch",
            CHILD_TO_HOST,
            raw_frame(
                {"type": "play", "utterance_id": "u", "seq": 0, "last": True, "pcm_bytes": 2},
                b"\x00\x00\x00\x00",
            ),
            "payload_length_mismatch",
        ),
        _invalid(
            "pcm_too_large",
            CHILD_TO_HOST,
            raw_frame(
                {"type": "play", "utterance_id": "u", "seq": 0, "last": True, "pcm_bytes": 65538},
                b"\x00" * 65538,
            ),
            "pcm_too_large",
        ),
        _invalid(
            "payload_on_control_type",
            HOST_TO_CHILD,
            raw_frame({"type": "ptt", "key": "down"}, b"\x00\x00"),
            "payload_unexpected",
        ),
        _invalid("unknown_type", CHILD_TO_HOST, raw_frame({"type": "open_url"}), "unknown_type"),
        _invalid("wrong_direction", HOST_TO_CHILD, raw_frame(READY), "unknown_type"),
        _invalid(
            "extra_property",
            CHILD_TO_HOST,
            raw_frame({"type": "earcon", "earcon": "wake", "volume": 11}),
            "schema_invalid",
        ),
        _invalid(
            "enum_violation",
            CHILD_TO_HOST,
            raw_frame({"type": "earcon", "earcon": "siren"}),
            "schema_invalid",
        ),
        _invalid(
            "version",
            HOST_TO_CHILD,
            raw_frame(hello(protocol="friday-voice/v2")),
            "version_unsupported",
        ),
        _invalid(
            "token_shape",
            HOST_TO_CHILD,
            raw_frame({**CREDENTIAL, "token": "jnd_node-secret-never-here"}),
            "schema_invalid",
        ),
        _invalid(
            "server_url_plain_http",
            HOST_TO_CHILD,
            raw_frame({**CREDENTIAL, "server_url": "http://friday.example"}),
            "schema_invalid",
        ),
    ]
    return {
        "format": CASES_FORMAT,
        "protocol": PROTOCOL,
        "max_frame_bytes": MAX_FRAME_BYTES,
        "max_pcm_bytes": 65536,
        "valid": valid,
        "invalid": invalid,
    }
