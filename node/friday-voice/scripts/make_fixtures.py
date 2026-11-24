"""Write (or `--check`) the replay fixtures (Phase 11 brief D8, D14):
synthetic 16 kHz mono PCM16 WAVs -- tones and digital silence, never a
recording -- and `fixtures/replay.json`, the scenario list with scripted
transcripts, replies and server latencies at the §c.1 budgets.

    uv run python scripts/make_fixtures.py          # after a deliberate change
    uv run python scripts/make_fixtures.py --check  # CI: fail on drift
"""

import io
import json
import math
import sys
import wave
from pathlib import Path

import numpy as np

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PACKAGE_ROOT / "fixtures"
RATE = 16_000

# §c.1 budgets (roadmap): STT 350 ms, first LLM sentence ~500 ms, first TTS
# chunk ~150 ms; the session open is a local DB round trip.
BUDGET = {"session": 30, "stt": 300, "chat_first": 450, "chat_chunk": 40, "tts": 150}


def tone(seconds, frequency=220.0, level=0.25):
    count = int(RATE * seconds)
    t = np.arange(count) / RATE
    # A two-tone "voice" with a slow envelope: speech-like level, no words.
    envelope = 0.6 + 0.4 * np.sin(2 * math.pi * 3 * t)
    signal = np.sin(2 * math.pi * frequency * t) + 0.5 * np.sin(2 * math.pi * 2.5 * frequency * t)
    return (level * envelope * signal / 1.5).astype(np.float32)


