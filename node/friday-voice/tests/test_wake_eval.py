"""The FAR/FRR harness (brief D13): `friday-voice wake-eval` on synthetic
WAVs with a scripted detector -- the refractory period, the FAR per hour
and FRR arithmetic, a report with hashes and numbers only (no file name,
no audio) -- and, opt-in (`-m real_models`), the real openWakeWord detector
with the public stand-in `hey_jarvis` head on a synthetic file."""

import argparse
import hashlib
import io
import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest
from conftest import PACKAGE_ROOT, REPO_ROOT

from friday_voice import cli, config, wake_eval
from friday_voice.wake import OpenWakeWordEvalDetector

RATE = 16_000
SHA = "c" * 64


class LevelDetector:
    """Scripted: a frame louder than the level "is" the wake phrase."""

    def __init__(self, level=1000, model_sha256=SHA):
        self.level = level
        self.model_sha256 = model_sha256
        self.resets = 0

    def score(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2")
        return 900 if samples.size and int(np.abs(samples).max()) >= self.level else 0

    def reset(self):
        self.resets += 1


def wav(path, segments, *, rate=RATE, channels=1, salt=0):
    """`segments`: [(seconds, loud: bool)]. A loud segment is a 0.3 FS tone;
    quiet is a near-silent hum carrying `salt`, so two files never share a
    hash by accident."""
    parts = []
    for seconds, loud in segments:
        count = int(rate * seconds)
        t = np.arange(count) / rate
        if loud:
            parts.append(0.3 * np.sin(2 * np.pi * 300 * t))
        else:
            parts.append(np.full(count, (salt % 7 + 1) / 32768.0))
    samples = np.concatenate(parts) if parts else np.zeros(0)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm, 2)
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())
    return path


# --- numbers ---


@pytest.mark.parametrize(
    ("text", "thousandths"), [("0.5", 500), ("0.55", 550), ("1", 1000), ("0.001", 1)]
)
def test_the_threshold_is_read_in_thousandths(text, thousandths):
    assert wake_eval.threshold_thousandths(text) == thousandths


@pytest.mark.parametrize("text", ["0", "-0.5", "1.5", "0.0005", "nan", "abc", "inf"])
def test_impossible_thresholds_are_refused(text):
    with pytest.raises(wake_eval.EvalError):
        wake_eval.threshold_thousandths(text)


def test_the_report_threshold_has_the_servers_four_decimals():
    assert wake_eval.threshold_text(config.WAKE_THRESHOLD) == "0.5000"
    assert wake_eval.threshold_text(555) == "0.5550"


# --- the refractory period (in audio time, the pipeline's WakeGate) ---


def test_one_long_utterance_is_one_detection_within_the_refractory_period(tmp_path):
    path = wav(tmp_path / "a.wav", [(1.0, False), (1.5, True), (1.0, False)])
    detections, ms = wake_eval.count_detections(
        LevelDetector(),
        wake_eval.iter_frames(path),
        threshold=config.WAKE_THRESHOLD,
        refractory_ms=config.WAKE_REFRACTORY_MS,
    )
    assert detections == 1
    assert ms == 3_500


@pytest.mark.parametrize(("gap_s", "expected"), [(0.5, 1), (1.5, 2), (3.0, 2)])
def test_bursts_closer_than_the_refractory_period_count_once(tmp_path, gap_s, expected):
    # Burst onsets are 0.5 + gap seconds apart; refractory is 2 s.
    path = wav(tmp_path / "a.wav", [(0.5, True), (gap_s, False), (0.5, True)])
    detections, _ = wake_eval.count_detections(
        LevelDetector(),
        wake_eval.iter_frames(path),
        threshold=config.WAKE_THRESHOLD,
        refractory_ms=config.WAKE_REFRACTORY_MS,
    )
    assert detections == expected


def test_a_score_below_the_threshold_is_not_a_detection(tmp_path):
    path = wav(tmp_path / "a.wav", [(1.0, True)])
    detections, _ = wake_eval.count_detections(
        LevelDetector(), wake_eval.iter_frames(path), threshold=950, refractory_ms=2_000
    )
    assert detections == 0


def test_frames_are_the_pipelines_20_ms_and_other_formats_are_normalised(tmp_path):
    native = wav(tmp_path / "n.wav", [(1.01, False)])
    frames = list(wake_eval.iter_frames(native))
    assert all(len(f) == config.ms_to_bytes(20) for f in frames)
    assert len(frames) == 51  # the last one zero-padded
    stereo_22k = wav(tmp_path / "s.wav", [(1.0, True)], rate=22_050, channels=2)
    frames = list(wake_eval.iter_frames(stereo_22k))
    assert len(frames) == 50 and all(len(f) == 640 for f in frames)


