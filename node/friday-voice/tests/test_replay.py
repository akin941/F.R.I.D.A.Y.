"""The replay harness (brief D14): the fixture set runs under a manual
clock, reports per-stage numbers, and a pipeline overhead regression of
more than 20 % fails it; fixtures are synthetic and checked for drift."""

import copy
import wave

import pytest
from conftest import PACKAGE_ROOT, load_script

from friday_voice import cli, config, replay

make_fixtures = load_script("make_fixtures")


@pytest.fixture(scope="module")
def results():
    return [replay.run_scenario(scenario) for scenario in replay.load_scenarios()]


def test_every_fixture_scenario_completes_a_turn_with_audio(results):
    assert len(results) >= 5
    for result in results:
        assert result["turns"] == 1, result["id"]
        assert result["played_utterances"] == len(result["spoken"]) > 0, result["id"]
    draft = next(r for r in results if r["id"] == "wake_node_action_draft")
    assert draft["spoken"] == [config.DRAFT_SENTENCE]


def test_the_report_has_every_stage_and_the_scripted_latencies(results):
    current = replay.report(results)
    assert set(current["stages"]) == set(replay.STAGES)
    scenario = replay.load_scenarios()[0]
    assert current["stages"]["stt_ms"]["p50"] == scenario["latencies"]["stt"]
    assert current["stages"]["tts_first_ms"]["p50"] == scenario["latencies"]["tts"]
    for result in results:
        stages = result["stages"]
        assert stages["playback_start_ms"] == stages["server_ms"] + stages["overhead_ms"]


def test_the_report_matches_the_stored_baseline(results):
    assert replay.check(replay.report(results), replay.load_baseline()) == []
    baseline = replay.load_baseline()
    assert replay.report(results)["stages"] == baseline["stages"]


def test_a_synthetic_25_percent_overhead_regression_fails(results):
    current = copy.deepcopy(replay.report(results))
    baseline = replay.load_baseline()
    base = baseline["stages"]["overhead_ms"]["p50"]
    current["stages"]["overhead_ms"]["p50"] = base * 1.25
    (failure,) = replay.check(current, baseline)
    assert "overhead" in failure
    current["stages"]["overhead_ms"]["p50"] = base * 1.2
    assert replay.check(current, baseline) == []


def test_a_real_regression_in_the_pipeline_fails_the_check(monkeypatch):
    # Waiting 25 % longer before asking smart-turn is pipeline overhead.
    monkeypatch.setattr(
        config, "TURN_TRAILING_SILENCE_MS", int(config.TURN_TRAILING_SILENCE_MS * 1.25) + 20
    )
    slow = [replay.run_scenario(scenario) for scenario in replay.load_scenarios()]
    assert replay.check(replay.report(slow), replay.load_baseline())


def test_a_missing_number_fails_the_check():
    assert replay.check({"stages": {}}, replay.load_baseline())


def test_replay_cli_check_passes(capsys):
    assert cli.main(["replay", "--check"]) == 0
    out = capsys.readouterr().out
    assert "overhead_ms" in out and "FAIL" not in out


def test_percentile_is_nearest_rank():
    assert replay.percentile([5, 1, 3, 2, 4], 50) == 3
    assert replay.percentile([1, 2, 3, 4], 50) == 2
    assert replay.percentile([1, 2, 3, 4], 95) == 4


def test_the_fixtures_are_synthetic_and_up_to_date():
    assert make_fixtures.main(["--check"]) == 0
    for path in (PACKAGE_ROOT / "fixtures").glob("*.wav"):
        with wave.open(str(path), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            assert wav.getnframes() <= 16000 * 10


def test_the_fixture_check_catches_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(make_fixtures, "FIXTURES", tmp_path)
    assert make_fixtures.main(["--check"]) == 1


# --- barge-in and echo scripts (D9, D10) ---


@pytest.fixture(scope="module")
def barge_in_results():
    return [replay.run_barge_in_scenario(s) for s in replay.load_barge_in_scenarios()]


def test_the_barge_in_scripts_pass_at_every_residual(barge_in_results):
    by_residual = {}
    for scenario, result in zip(replay.load_barge_in_scenarios(), barge_in_results, strict=True):
        assert result["failures"] == [], result["id"]
        by_residual.setdefault(scenario["residual"], []).append(scenario["id"])
    assert sorted(by_residual) == [0.0, 0.3, 1.0]
    assert all(len(ids) == 2 for ids in by_residual.values())  # owner + echo each


def test_barge_in_stops_within_the_budget(barge_in_results):
    stops = [r["stop_ms"] for r in barge_in_results if r["stop_ms"] is not None]
    assert stops and max(stops) <= config.BARGE_IN_STOP_BUDGET_MS == 100
    owner = [r for r in barge_in_results if r["id"].startswith("owner_")]
    assert all(r["interrupted_at_ms"] >= config.BARGE_IN_AEC_GUARD_MS for r in owner)


def test_a_missed_expectation_or_a_slow_stop_fails(monkeypatch):
    scenario = copy.deepcopy(replay.load_barge_in_scenarios()[0])
    scenario["expect"]["chat_turns"] = 5
    (failure,) = replay.run_barge_in_scenario(scenario)["failures"]
    assert "chat_turns" in failure
    monkeypatch.setattr(config, "BARGE_IN_STOP_BUDGET_MS", -1)
    scenario = replay.load_barge_in_scenarios()[0]
    assert any("stop_playback" in f for f in replay.run_barge_in_scenario(scenario)["failures"])


def test_replay_cli_fails_on_a_barge_in_failure(monkeypatch, capsys):
    monkeypatch.setattr(config, "BARGE_IN_STOP_BUDGET_MS", -1)
    assert cli.main(["replay"]) == 1
    assert "FAIL owner_r00" in capsys.readouterr().out
