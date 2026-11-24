"""The self-trigger release gate and the nightly selftest (brief D13).

- `--ci`: Friday's own "Hey Friday, terminali aç" played through the
  reference host's EchoDouble at residual 0.0 / 0.3 / 1.0 with a scripted
  wake detector firing on it: no echo turn, no draft, nothing spoken in
  reply, every upload discarded; the negative control (filter off) posts
  the turn, so the gate proves the filter, not the fake; a broken filter
  fails the gate.
- `--nightly`: refuses without `--server`, the token, a model directory and
  the host's audio path; exit code and the one-line result with fakes; the
  loopback command contract.
"""

import argparse
import json
import sys
import wave

import numpy as np
import pytest
from conftest import PACKAGE_ROOT

from friday_voice import cli, config, selftest
from friday_voice.client import FridayError
from friday_voice.fakes import FakeFriday
from friday_voice.self_trigger import SelfTriggerFilter

SCRIPT = selftest.load_script()
CLIP_WAV = PACKAGE_ROOT / "fixtures" / SCRIPT["clip"]["wav"]


# --- the fixture ---


def test_the_clip_is_the_fake_tts_of_the_text():
    from conftest import load_script

    fixtures = load_script("make_fixtures")
    assert SCRIPT["clip"]["text"] == "Hey Friday, terminali aç."
    assert SCRIPT["heard"]["text"] == "Hey Friday, terminali aç"
    assert CLIP_WAV.read_bytes() == fixtures.wav_bytes(fixtures.fake_tts(SCRIPT["clip"]["text"]))
    with wave.open(str(CLIP_WAV), "rb") as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (16000, 1, 2)
        seconds = clip.getnframes() / clip.getframerate()
    assert 1.0 < seconds < 2.0
    assert SCRIPT["residuals"] == [0.0, 0.3, 1.0]


# --- the release gate ---


@pytest.mark.parametrize("residual", [0.0, 0.3, 1.0])
def test_friday_never_answers_its_own_wake_phrase(residual):
    result = selftest.run_selftrigger(residual)
    assert selftest.judge_gate(result) == []
    assert result["armed"] and result["clip_spoken"]
    assert result["wakes"] == 1 and result["interrupted_via"] == ["wake"]
    assert result["echo_turns"] == 0  # no chat turn posted
    assert result["drafts"] == 0  # no draft
    assert result["replies_spoken"] == [] and result["foreign_play"] == []  # nothing said back
    # At 0.0 the perfect canceller leaves nothing to upload (branch 4 note 7);
    # with a leak the echo is transcribed and discarded as Friday's own words.
    assert result["uploads"] == result["discarded"] == (0 if residual == 0.0 else 1)


@pytest.mark.parametrize("residual", [0.3, 1.0])
def test_the_negative_control_posts_the_echo_without_the_filter(residual):
    result = selftest.run_selftrigger(residual, self_trigger=False)
    assert selftest.judge_control(result) == []
    assert result["echo_turns"] == 1 and result["drafts"] == 1
    assert result["replies_spoken"] == [config.DRAFT_SENTENCE]
    # ... and the release gate would have failed it:
    assert selftest.judge_gate(result)


def test_the_whole_gate_passes():
    lines = []
    report = selftest.run_ci(out=lines.append)
    assert report["passed"]
    assert [run["kind"] for run in report["runs"]] == ["gate"] * 3 + ["negative_control"] * 2
    assert all(line.endswith(("discarded=0", "discarded=1")) for line in lines)


def test_a_broken_filter_fails_the_gate(monkeypatch):
    monkeypatch.setattr(SelfTriggerFilter, "check", lambda self, transcript, suspect=False: None)
    report = selftest.run_ci(out=lambda _l: None)
    assert not report["passed"]
    gate = [run for run in report["runs"] if run["kind"] == "gate"]
    failing = {run["residual"] for run in gate if run["failures"]}
    assert failing == {0.3, 1.0}


def test_a_detector_that_never_fires_fails_the_gate(monkeypatch):
    monkeypatch.setattr(selftest, "WAKE_AFTER_MS", 10**9)
    result = selftest.run_selftrigger(0.0)
    assert "the scripted wake detector never fired on the echo" in selftest.judge_gate(result)