def test_a_file_that_is_not_a_wav_is_refused(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF not really")
    with pytest.raises(wake_eval.EvalError):
        list(wake_eval.iter_frames(bad))


# --- the arithmetic and the report ---


@pytest.fixture
def corpus(tmp_path):
    """Negatives: three files, 3 + 2 + 1 = 6 bursts far apart, 1.5 min of
    audio in all. Positives: five files, one of them without the phrase."""
    negative = tmp_path / "household_recordings_owner_secret"
    long_quiet = [(9.0, False)]
    wav(
        negative / "kitchen-monday.wav",
        [(1, True), *long_quiet, (1, True), *long_quiet, (1, True), (9, False)],
        salt=1,
    )
    wav(negative / "living-room.wav", [(1, True), *long_quiet, (1, True), (19, False)], salt=2)
    wav(negative / "nested" / "night.wav", [(1, True), (29, False)], salt=3)
    (negative / "notes.txt").write_text("not audio: ignored")
    positives = tmp_path / "positives_owner"
    for index in range(4):
        wav(positives / f"hey-friday-{index}.wav", [(0.2, False), (0.6, True)], salt=index)
    wav(positives / "hey-friday-missed.wav", [(0.8, False)], salt=9)
    return negative, positives


def test_far_per_hour_and_frr_arithmetic(corpus):
    negative, positives = corpus
    report = wake_eval.evaluate(LevelDetector(), negative_dir=negative, positives_dir=positives)
    n, p = report["negative"], report["positives"]
    assert sum(entry["ms"] for entry in n["files"]) == 90_000
    assert n["hours"] == "0.025"  # 90 s
    assert n["false_accepts"] == 6
    assert n["far_per_hour"] == "240.0000"  # 6 / 0.025
    assert p["positives"] == 5 and p["false_rejects"] == 1 and p["frr"] == "0.2000"
    assert report["model_sha256"] == SHA
    assert report["threshold"] == "0.5000" and report["refractory_ms"] == 2_000
    assert report["frame_ms"] == 20 and report["schema"] == "friday-voice/wake-eval/v1"


def test_the_report_holds_hashes_and_numbers_only(corpus):
    negative, positives = corpus
    report = wake_eval.evaluate(LevelDetector(), negative_dir=negative, positives_dir=positives)
    text = wake_eval.render(report)
    for secret in (
        "kitchen",
        "living",
        "night",
        "nested",
        "hey-friday",
        "household_recordings",
        "positives_owner",
        ".wav",
        str(negative.parent),
    ):
        assert secret not in text
    for section in ("negative", "positives"):
        files = report[section]["files"]
        assert [set(entry) for entry in files] == [{"sha256", "ms", "detections"}] * len(files)
        assert [entry["sha256"] for entry in files] == sorted(e["sha256"] for e in files)
    expected = {hashlib.sha256(p.read_bytes()).hexdigest() for p in negative.rglob("*.wav")}
    assert {entry["sha256"] for entry in report["negative"]["files"]} == expected
    assert len(text) < 10_000  # numbers, never audio


def test_the_hashes_cover_every_number(corpus):
    negative, _ = corpus
    report = wake_eval.evaluate(LevelDetector(), negative_dir=negative)
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    assert report["report_sha256"] == hashlib.sha256(wake_eval.canonical(body)).hexdigest()
    files = report["negative"]["files"]
    assert (
        report["negative"]["manifest_sha256"]
        == hashlib.sha256(wake_eval.canonical(files)).hexdigest()
    )
    assert "positives" not in report


def test_the_same_recording_twice_is_refused(tmp_path):
    first = wav(tmp_path / "neg" / "a.wav", [(1, False)])
    (tmp_path / "neg" / "copy.wav").write_bytes(first.read_bytes())
    with pytest.raises(wake_eval.EvalError, match="twice"):
        wake_eval.evaluate(LevelDetector(), negative_dir=tmp_path / "neg")


def test_an_empty_or_missing_directory_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(wake_eval.EvalError):
        wake_eval.evaluate(LevelDetector(), negative_dir=tmp_path / "empty")
    with pytest.raises(wake_eval.EvalError):
        wake_eval.evaluate(LevelDetector(), negative_dir=tmp_path / "missing")
    with pytest.raises(wake_eval.EvalError):
        wake_eval.evaluate(LevelDetector())


def test_the_defaults_are_the_childs_threshold_and_refractory_period(corpus, monkeypatch):
    negative, _ = corpus
    monkeypatch.setattr(config, "WAKE_THRESHOLD", 950)
    monkeypatch.setattr(config, "WAKE_REFRACTORY_MS", 30_000)
    report = wake_eval.evaluate(LevelDetector(), negative_dir=negative)
    assert report["threshold"] == "0.9500" and report["refractory_ms"] == 30_000
    assert report["negative"]["false_accepts"] == 0  # 900 < 950


def test_the_detector_is_reset_between_files(corpus):
    negative, _ = corpus
    detector = LevelDetector()
    wake_eval.evaluate(detector, negative_dir=negative)
    assert detector.resets == 3


# --- the command ---


def _args(**overrides):
    base = {
        "negative": None,
        "positives": None,
        "model": "head.onnx",
        "model_dir": None,
        "threshold": None,
        "refractory_ms": None,
        "out": None,
    }
    return argparse.Namespace(**(base | overrides))


def test_the_command_writes_the_report(corpus, tmp_path):
    negative, positives = corpus
    out = tmp_path / "report.json"
    lines = []
    code = wake_eval.main(
        _args(negative=str(negative), positives=str(positives), out=str(out), threshold="0.5"),
        detector_factory=lambda _a: LevelDetector(),
        err=lines.append,
    )
    assert code == 0
    report = json.loads(out.read_text())
    assert report["negative"]["false_accepts"] == 6
    assert lines[-1].startswith("wake-eval model=cccccccccccc threshold=0.5000")
    assert "kitchen" not in " ".join(lines)


def test_the_command_prints_to_stdout_without_out(corpus):
    negative, _ = corpus
    printed = []
    code = wake_eval.main(
        _args(negative=str(negative)),
        detector_factory=lambda _a: LevelDetector(),
        out=printed.append,
        err=lambda _l: None,
    )
    assert code == 0 and json.loads(printed[0])["schema"] == wake_eval.REPORT_SCHEMA


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"negative": "missing-dir"},
        {"negative": ".", "threshold": "2"},
        {"negative": ".", "refractory_ms": -1},
    ],
)
def test_the_command_refuses_bad_input(overrides, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    errors = []
    code = wake_eval.main(
        _args(**overrides), detector_factory=lambda _a: LevelDetector(), err=errors.append
    )
    assert code == 2 and errors


def test_a_detector_that_cannot_load_is_an_error_not_a_report(corpus, tmp_path):
    negative, _ = corpus
    errors = []
    code = wake_eval.main(
        _args(
            negative=str(negative),
            model_dir=str(tmp_path / "no-models"),
            model=str(tmp_path / "missing.onnx"),
        ),
        err=errors.append,
    )
    assert code == 2 and "detector failed" in errors[0]


def test_the_eval_detector_refuses_unpinned_feature_graphs(tmp_path):
    head = tmp_path / "head.onnx"
    head.write_bytes(b"not a model")
    with pytest.raises(Exception, match="feature graphs"):
        OpenWakeWordEvalDetector(tmp_path, head)


def test_the_cli_parses_wake_eval(monkeypatch):
    seen = {}
    monkeypatch.setattr(wake_eval, "main", lambda args: seen.setdefault("args", args) and 0)
    cli.main(["wake-eval", "--negative", "n", "--model", "m.onnx", "--threshold", "0.6"])
    args = seen["args"]
    assert (args.negative, args.positives, args.model, args.threshold) == (
        "n",
        None,
        "m.onnx",
        "0.6",
    )


# --- the round-trip fixture (backend `voice_wake_eval record --from-report`) ---


def test_the_round_trip_report_is_current_and_its_backend_copy_identical():
    from conftest import load_script

    rendered = load_script("make_fixtures").wake_eval_report()
    node_copy = PACKAGE_ROOT / "fixtures" / "wake_eval_report.json"
    backend_copy = (
        REPO_ROOT / "backend" / "accounts" / "tests" / "fixtures" / "wake_eval_report.json"
    )
    assert node_copy.read_text("utf-8") == rendered
    assert backend_copy.read_text("utf-8") == rendered
    report = json.loads(rendered)
    assert report["negative"]["hours"] == "24.000"
    assert report["negative"]["far_per_hour"] == "0.1250"
    assert report["positives"]["frr"] == "0.0800"
    assert report["threshold"] == wake_eval.threshold_text(config.WAKE_THRESHOLD)
    assert report["refractory_ms"] == config.WAKE_REFRACTORY_MS


# --- real models (opt-in: -m real_models; Linux CPU) ---

MODEL_DIR = os.environ.get("FRIDAY_VOICE_MODEL_DIR")
STANDIN_HEAD = os.environ.get("FRIDAY_VOICE_STANDIN_HEAD")  # openWakeWord's hey_jarvis


@pytest.mark.real_models
@pytest.mark.skipif(
    not (MODEL_DIR and STANDIN_HEAD),
    reason="FRIDAY_VOICE_MODEL_DIR (pinned graphs) and FRIDAY_VOICE_STANDIN_HEAD (hey_jarvis)",
)
def test_the_real_detector_with_the_stand_in_head_on_a_synthetic_file(tmp_path):
    detector = OpenWakeWordEvalDetector(MODEL_DIR, STANDIN_HEAD)
    wav(tmp_path / "neg" / "tones.wav", [(2.0, False), (2.0, True), (2.0, False)])
    report = wake_eval.evaluate(detector, negative_dir=tmp_path / "neg")
    assert report["model_sha256"] == hashlib.sha256(Path(STANDIN_HEAD).read_bytes()).hexdigest()
    assert report["negative"]["hours"] == "0.002"
    assert report["negative"]["false_accepts"] == 0  # a tone is not "hey jarvis"
