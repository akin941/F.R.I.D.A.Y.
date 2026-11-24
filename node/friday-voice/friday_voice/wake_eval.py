"""The FAR/FRR harness (Phase 11 brief D13; roadmap d3, §c.3).

    friday-voice wake-eval --negative <dir> --positives <dir> --model <onnx>
                           [--threshold 0.5] [--refractory-ms 2000]
                           [--model-dir <dir>] [--out report.json]

Streams every WAV file of the two directories through the real
`openWakeWord` detector (the pinned melspectrogram and embedding graphs of
the node's model directory plus the keyword head under evaluation) in the
pipeline's 20 ms capture frames, behind the same `WakeGate` the child runs
(threshold and refractory period from `friday_voice.config` unless given),
and writes a JSON report:

- `negative` (household audio or a negative corpus): every detection is a
  false accept; `hours`, `false_accepts`, `far_per_hour`;
- `positives` (one "Hey Friday" per file): a file without a detection is a
  false reject; `positives`, `false_rejects`, `frr`.

No server, no network, nothing written but the report. The report carries
numbers and hashes only: per file its content sha256, its length in ms and
its detection count -- **no file name, no path, no audio, no transcript**
(the files are sorted by hash, so not even their order survives). Each
section's `manifest_sha256` hashes its file list and `report_sha256`
hashes the whole report (canonical JSON without that field), so
`manage.py voice_wake_eval record --from-report` can refuse a tampered or
internally inconsistent report before it becomes the D11 evaluation row.

The owner's household audio and positive recordings stay outside the
repository and outside the database (brief "Out of scope"); this module
only reads them.
"""

import hashlib
import json
import sys
import wave
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import config
from .audio import decode_wav
from .ports import ManualClock
from .wake import WakeGate

REPORT_SCHEMA = "friday-voice/wake-eval/v1"
FRAME_MS = 20  # the pipeline's capture frame (reference host FRAME_MS)
AUDIO_SUFFIX = ".wav"
HOURS_QUANTUM = Decimal("0.001")
RATE_QUANTUM = Decimal("0.0001")
MS_PER_HOUR = 3_600_000
_STREAM_FRAMES = 16_000 * 60  # read native WAVs a minute at a time


class EvalError(ValueError):
    """The inputs cannot be evaluated (no files, duplicates, unreadable audio,
    a bad threshold): nothing is reported."""


# --- numbers ---


def threshold_thousandths(text) -> int:
    """A CLI threshold (a fraction in (0, 1], e.g. `0.5`) -> thousandths,
    the unit the child runs at. More than 3 decimals cannot be run."""
    try:
        value = Decimal(str(text))
    except InvalidOperation as exc:
        raise EvalError("the threshold is a number in (0, 1]") from exc
    if not value.is_finite() or not Decimal(0) < value <= Decimal(1):
        raise EvalError("the threshold is a number in (0, 1]")
    thousandths = value * 1000
    if thousandths != thousandths.to_integral_value():
        raise EvalError("the threshold has at most 3 decimals (the child runs in thousandths)")
    return int(thousandths)


def threshold_text(thousandths) -> str:
    """The report's (and the server's) threshold: 4 decimals, exact."""
    return str((Decimal(int(thousandths)) / 1000).quantize(RATE_QUANTUM))


def hours_text(total_ms) -> str:
    return str((Decimal(int(total_ms)) / MS_PER_HOUR).quantize(HOURS_QUANTUM))


def rate_text(count, base) -> str | None:
    base = Decimal(str(base))
    if base <= 0:
        return None
    return str((Decimal(int(count)) / base).quantize(RATE_QUANTUM))


