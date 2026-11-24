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


WAVS = {
    "ptt_question.wav": lambda: tone(1.6),
    "ptt_long_question.wav": lambda: np.concatenate([tone(2.0), silence(0.3), tone(1.4, 260)]),
    "wake_question.wav": lambda: np.concatenate([silence(0.4), tone(1.2, 240)]),
    "wake_followup.wav": lambda: np.concatenate([silence(0.2), tone(0.9, 200)]),
    "silence_2s.wav": lambda: silence(2.0),
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
