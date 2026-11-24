"""The child's session state machine (Phase 11 brief D9, D6 `state`).

Two parts, both pure:

- `derive_state(...)` is the one place that turns the shell's blocking
  rules and the pipeline's turn into the `state` the child reports. The
  precedence is fixed: a mute (or an unreadable mute) is `muted`; a lost
  link or mode `off` is `off`; a missing or expired credential is `error`;
  then the pipeline's own states (`storm_muted`, `error` after a refused
  credential or within the error hold, `listening` / `thinking` /
  `speaking` while a turn runs), else `armed_wake` or `armed_ptt`.
- `TRANSITIONS` is the exhaustive transition table: for every state of
  D6's `state` message and every host input the child can receive, the
  state it reports next and the frames (other than `state`) it sends, in
  order. `tests/test_session.py` drives the real child (`ChildCore` +
  `VoicePipeline` behind the reference host, manual clock) into each
  state, applies each input and compares with this table, so the table is
  the specification and the code cannot drift from it.

The inputs are host frames (D6) plus three audio shapes: `speech` is
300 ms of frames the VAD scores as speech, `silence` one quiet frame,
`wake` one frame the wake detector scores at the threshold. Worker
events (transcripts, replies, refusals) are pipeline transitions pinned
by `tests/test_pipeline.py`. `exit` means the child finished (shutdown).
"""

from types import MappingProxyType

from .protocol.messages import STATES

EXIT = "exit"

INPUTS = (
    "mute_on",
    "mute_unreadable",
    "mute_off",
    "link_down",
    "link_up",
    "mode_off",
    "mode_ptt",
    "mode_always_on",
    "ptt_down",
    "ptt_up",
    "speech",
    "silence",
    "wake",
    "playback_ended",
    "credential",
    "shutdown",
)

# Turn phases (VoicePipeline) as the state machine reads them.
PHASE_CAPTURING = "capturing"


def derive_state(
    *,
    blocking_reason=None,
    storm_muted=False,
    refused=False,
    error_hold=False,
    turn_phase=None,
    playing=False,
    wake_armed=False,
    ptt_down=False,
):
    """The `state` the child reports (see the module docstring)."""
    if blocking_reason in ("muted", "mute_unreadable"):
        return "muted"
    if blocking_reason in ("link_down", "mode_off"):
        return "off"
    if blocking_reason is not None:
        return "error"
    if storm_muted:
        return "storm_muted"
    if refused or error_hold:
        return "error"
    if turn_phase is not None:
        if turn_phase == PHASE_CAPTURING:
            return "listening"
        return "speaking" if playing else "thinking"
    if ptt_down:
        return "listening"
    return "armed_wake" if wake_armed else "armed_ptt"


R = ("release_mic",)
S = ("stop_playback",)
SR = ("stop_playback", "release_mic")
NONE = ()


def _row(**cells):
    missing = set(INPUTS) - set(cells)
    extra = set(cells) - set(INPUTS)
    if missing or extra:
        raise ValueError(f"row is not exhaustive: missing {missing}, extra {extra}")
    return MappingProxyType(dict(cells))