def canonical(document) -> bytes:
    """The bytes every hash in the report is taken over (the backend's
    `accounts.wake_eval_report.canonical` is the same function)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- audio ---


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frames_of(pcm: bytes, frame_bytes: int, carry: bytearray):
    carry += pcm
    whole = len(carry) - len(carry) % frame_bytes
    for start in range(0, whole, frame_bytes):
        yield bytes(carry[start : start + frame_bytes])
    del carry[:whole]


def iter_frames(path, *, frame_ms=FRAME_MS):
    """The file as 16 kHz mono PCM16 capture frames of `frame_ms` (the last
    one zero-padded). 16 kHz mono PCM16 is streamed; any other PCM WAV is
    normalised in memory with the child's own `decode_wav`."""
    frame_bytes = config.ms_to_bytes(frame_ms)
    carry = bytearray()
    try:
        with wave.open(str(path), "rb") as wav:
            native = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
                config.SAMPLE_RATE,
                1,
                2,
            )
            if native:
                while True:
                    chunk = wav.readframes(_STREAM_FRAMES)
                    if not chunk:
                        break
                    yield from _frames_of(chunk, frame_bytes, carry)
    except (wave.Error, EOFError) as exc:
        raise EvalError("an input file is not a PCM WAV") from exc
    if not native:
        try:
            pcm = decode_wav(Path(path).read_bytes())
        except ValueError as exc:
            raise EvalError("an input file is not a readable PCM WAV") from exc
        yield from _frames_of(pcm, frame_bytes, carry)
    if carry:
        yield bytes(carry) + b"\x00" * (frame_bytes - len(carry))


def count_detections(detector, frames, *, threshold, refractory_ms, frame_ms=FRAME_MS):
    """`(detections, ms)` of one stream through a fresh `WakeGate` on a
    manual clock that advances one frame per frame: the refractory period
    is the pipeline's, applied in audio time."""
    detector.reset()
    clock = ManualClock()
    gate = WakeGate(detector, clock, threshold=threshold, refractory_ms=refractory_ms)
    ms = 0
    for pcm in frames:
        gate.feed(pcm)
        clock.advance(frame_ms)
        ms += frame_ms
    return gate.detections, ms


def audio_files(directory) -> list:
    directory = Path(directory)
    if not directory.is_dir():
        raise EvalError("an input directory does not exist")
    files = sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() == AUDIO_SUFFIX
    )
    if not files:
        raise EvalError("an input directory holds no .wav file")
    return files


def scan(paths, detector, *, threshold, refractory_ms, frame_ms=FRAME_MS, frames_of=None):
    """One entry per file: `{"sha256", "ms", "detections"}`, sorted by hash.
    The same recording twice would count its hours twice: refused."""
    frames_of = frames_of or (lambda path: iter_frames(path, frame_ms=frame_ms))
    entries = []
    seen = set()
    for path in paths:
        digest = file_sha256(path)
        if digest in seen:
            raise EvalError("the same recording appears twice (identical sha256)")
        seen.add(digest)
        detections, ms = count_detections(
            detector,
            frames_of(path),
            threshold=threshold,
            refractory_ms=refractory_ms,
            frame_ms=frame_ms,
        )
        if ms <= 0:
            raise EvalError("an input file holds no audio")
        entries.append({"sha256": digest, "ms": ms, "detections": detections})
    return sorted(entries, key=lambda entry: entry["sha256"])


# --- the report ---


def manifest_sha256(files) -> str:
    return sha256_hex(canonical(files))


def negative_section(files) -> dict:
    total_ms = sum(entry["ms"] for entry in files)
    hours = hours_text(total_ms)
    accepts = sum(entry["detections"] for entry in files)
    return {
        "files": files,
        "manifest_sha256": manifest_sha256(files),
        "hours": hours,
        "false_accepts": accepts,
        "far_per_hour": rate_text(accepts, hours),
    }


def positives_section(files) -> dict:
    rejects = sum(1 for entry in files if entry["detections"] == 0)
    return {
        "files": files,
        "manifest_sha256": manifest_sha256(files),
        "positives": len(files),
        "false_rejects": rejects,
        "frr": rate_text(rejects, len(files)),
    }