def test_the_cli_gate_exit_code_and_result_line(tmp_path, capsys):
    path = tmp_path / "state" / "selftest.json"
    assert cli.main(["selftest", "--ci", "--result-file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "friday-voice selftest ci: PASS" in out
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["mode"] == "ci" and result["result"] == "pass"
    assert result["posted_turns"] == 0 and result["schema"] == selftest.RESULT_SCHEMA


def test_the_cli_gate_fails_with_a_broken_filter(monkeypatch, capsys):
    monkeypatch.setattr(SelfTriggerFilter, "check", lambda self, transcript, suspect=False: None)
    assert cli.main(["selftest", "--ci"]) == 1
    assert "friday-voice selftest ci: FAIL" in capsys.readouterr().out


def test_selftest_needs_exactly_one_mode(capsys):
    assert cli.main(["selftest"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["selftest", "--ci", "--nightly"])


# --- the nightly run (fakes in place of the Mac's real pieces) ---


class Detector:
    """Scripted: fires on loud frames (what reaches it is the capture)."""

    model_sha256 = "d" * 64

    def __init__(self):
        self.resets = 0

    def score(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2")
        return 900 if samples.size and int(np.abs(samples).max()) >= 2000 else 0

    def reset(self):
        self.resets += 1


class Loopback:
    """The host's audio path: plays the clip and returns `residual` of it
    (the AEC's leftover) after `lead_ms` of room tone."""

    def __init__(self, residual=0.0, *, fail=False):
        self.residual = residual
        self.fail = fail
        self.played = []

    def play_and_capture(self, pcm):
        if self.fail:
            raise selftest.LoopbackFailed("exit 1")
        self.played.append(pcm)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64) * self.residual
        lead = np.zeros(config.SAMPLE_RATE // 5)
        return np.concatenate([lead, samples]).astype("<i2").tobytes()


def nightly_client(transcript="Hey Friday, terminali aç"):
    client = FakeFriday(
        transcripts=[transcript], speech={SCRIPT["clip"]["text"]: CLIP_WAV.read_bytes()}
    )
    client.set_credential("jpat_nightly", "http://127.0.0.1:8001", None)
    return client


def nightly_args(tmp_path, **overrides):
    base = {
        "ci": False,
        "nightly": True,
        "server": "http://127.0.0.1:8001",
        "token_file": str(tmp_path / "token"),
        "model_dir": str(tmp_path / "models"),
        "loopback_cmd": "friday-node voice-loopback",
        "ca_file": None,
        "ca_sha256": None,
        "result_file": str(tmp_path / "selftest.json"),
    }
    return argparse.Namespace(**(base | overrides))


def run_main(tmp_path, client, loopback, detector=None, **overrides):
    args = nightly_args(tmp_path, **overrides)
    code = selftest.main(
        args,
        nightly_parts=lambda _a: (client, detector or Detector(), loopback),
        out=lambda _l: None,
        err=lambda _l: None,
    )
    line = (tmp_path / "selftest.json").read_text() if args.result_file else ""
    return code, (json.loads(line) if line else None)


@pytest.mark.parametrize("missing", ["server", "token_file", "model_dir", "loopback_cmd"])
def test_nightly_refuses_without_its_real_pieces(tmp_path, missing):
    called = []
    args = nightly_args(tmp_path, **{missing: None})
    errors = []
    code = selftest.main(
        args, nightly_parts=lambda _a: called.append(1), out=lambda _l: None, err=errors.append
    )
    assert code == 2 and not called
    assert "refuses to run without" in errors[0]
    result = json.loads((tmp_path / "selftest.json").read_text())
    assert (result["mode"], result["result"], result["reason"]) == ("nightly", "error", "usage")


def test_nightly_passes_with_a_perfect_canceller(tmp_path):
    client = nightly_client()
    loopback = Loopback(residual=0.0)
    code, result = run_main(tmp_path, client, loopback)
    assert code == 0
    assert result["result"] == "pass" and result["wakes"] == 0 and result["posted_turns"] == 0
    assert result["model_sha256"] == "d" * 64
    assert result["threshold"] == config.WAKE_THRESHOLD
    assert result["refractory_ms"] == config.WAKE_REFRACTORY_MS
    # The clip came from Friday's TTS and went through the host's audio path.
    assert [c["text"] for c in client.calls("tts")] == [SCRIPT["clip"]["text"]]
    assert loopback.played and client.calls("stt") == [] and client.calls("chat") == []


def test_nightly_fails_on_any_wake_even_one_the_filter_discards(tmp_path):
    client = nightly_client()
    code, result = run_main(tmp_path, client, Loopback(residual=1.0))
    assert code == 1
    assert result["result"] == "fail" and result["wakes"] == 1 and result["posted_turns"] == 0
    (stt,) = client.calls("stt")
    assert stt["trigger"] == "barge_in"  # a wake while Friday speaks (branch 4 note 7)
    assert client.calls("chat") == []  # the selftest never posts a turn


def test_nightly_counts_a_turn_the_filter_would_let_through(tmp_path):
    client = nightly_client(transcript="bugün hava nasıl")
    code, result = run_main(tmp_path, client, Loopback(residual=1.0))
    assert code == 1 and result["posted_turns"] == 1
    assert client.calls("chat") == []


def test_nightly_errors_are_never_green(tmp_path):
    code, result = run_main(tmp_path, nightly_client(), Loopback(fail=True))
    assert code == 2 and result["result"] == "error" and result["reason"] == "LoopbackFailed"
    client = nightly_client()
    client.fail_next("tts", FridayError(503, "speech_unavailable"))
    code, result = run_main(tmp_path, client, Loopback())
    assert code == 2 and result["reason"] == "FridayError"


def test_the_result_line_holds_no_text(tmp_path):
    client = nightly_client(transcript="özel bir cümle")
    _code, result = run_main(tmp_path, client, Loopback(residual=1.0))
    raw = (tmp_path / "selftest.json").read_text()
    assert raw.count("\n") == 1
    assert "özel" not in raw and "terminali" not in raw
    assert set(result) == {
        "schema",
        "mode",
        "result",
        "at",
        "wakes",
        "posted_turns",
        "model_sha256",
        "threshold",
        "refractory_ms",
        "reason",
    }


def test_the_result_file_is_replaced_atomically(tmp_path):
    path = tmp_path / "selftest.json"
    path.write_text("old\n")
    selftest.write_result(path, '{"result":"pass"}')
    assert path.read_text() == '{"result":"pass"}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["selftest.json"]


def test_nightly_parts_build_the_real_client_and_refuse_unpinned_models(tmp_path):
    (tmp_path / "token").write_text("jpat_x\n")
    args = nightly_args(tmp_path)
    with pytest.raises(Exception, match="wake models"):
        selftest._nightly_parts(args)
    code = selftest.main(args, out=lambda _l: None, err=lambda _l: None)
    result = json.loads((tmp_path / "selftest.json").read_text())
    assert code == 2 and result["reason"] == "ModelNotArmable"


# --- the loopback command contract ---

ECHO_SCRIPT = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"


def test_the_loopback_command_gets_a_wav_and_returns_the_capture():
    pcm = (np.arange(1600, dtype="<i2") * 3).tobytes()
    loopback = selftest.CommandLoopback([sys.executable, "-c", ECHO_SCRIPT])
    assert loopback.play_and_capture(pcm) == pcm


@pytest.mark.parametrize(
    "script", ["import sys; sys.exit(3)", "import sys; sys.stdout.write('not a wav')"]
)
def test_a_failing_loopback_command_is_refused(script):
    loopback = selftest.CommandLoopback([sys.executable, "-c", script])
    with pytest.raises(selftest.LoopbackFailed):
        loopback.play_and_capture(b"\x00\x00" * 160)


def test_a_missing_loopback_command_is_refused():
    with pytest.raises(selftest.LoopbackFailed):
        selftest.CommandLoopback("").play_and_capture(b"")
    loopback = selftest.CommandLoopback(["/nonexistent/friday-node-loopback"])
    with pytest.raises(selftest.LoopbackFailed):
        loopback.play_and_capture(b"\x00\x00")


def test_detections_use_the_pipelines_gate():
    loud = (np.full(config.SAMPLE_RATE, 5000, dtype="<i2")).tobytes()  # 1 s
    quiet = bytes(config.ms_to_bytes(3_000))
    hits = selftest.detections_in(Detector(), loud + quiet + loud)
    assert hits == [config.ms_to_bytes(20), config.ms_to_bytes(4_020)]
    assert selftest.detections_in(Detector(), b"") == []