TRANSITIONS = MappingProxyType(
    {
        "armed_ptt": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("armed_ptt", NONE),
            link_down=("off", R),
            link_up=("armed_ptt", NONE),
            mode_off=("off", R),
            mode_ptt=("armed_ptt", NONE),
            mode_always_on=("armed_wake", NONE),
            ptt_down=("listening", NONE),
            ptt_up=("armed_ptt", NONE),
            speech=("armed_ptt", NONE),
            silence=("armed_ptt", NONE),
            wake=("armed_ptt", NONE),  # not armed: the detector is never asked
            playback_ended=("armed_ptt", NONE),  # not ours: ignored
            credential=("armed_ptt", NONE),
            shutdown=(EXIT, R),
        ),
        "armed_wake": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("armed_wake", NONE),
            link_down=("off", R),
            link_up=("armed_wake", NONE),
            mode_off=("off", R),
            mode_ptt=("armed_ptt", NONE),
            mode_always_on=("armed_wake", NONE),
            ptt_down=("listening", NONE),  # push-to-talk works in always-on too
            ptt_up=("armed_wake", NONE),
            speech=("armed_wake", NONE),  # speech without the wake word: nothing
            silence=("armed_wake", NONE),
            wake=("listening", ("earcon",)),
            playback_ended=("armed_wake", NONE),
            credential=("armed_wake", NONE),
            shutdown=(EXIT, R),
        ),
        "listening": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("listening", NONE),
            link_down=("off", R),
            link_up=("listening", NONE),
            mode_off=("off", R),
            mode_ptt=("listening", NONE),
            mode_always_on=("listening", NONE),
            ptt_down=("listening", NONE),  # a fresh capture
            ptt_up=("armed_ptt", NONE),  # shorter than PTT_MIN_MS: a slip, nothing uploaded
            speech=("listening", NONE),
            silence=("listening", NONE),
            wake=("listening", NONE),
            playback_ended=("listening", NONE),
            credential=("listening", NONE),
            shutdown=(EXIT, R),
        ),
        "thinking": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("thinking", NONE),
            link_down=("off", R),
            link_up=("thinking", NONE),
            mode_off=("off", R),
            mode_ptt=("thinking", NONE),
            mode_always_on=("thinking", NONE),
            ptt_down=("listening", NONE),  # the turn is cut, a new capture starts
            ptt_up=("thinking", NONE),
            speech=("thinking", NONE),  # nothing is playing: no barge-in
            silence=("thinking", NONE),
            wake=("thinking", NONE),
            playback_ended=("thinking", NONE),
            credential=("thinking", NONE),
            shutdown=(EXIT, R),
        ),
        "speaking": _row(
            mute_on=("muted", SR),
            mute_unreadable=("muted", SR),
            mute_off=("speaking", NONE),
            link_down=("off", SR),
            link_up=("speaking", NONE),
            mode_off=("off", SR),
            mode_ptt=("speaking", NONE),
            mode_always_on=("speaking", NONE),
            ptt_down=("listening", S),  # the hotkey cuts the reply (R3 fallback)
            ptt_up=("speaking", NONE),
            speech=("listening", S),  # barge-in (D9)
            silence=("speaking", NONE),
            wake=("speaking", NONE),  # one frame is not 250 ms of speech; wake not armed
            playback_ended=("thinking", NONE),  # the reply stream is still open
            credential=("speaking", NONE),
            shutdown=(EXIT, SR),
        ),
        "muted": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("armed_ptt", NONE),
            link_down=("muted", R),
            link_up=("muted", NONE),
            mode_off=("muted", R),
            mode_ptt=("muted", NONE),
            mode_always_on=("muted", NONE),
            ptt_down=("muted", NONE),
            ptt_up=("muted", NONE),
            speech=("muted", NONE),  # never consumed
            silence=("muted", NONE),
            wake=("muted", NONE),
            playback_ended=("muted", NONE),
            credential=("muted", NONE),
            shutdown=(EXIT, R),
        ),
        "off": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("off", NONE),
            link_down=("off", R),
            link_up=("off", NONE),
            mode_off=("off", R),
            mode_ptt=("armed_ptt", NONE),
            mode_always_on=("armed_wake", NONE),
            ptt_down=("off", NONE),
            ptt_up=("off", NONE),
            speech=("off", NONE),
            silence=("off", NONE),
            wake=("off", NONE),
            playback_ended=("off", NONE),
            credential=("off", NONE),
            shutdown=(EXIT, R),
        ),
        "storm_muted": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("storm_muted", NONE),  # not a mute on -> off cycle
            link_down=("off", R),
            link_up=("storm_muted", NONE),
            mode_off=("off", R),
            mode_ptt=("storm_muted", NONE),
            mode_always_on=("storm_muted", NONE),
            ptt_down=("storm_muted", NONE),
            ptt_up=("storm_muted", NONE),
            speech=("storm_muted", NONE),
            silence=("storm_muted", NONE),
            wake=("storm_muted", NONE),
            playback_ended=("storm_muted", NONE),
            credential=("storm_muted", NONE),  # a credential does not lift a storm mute
            shutdown=(EXIT, R),
        ),
        "error": _row(
            mute_on=("muted", R),
            mute_unreadable=("muted", R),
            mute_off=("error", NONE),
            link_down=("off", R),
            link_up=("error", NONE),
            mode_off=("off", R),
            mode_ptt=("error", NONE),
            mode_always_on=("error", NONE),
            ptt_down=("error", NONE),
            ptt_up=("error", NONE),
            speech=("error", NONE),
            silence=("error", NONE),
            wake=("error", NONE),
            playback_ended=("error", NONE),
            credential=("armed_ptt", NONE),  # a new credential after a refused one
            shutdown=(EXIT, R),
        ),
    }
)

if set(TRANSITIONS) != set(STATES):  # pragma: no cover -- an import-time guard
    raise RuntimeError("the transition table must cover every D6 state")


def transition(state, event):
    """`(next_state, frames)` for `state` and `event` (KeyError for an
    unknown one: the table is closed)."""
    return TRANSITIONS[state][event]