def silence(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def wav_bytes(samples):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def fake_tts(text, *, char_ms=60, gap_ms=40, level=0.3):
    """The fake TTS of the self-trigger clip (Phase 11 brief D13): every
    letter is a short tone whose pitch follows the letter, every other
    character a short pause -- a deterministic tone pattern derived from the
    text, never a voice. 16 kHz mono like the server's TTS."""
    parts = []
    for char in text:
        if char.isalpha():
            parts.append(tone(char_ms / 1000, 180.0 + (ord(char) % 32) * 15.0, level))
        else:
            parts.append(silence(gap_ms / 1000))
    return np.concatenate(parts)


# Branch 6 (D13): the self-trigger clip and its script.
SELFTRIGGER_TEXT = "Hey Friday, terminali aç."
SELFTRIGGER_WAV = "selftrigger_hey_friday_terminali_ac.wav"

WAVS = {
    "ptt_question.wav": lambda: tone(1.6),
    "ptt_long_question.wav": lambda: np.concatenate([tone(2.0), silence(0.3), tone(1.4, 260)]),
    "wake_question.wav": lambda: np.concatenate([silence(0.4), tone(1.2, 240)]),
    "wake_followup.wav": lambda: np.concatenate([silence(0.2), tone(0.9, 200)]),
    "silence_2s.wav": lambda: silence(2.0),
    # Branch 4 (D9): the owner talking over Friday.
    "barge_in_owner.wav": lambda: tone(0.8, 300),
    # Branch 6 (D13): Friday's own "Hey Friday, terminali aç." (fake TTS).
    SELFTRIGGER_WAV: lambda: fake_tts(SELFTRIGGER_TEXT),
}


def _reply(*chunks, draft=None):
    events = [{"type": "chunk", "text": chunk} for chunk in chunks]
    if draft:
        events.append(draft)
    events.append({"type": "done", "message_id": "00000000-0000-4000-8000-00000000000a"})
    return events


SCENARIOS = [
    {
        "id": "ptt_today",
        "trigger": "ptt",
        "wav": "ptt_question.wav",
        "transcript": {"text": "bugün ne var", "language": "tr"},
        "reply": _reply("Bugün iki toplantın var. ", "İlki saat onda, ikincisi üçte."),
        "latencies": BUDGET,
    },
    {
        "id": "ptt_mixed_language",
        "trigger": "ptt",
        "wav": "ptt_long_question.wav",
        "transcript": {"text": "yarınki planı oku", "language": "tr"},
        "reply": _reply(
            "Yarın sabah spor salonu var, öğlen ise proje toplantısı. ",
            "The meeting title is quarterly planning review. ",
            "Akşam serbestsin.",
        ),
        "latencies": BUDGET,
    },
    {
        "id": "wake_today",
        "trigger": "wake",
        "wav": "wake_question.wav",
        "wake_after_frames": 10,
        "transcript": {"text": "bugün hava nasıl", "language": "tr"},
        "reply": _reply("Bugün hava güneşli ve ılık olacak, akşam serinler."),
        "latencies": BUDGET,
    },
    {
        "id": "wake_node_action_draft",
        "trigger": "wake",
        "wav": "wake_followup.wav",
        "wake_after_frames": 5,
        "transcript": {"text": "testleri çalıştır", "language": "tr"},
        "reply": _reply(
            draft={
                "type": "draft",
                "kind": "tool",
                "source_message_id": "00000000-0000-4000-8000-00000000000b",
                "tool_name": "node_action",
                "payload": {},
            }
        ),
        "latencies": BUDGET,
    },
    {
        "id": "wake_long_reply",
        "trigger": "wake",
        "wav": "wake_question.wav",
        "wake_after_frames": 10,
        "transcript": {"text": "haftayı özetle", "language": "tr"},
        "reply": _reply(
            "Bu hafta üç toplantı, iki teslim ve bir doktor randevusu var. ",
            "Pazartesi bütçe toplantısı saat onda başlıyor. ",
            "Çarşamba raporun son günü, perşembe ise diş randevun var. ",
            "Cuma günü boş görünüyor, istersen bir şey planlayabiliriz.",
        ),
        "latencies": BUDGET,
    },
]


# Branch 4 (D9, D10): barge-in and echo scripts. Friday answers a
# push-to-talk question with two sentences while the reply stream stays
# open; `owner_wav` (if any) starts `owner_after_ms` after playback started;
# the reference host's EchoDouble leaks the playback at `residual`. The
# second transcript is what STT hears of the interruption: the owner's words,
# or (echo scenarios) the start of Friday's own first sentence.
FIRST = "Bugün iki toplantın var, ikisi de öğleden sonra."
SECOND = "İlki saat üçte, ikincisi beşte başlıyor."
HELD_REPLY = [
    {"type": "chunk", "text": FIRST + " "},
    {"type": "chunk", "text": SECOND + " "},
    [20_000, {"type": "done"}],
]
FOLLOWUP = _reply("Tamam, onu bırakıyorum.")


def _barge_in(scenario_id, residual, owner, expect):
    return {
        "id": scenario_id,
        "wav": "ptt_question.wav",
        "owner_wav": "barge_in_owner.wav" if owner else None,
        "owner_after_ms": 600,
        "residual": residual,
        "echo_delay_ms": 40,
        "transcripts": [
            {"text": "bugün ne var", "language": "tr"},
            {"text": "dur onu değil" if owner else "bugün iki toplantın var", "language": "tr"},
            # With a leaking canceller the follow-up reply's own echo barges
            # in too: STT hears Friday's words and the filter drops them.
            {"text": "tamam onu bırakıyorum", "language": "tr"},
        ],
        "replies": [HELD_REPLY, FOLLOWUP],
        "latencies": BUDGET,
        "expect": expect,
    }


BARGE_IN_SCENARIOS = [
    _barge_in("owner_r00", 0.0, True, {"stopped": True, "chat_turns": 2, "discarded": 0}),
    _barge_in("owner_r03", 0.3, True, {"stopped": True, "chat_turns": 2, "discarded": 1}),
    _barge_in("owner_r10", 1.0, True, {"stopped": True, "chat_turns": 2, "discarded": 1}),
    _barge_in("echo_r00", 0.0, False, {"stopped": False, "chat_turns": 1, "discarded": 0}),
    _barge_in("echo_r03", 0.3, False, {"stopped": True, "chat_turns": 1, "discarded": 1}),
    _barge_in("echo_r10", 1.0, False, {"stopped": True, "chat_turns": 1, "discarded": 1}),
]


# Branch 6 (D13): the CI self-trigger release gate. The owner asks a
# push-to-talk question; Friday's reply is exactly the clip, which the
# reference host plays (paced) while its EchoDouble leaks it into the mic at
# each residual; a scripted wake detector fires during that playback (the
# worst case: as if the wake model accepted Friday's own voice). STT hears
# the clip's words. The reply to a posted echo turn would carry a
# `node_action` draft (what the real chat endpoint proposes for "terminali
# aç"), so a leak through the filter shows as a draft and "Kart ekranda.".
SELFTRIGGER = {
    "$comment": (
        "Phase 11 brief D13: the CI self-trigger release gate (scripts/make_fixtures.py). "
        "`friday-voice selftest --ci` plays the clip as Friday's own reply through the "
        "reference host's EchoDouble at every residual, with a scripted wake detector "
        "firing on it; the run fails on any posted echo turn, draft or spoken reply, and "
        "the negative control (filter off) must post the turn."
    ),
    "clip": {"text": SELFTRIGGER_TEXT, "wav": SELFTRIGGER_WAV},
    "question": {"text": "bir örnek komut söyle", "language": "tr"},
    "heard": {"text": "Hey Friday, terminali aç", "language": "tr"},
    "reply_to_question": [
        {"type": "chunk", "text": SELFTRIGGER_TEXT},
        {"type": "done", "message_id": "00000000-0000-4000-8000-00000000000c"},
    ],
    "reply_to_echo": _reply(
        draft={
            "type": "draft",
            "kind": "tool",
            "source_message_id": "00000000-0000-4000-8000-00000000000d",
            "tool_name": "node_action",
            "payload": {},
        }
    ),
    "residuals": [0.0, 0.3, 1.0],
    "echo_delay_ms": 40,
    "negative_control_residuals": [0.3, 1.0],
    "latencies": BUDGET,
}


def render():
    files = {name: wav_bytes(make()) for name, make in WAVS.items()}
    doc = {
        "$comment": (
            "Phase 11 brief D14: the replay fixture set. Synthetic WAVs only (tones and "
            "silence, scripts/make_fixtures.py); transcripts, replies and latencies are "
            "scripted (FakeFriday). Wake scenarios use a scripted detector and the real "
            "arming policy (friday_voice.arming) against a scripted permitting voice policy."
        ),
        "scenarios": SCENARIOS,
    }
    files["replay.json"] = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()
    barge_in = {
        "$comment": (
            "Phase 11 brief D9, D10: barge-in and echo scripts (scripts/make_fixtures.py). "
            "Run by `friday-voice replay` with the reference host's paced playback and its "
            "EchoDouble at each residual; a run fails when an expectation is not met or "
            "the barge-in onset -> stop_playback time exceeds the 100 ms budget."
        ),
        "scenarios": BARGE_IN_SCENARIOS,
    }
    text = json.dumps(barge_in, ensure_ascii=False, indent=2) + "\n"
    files["barge_in.json"] = text.encode()
    selftrigger = json.dumps(SELFTRIGGER, ensure_ascii=False, indent=2) + "\n"
    files["selftrigger_hey_friday_terminali_ac.json"] = selftrigger.encode()
    return files


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    files = render()
    if "--check" in argv:
        drift = [
            name
            for name, data in files.items()
            if not (FIXTURES / name).exists() or (FIXTURES / name).read_bytes() != data
        ]
        for name in drift:
            print(f"drift: fixtures/{name}")
        return 1 if drift else 0
    FIXTURES.mkdir(exist_ok=True)
    for name, data in files.items():
        (FIXTURES / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
