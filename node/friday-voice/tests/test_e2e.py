"""The closeout e2e driver (`friday-voice e2e`): its wiring is checked here;
the scenarios themselves run against a live Friday in
`backend/ops/tests/test_live_voice_e2e.py` (the CI `friday-voice` job)."""

import sys

from friday_voice import cli, e2e, e2e_child
from friday_voice.ports import ManualClock
from friday_voice.replay import FIXTURE_WAKE_MODEL_SHA256


def test_the_cli_routes_e2e_and_refuses_an_unknown_scenario(tmp_path, capsys):
    token = tmp_path / "token"
    token.write_text("jpat_x\n", encoding="utf-8")
    code = cli.main(
        ["e2e", "--server", "http://127.0.0.1:1", "--token-file", str(token), "--scenario", "x"]
    )
    assert code == 2
    assert "unknown scenario" in capsys.readouterr().err


def test_every_scenario_has_a_runner():
    assert set(e2e.RUNNERS) == set(e2e.SCENARIOS)


def test_the_child_is_friday_voice_run_except_for_the_scripted_self_trigger():
    assert e2e.child_argv("ptt") == [sys.executable, "-m", "friday_voice", "run"]
    assert e2e.child_argv("selftrigger") == [
        sys.executable,
        "-m",
        "friday_voice.e2e_child",
        "--wake-model-sha256",
        FIXTURE_WAKE_MODEL_SHA256,
    ]


def test_the_credential_is_loopback_and_short_lived():
    credential = e2e._credential("http://127.0.0.1:8011/", "jpat_x")
    assert credential["server_url"] == "http://127.0.0.1:8011"
    assert credential["ca_sha256"] is None
    assert credential["expires_at"].endswith("Z")


def test_the_e2e_child_has_the_real_arming_policy_and_scripted_detectors():
    from friday_voice.arming import ArmingPolicy
    from friday_voice.wake import ScriptedWakeDetector

    pipeline = e2e_child.make_pipeline(ManualClock(), "c" * 64)(lambda _event: None)
    assert isinstance(pipeline.arming, ArmingPolicy)
    detector = pipeline.wake_gate.detector
    assert isinstance(detector, ScriptedWakeDetector)
    assert detector.model_sha256 == "c" * 64
    # Silent until Friday's first reply has played 400 ms.
    assert detector.score(b"\x00" * 640) == 0


def test_settled_waits_for_thinking_and_an_empty_playback_queue():
    class Core:
        playback_pending = False
        states = [{"state": "armed_ptt"}, {"state": "listening"}]

    core = Core()
    assert not e2e._settled_after(core, 0)
    core.states += [{"state": "thinking"}, {"state": "speaking"}, {"state": "armed_ptt"}]
    assert e2e._settled_after(core, 0)
    core.playback_pending = True
    assert not e2e._settled_after(core, 0)
    core.playback_pending = False
    core.states.append({"state": "storm_muted"})
    assert e2e._settled_after(core, 5)