def build_report(
    *, model_sha256, threshold, refractory_ms, negative=None, positives=None, frame_ms=FRAME_MS
) -> dict:
    """The report document from per-file entries (`scan`). `threshold` in
    thousandths."""
    if negative is None and positives is None:
        raise EvalError("nothing to report: give --negative and/or --positives")
    report = {
        "schema": REPORT_SCHEMA,
        "model_sha256": str(model_sha256),
        "threshold": threshold_text(threshold),
        "refractory_ms": int(refractory_ms),
        "frame_ms": int(frame_ms),
    }
    if negative is not None:
        report["negative"] = negative_section(negative)
    if positives is not None:
        report["positives"] = positives_section(positives)
    report["report_sha256"] = sha256_hex(canonical(report))
    return report


def render(report) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def evaluate(
    detector,
    *,
    negative_dir=None,
    positives_dir=None,
    threshold=None,
    refractory_ms=None,
    frame_ms=FRAME_MS,
    frames_of=None,
) -> dict:
    """Run the harness over the directories with `detector` (any object
    with `score(pcm)`, `reset()` and `model_sha256`)."""
    threshold = config.WAKE_THRESHOLD if threshold is None else int(threshold)
    refractory_ms = config.WAKE_REFRACTORY_MS if refractory_ms is None else int(refractory_ms)
    options = {
        "threshold": threshold,
        "refractory_ms": refractory_ms,
        "frame_ms": frame_ms,
        "frames_of": frames_of,
    }
    negative = (
        scan(audio_files(negative_dir), detector, **options) if negative_dir is not None else None
    )
    positives = (
        scan(audio_files(positives_dir), detector, **options) if positives_dir is not None else None
    )
    return build_report(
        model_sha256=detector.model_sha256,
        threshold=threshold,
        refractory_ms=refractory_ms,
        negative=negative,
        positives=positives,
        frame_ms=frame_ms,
    )


def summary(report) -> str:
    """One line for the terminal (numbers only)."""
    parts = [
        f"model={report['model_sha256'][:12]}",
        f"threshold={report['threshold']}",
        f"refractory_ms={report['refractory_ms']}",
    ]
    if "negative" in report:
        n = report["negative"]
        parts += [
            f"hours={n['hours']}",
            f"false_accepts={n['false_accepts']}",
            f"far_per_hour={n['far_per_hour']}",
        ]
    if "positives" in report:
        p = report["positives"]
        parts += [
            f"positives={p['positives']}",
            f"false_rejects={p['false_rejects']}",
            f"frr={p['frr']}",
        ]
    return "wake-eval " + " ".join(parts)


def main(args, *, detector_factory=None, out=print, err=None) -> int:
    """`friday-voice wake-eval`. Exit 0 with a report, 2 on bad input."""
    err = err or (lambda line: print(line, file=sys.stderr))
    if args.negative is None and args.positives is None:
        err("friday-voice wake-eval needs --negative and/or --positives")
        return 2
    try:
        threshold = (
            config.WAKE_THRESHOLD
            if args.threshold is None
            else threshold_thousandths(args.threshold)
        )
        refractory_ms = (
            config.WAKE_REFRACTORY_MS if args.refractory_ms is None else int(args.refractory_ms)
        )
        if refractory_ms < 0:
            raise EvalError("the refractory period is not negative")
        if detector_factory is None:
            from .wake import OpenWakeWordEvalDetector

            detector = OpenWakeWordEvalDetector(args.model_dir, args.model)
        else:
            detector = detector_factory(args)
        report = evaluate(
            detector,
            negative_dir=args.negative,
            positives_dir=args.positives,
            threshold=threshold,
            refractory_ms=refractory_ms,
        )
    except (EvalError, OSError) as exc:
        err(f"friday-voice wake-eval: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 -- a model that cannot load: no report
        err(f"friday-voice wake-eval: the detector failed ({type(exc).__name__})")
        return 2
    text = render(report)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        err(f"wrote the report ({report['report_sha256'][:12]})")
    else:
        out(text.rstrip("\n"))
    err(summary(report))
    return 0
