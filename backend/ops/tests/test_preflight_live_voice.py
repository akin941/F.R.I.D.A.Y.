"""`manage.py preflight` live-voice checks (Phase 11 closeout, brief §8):
`voice_live_channel`, `voice_live_policy`, `voice_live_storm`,
`voice_live_selftest`, `friday_voice_conformance` and `node_voice_state` --
each passes, warns and fails with fakes. The route walk is the real URL
table (or the seam `drf_routes` with a leaking view added); the conformance
check hashes the committed `node/friday-voice` files (or a copy in a temp
dir, seam `friday_voice_dir` / `PREFLIGHT_FRIDAY_VOICE_DIR`). No test
reaches a Mac, a node, a model or the network."""

import datetime
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.views import APIView

from accounts import devices, voice_live
from accounts.authentication import NodeVoiceTokenAuthentication
from accounts.models import (
    DevicePairingCode,
    TrustedDevice,
    VoiceLiveMute,
)
from accounts.services import register_user
from nodes import bus
from nodes.services import pair_node
from ops import preflight
from ops.preflight import FAIL, PASS, WARN

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
MODEL = "a" * 64
OTHER_MODEL = "b" * 64
REPO_FRIDAY_VOICE = Path(preflight.settings.BASE_DIR).resolve().parent / "node" / "friday-voice"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def paired(owner):
    made = []

    def make(name="MacBook", *, present=True):
        raw_code, _ = devices.issue_pairing_code(
            owner=owner,
            kind=DevicePairingCode.Kind.STRONG,
            issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            device_kind=TrustedDevice.Kind.NODE,
        )
        node = pair_node(raw_code=raw_code, node_name=name, protocol_versions=[1, 2]).node
        made.append(node)
        if present:
            bus.mark_present(node.pk)
        return node

    yield make
    for node in made:
        bus.clear_presence(node.pk)


@pytest.fixture(autouse=True)
def quiet_settings(settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = False
    settings.VOICE_LIVE_SELFTEST_RESULT_PATH = ""
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = ""


def _rows(name, results):
    return [r for r in results if r.name == name or r.name.startswith(name + ":")]


def _one(name):
    (row,) = _rows(name, preflight.check_live_voice())
    return row


# --- registration -----------------------------------------------------------


def test_the_live_voice_section_is_registered_after_the_control_checks():
    names = [name for name, _ in preflight.CHECKS]
    assert names.index("live_voice") == names.index("control") + 1
    assert ("live_voice", preflight.check_live_voice) in preflight.CHECKS


def test_a_fresh_stack_is_green_apart_from_the_documented_mac_warnings(owner):
    results = preflight.check_live_voice()
    statuses = {r.name: r.status for r in results}
    assert statuses == {
        "voice_live_channel": PASS,
        "voice_live_policy": WARN,  # no evaluation recorded: pending owner evaluation
        "voice_live_storm": PASS,
        "voice_live_selftest": WARN,  # the nightly run is the Mac session (M3)
        "friday_voice_conformance": PASS,  # this checkout carries node/friday-voice
        "node_voice_state": WARN,  # no node paired: the Swift host is M1
    }
    text = " ".join(r.detail for r in results)
    assert "Mac session pending owner evaluation" in text


def test_one_crashing_live_voice_check_is_its_own_fail_row(owner, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(preflight, "drf_routes", broken)
    results = preflight.check_live_voice()
    channel = _rows("voice_live_channel", results)
    assert [(r.status, r.detail) for r in channel] == [(FAIL, "check crashed: RuntimeError")]
    assert len(results) == 6  # the others still report


# --- voice_live_channel -----------------------------------------------------


def test_channel_passes_on_the_real_url_table():
    row = _one("voice_live_channel")
    assert row.status == PASS
    assert row.detail == "node_voice reaches exactly the 6 D4 views; token TTL 900s"


def test_the_route_walk_finds_every_d4_view():
    names = {preflight._view_name(view) for _, view, _ in preflight.drf_routes()}
    assert preflight.NODE_VOICE_SURFACES <= names


class LeakingView(APIView):
    authentication_classes = [NodeVoiceTokenAuthentication]


def test_channel_fails_when_a_voice_token_reaches_a_forbidden_surface(monkeypatch):
    real = preflight.drf_routes()
    monkeypatch.setattr(
        preflight,
        "drf_routes",
        lambda: [*real, ("api/v1/approvals/leak", LeakingView, [NodeVoiceTokenAuthentication])],
    )
    row = _one("voice_live_channel")
    assert row.status == FAIL
    assert "node_voice reaches 1 view(s) outside D4" in row.detail
    assert "test_preflight_live_voice.LeakingView" in row.detail


def test_channel_fails_when_a_d4_view_lost_the_authenticator(monkeypatch):
    real = preflight.drf_routes()
    monkeypatch.setattr(
        preflight,
        "drf_routes",
        lambda: [r for r in real if r[1].__name__ != "SpeechCreateView"],
    )
    row = _one("voice_live_channel")
    assert row.status == FAIL
    assert "1 D4 view(s) not routed with it: voice.views.SpeechCreateView" in row.detail


@pytest.mark.parametrize("ttl", [3601, 0, None])
def test_channel_fails_on_a_token_ttl_above_one_hour(settings, ttl):
    settings.VOICE_LIVE_TOKEN_TTL_SECONDS = ttl
    row = _one("voice_live_channel")
    assert row.status == FAIL
    assert f"VOICE_LIVE_TOKEN_TTL_SECONDS={ttl} is outside 1..3600" in row.detail


def test_channel_passes_at_one_hour(settings):
    settings.VOICE_LIVE_TOKEN_TTL_SECONDS = 3600
    assert _one("voice_live_channel").status == PASS


def test_channel_fails_when_node_voice_becomes_mintable(monkeypatch):
    from accounts import models

    monkeypatch.setattr(models, "MINTABLE_PAT_KINDS", (*models.MINTABLE_PAT_KINDS, "node_voice"))
    row = _one("voice_live_channel")
    assert row.status == FAIL
    assert "node_voice is mintable through /auth/tokens" in row.detail


def test_channel_fails_when_the_kind_maps_to_another_channel(monkeypatch):
    from accounts import authentication

    monkeypatch.setitem(authentication.KIND_CHANNELS, "node_voice", "web")
    row = _one("voice_live_channel")
    assert row.status == FAIL
    assert "does not authenticate as the node_voice channel" in row.detail


# --- voice_live_policy ------------------------------------------------------


def _record(owner, kind, *, model=MODEL, threshold="0.5", **numbers):
    """Rows are append-only: a later call is the later row."""
    return voice_live.record_evaluation(
        owner=owner,
        model_sha256=model,
        kind=kind,
        threshold=threshold,
        refractory_ms=2000,
        **numbers,
    )


def _passing_pair(owner, *, model=MODEL):
    _record(owner, "household", model=model, hours=24, false_accepts=2)  # 0.0833/h
    _record(owner, "positives", model=model, positives=50, false_rejects=3)  # 0.06


def test_policy_warns_while_nothing_is_recorded(owner):
    row = _one("voice_live_policy")
    assert row.status == WARN
    assert row.detail == (
        "always-on not permitted: no_household_eval, no_positive_eval "
        "(0 wake model(s) measured; Mac session pending owner evaluation)"
    )


def test_policy_fails_when_always_on_is_enabled_without_evidence(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    row = _one("voice_live_policy")
    assert row.status == FAIL
    assert row.detail.startswith("VOICE_LIVE_ALWAYS_ON_ENABLED=on but no model's latest")


def test_policy_fails_when_enabled_with_far_above_budget(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    _record(owner, "household", hours=24, false_accepts=12)  # 0.5/h > 0.2
    _record(owner, "positives", positives=50, false_rejects=3)
    row = _one("voice_live_policy")
    assert row.status == FAIL
    assert "far_above_budget" in row.detail


def test_policy_passes_with_a_passing_latest_pair(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    _passing_pair(owner)
    row = _one("voice_live_policy")
    assert row.status == PASS
    assert row.detail == (
        "VOICE_LIVE_ALWAYS_ON_ENABLED=on; model aaaaaaaaaaaa passes at threshold 0.5000 / 2000 ms"
    )


def test_policy_passes_with_evidence_while_the_setting_stays_off(owner):
    _passing_pair(owner)
    row = _one("voice_live_policy")
    assert row.status == PASS
    assert "always-on stays off until the setting is enabled" in row.detail


def test_an_earlier_passing_row_superseded_by_a_later_failing_one_does_not_count(owner, settings):
    _passing_pair(owner)
    _record(owner, "household", hours=24, false_accepts=24)  # the latest: 1/h
    row = _one("voice_live_policy")
    assert row.status == WARN
    assert "far_above_budget" in row.detail
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    assert _one("voice_live_policy").status == FAIL


def test_policy_needs_both_latest_rows_at_the_same_threshold(owner):
    _record(owner, "household", hours=24, false_accepts=2)
    _record(owner, "positives", positives=50, false_rejects=3, threshold="0.45")
    row = _one("voice_live_policy")
    assert row.status == WARN
    assert "threshold_mismatch" in row.detail


def test_policy_passes_when_any_model_passes(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    _record(owner, "household", model=OTHER_MODEL, hours=24, false_accepts=24)
    _passing_pair(owner)
    row = _one("voice_live_policy")
    assert row.status == PASS
    assert "model aaaaaaaaaaaa" in row.detail


def test_policy_is_the_endpoint_verdict_not_a_copy(owner, monkeypatch):
    """The check calls `accounts.voice_live.model_verdicts`: replacing it
    changes the check's answer."""
    _passing_pair(owner)
    calls = []
    real = voice_live.model_verdicts

    def spy(actor, workspace_id):
        calls.append((actor.pk, workspace_id))
        return real(actor, workspace_id)

    monkeypatch.setattr(voice_live, "model_verdicts", spy)
    assert _one("voice_live_policy").status == PASS
    assert calls and calls[0][0] == owner.pk


# --- voice_live_storm -------------------------------------------------------


def test_storm_passes_without_an_engaged_mute(owner, paired):
    node = paired()
    VoiceLiveMute.objects.create(
        workspace_id=_personal(owner),
        node_device=node.device,
        released_at=timezone.now(),
        released_by=owner,
    )
    row = _one("voice_live_storm")
    assert (row.status, row.detail) == (PASS, "no storm mute engaged")


def _personal(owner):
    return voice_live.personal_workspace_id(owner)


def test_an_engaged_mute_is_a_warning_with_its_age(owner, paired):
    node = paired()
    VoiceLiveMute.objects.create(
        workspace_id=_personal(owner),
        node_device=node.device,
        engaged_at=timezone.now() - datetime.timedelta(minutes=7),
    )
    row = _one("voice_live_storm")
    assert row.name == "voice_live_storm:MacBook"
    assert row.status == WARN
    assert row.detail == "storm mute (stt_storm) engaged 7 min ago: release it on screen (/voice)"


# --- voice_live_selftest ----------------------------------------------------


def _result_line(result="pass", *, mode="nightly", hours_ago=2, **extra):
    at = timezone.now() - datetime.timedelta(hours=hours_ago)
    document = {
        "schema": "friday-voice/selftest/v1",
        "mode": mode,
        "result": result,
        "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wakes": 0,
        "posted_turns": 0,
        "model_sha256": MODEL,
        "threshold": 500,
        "refractory_ms": 2000,
        "reason": "",
        **extra,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"


@pytest.fixture
def result_file(tmp_path, settings):
    path = tmp_path / "selftest.json"
    settings.VOICE_LIVE_SELFTEST_RESULT_PATH = str(path)
    return path


def test_selftest_warns_when_not_configured():
    row = _one("voice_live_selftest")
    assert row.status == WARN
    assert "VOICE_LIVE_SELFTEST_RESULT_PATH not set" in row.detail


def test_selftest_warns_while_no_nightly_result_exists(result_file):
    row = _one("voice_live_selftest")
    assert (row.status, row.detail) == (
        WARN,
        "no nightly result yet (Mac session pending owner evaluation)",
    )


def test_selftest_passes_on_a_fresh_green_night(result_file):
    result_file.write_text(_result_line(), encoding="utf-8")
    row = _one("voice_live_selftest")
    assert row.status == PASS
    assert row.detail == "nightly run 2 h ago: pass (wakes 0, posted turns 0)"
    assert str(result_file) not in row.detail


def test_selftest_warns_on_a_stale_green_night(result_file):
    result_file.write_text(_result_line(hours_ago=49), encoding="utf-8")
    row = _one("voice_live_selftest")
    assert row.status == WARN
    assert "stale: older than 48 h" in row.detail


def test_selftest_fails_when_the_last_night_failed(result_file):
    lines = _result_line(hours_ago=26) + _result_line(
        "fail", hours_ago=1, wakes=1, posted_turns=1, reason="posted_turn"
    )
    result_file.write_text(lines, encoding="utf-8")
    row = _one("voice_live_selftest")
    assert row.status == FAIL
    assert row.detail == ("nightly run 1 h ago: fail (wakes 1, posted turns 1), reason posted_turn")


def test_selftest_fails_when_the_last_night_could_not_judge(result_file):
    result_file.write_text(_result_line("error", reason="loopback_failed"), encoding="utf-8")
    assert _one("voice_live_selftest").status == FAIL


@pytest.mark.parametrize(
    "text",
    ["", "not json\n", '{"schema": "other/v1", "result": "pass"}\n', "[1, 2]\n"],
)
def test_selftest_fails_on_a_result_file_that_is_not_a_result_line(result_file, text):
    result_file.write_text(text, encoding="utf-8")
    row = _one("voice_live_selftest")
    assert row.status == FAIL
    assert "is not a friday-voice/selftest/v1 line" in row.detail


def test_selftest_fails_on_a_bad_timestamp(result_file):
    result_file.write_text(_result_line(at="yesterday"), encoding="utf-8")
    assert _one("voice_live_selftest").status == FAIL


def test_selftest_warns_on_a_ci_result_in_place_of_a_nightly_one(result_file):
    result_file.write_text(_result_line(mode="ci"), encoding="utf-8")
    row = _one("voice_live_selftest")
    assert row.status == WARN
    assert "not a nightly run" in row.detail


# --- friday_voice_conformance -----------------------------------------------


@pytest.fixture
def checkout(tmp_path, settings):
    """A copy of the committed lock and the files it covers."""
    root = tmp_path / "friday-voice"
    for rel in ("friday_voice/conformance/cases", "friday_voice/protocol/schemas"):
        shutil.copytree(REPO_FRIDAY_VOICE / rel, root / rel)
    vectors = root / "friday_voice/protocol/vectors"
    vectors.mkdir(parents=True)
    shutil.copy(REPO_FRIDAY_VOICE / "friday_voice/protocol/vectors/frames.json", vectors)
    shutil.copy(REPO_FRIDAY_VOICE / "conformance.lock", root / "conformance.lock")
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = str(root)
    return root


def test_conformance_passes_on_the_committed_checkout():
    row = _one("friday_voice_conformance")
    assert row.status == PASS
    host = len(list((REPO_FRIDAY_VOICE / "friday_voice/conformance/cases/host").glob("*.json")))
    child = len(list((REPO_FRIDAY_VOICE / "friday_voice/conformance/cases/child").glob("*.json")))
    assert f"({host} host + {child} child cases, vectors, schemas)" in row.detail


def test_conformance_passes_on_a_faithful_copy(checkout):
    assert _one("friday_voice_conformance").status == PASS


def test_conformance_warns_where_no_checkout_exists(settings, tmp_path):
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = str(tmp_path / "absent")
    row = _one("friday_voice_conformance")
    assert (row.status, row.detail) == (
        WARN,
        "conformance not checkable here (no node/friday-voice)",
    )


def test_conformance_drift_fails(checkout):
    case = next((checkout / "friday_voice/conformance/cases/host").glob("*.json"))
    case.write_text(case.read_text("utf-8").replace('"', "'", 1), encoding="utf-8")
    row = _one("friday_voice_conformance")
    assert row.status == FAIL
    assert f"friday_voice/conformance/cases/host/{case.name}" in row.detail
    assert "run friday-voice conformance --check" in row.detail


def test_an_unlocked_extra_case_file_fails(checkout):
    (checkout / "friday_voice/conformance/cases/child/child.extra.json").write_text("{}\n")
    row = _one("friday_voice_conformance")
    assert row.status == FAIL
    assert "child.extra.json" in row.detail


def test_a_missing_locked_file_fails(checkout):
    (checkout / "friday_voice/protocol/vectors/frames.json").unlink()
    row = _one("friday_voice_conformance")
    assert row.status == FAIL
    assert "frames.json" in row.detail


def test_a_missing_lock_fails(checkout):
    (checkout / "conformance.lock").unlink()
    row = _one("friday_voice_conformance")
    assert row.status == FAIL
    assert "conformance.lock missing" in row.detail


@pytest.mark.parametrize(
    "text",
    ["# only a comment\n", "not-a-hash  some/path\n", f"{'0' * 64}  ../../etc/passwd\n"],
)
def test_a_malformed_lock_fails(checkout, text):
    (checkout / "conformance.lock").write_text(text, encoding="utf-8")
    row = _one("friday_voice_conformance")
    assert row.status == FAIL
    assert "malformed or empty" in row.detail


def test_the_lock_lists_what_the_check_hashes():
    lock = (REPO_FRIDAY_VOICE / "conformance.lock").read_text("utf-8")
    entries = preflight._read_lock(REPO_FRIDAY_VOICE / "conformance.lock")
    assert len(entries) == len([line for line in lock.splitlines() if line[:1] not in ("#", "")])
    some = next(iter(entries))
    digest = hashlib.sha256((REPO_FRIDAY_VOICE / some).read_bytes()).hexdigest()
    assert entries[some] == digest


# --- node_voice_state -------------------------------------------------------


def test_node_voice_state_warns_without_a_paired_node(owner):
    row = _one("node_voice_state")
    assert (row.status, row.detail) == (
        WARN,
        "no node paired (Mac session pending owner evaluation)",
    )


def test_node_voice_state_warns_while_no_node_reports(owner, paired):
    paired()
    rows = _rows("node_voice_state", preflight.check_live_voice())
    assert [(r.name, r.status) for r in rows] == [
        ("node_voice_state:MacBook", WARN),
        ("node_voice_state", WARN),
    ]
    assert "no voice.state reported yet" in rows[0].detail
    assert rows[1].detail == "no node reports voice.state (Mac session pending owner evaluation)"


def test_node_voice_state_passes_for_a_reporting_node(owner, paired):
    node = paired()
    paired("Mac mini")
    node.voice_state = "armed_ptt"
    node.voice_mode = "push_to_talk"
    node.voice_state_at = timezone.now() - datetime.timedelta(seconds=30)
    node.voice_wake_accepts_today = 3
    node.save()
    rows = _rows("node_voice_state", preflight.check_live_voice())
    assert [(r.name, r.status) for r in rows] == [
        ("node_voice_state:MacBook", PASS),
        ("node_voice_state:Mac mini", WARN),
    ]
    assert rows[0].detail.startswith("connected, state ")
    assert "mode push_to_talk, wake accepts 3" in rows[0].detail


def test_node_voice_state_skips_a_revoked_node(owner, paired):
    node = paired()
    node.voice_state = "speaking"
    node.voice_state_at = timezone.now()
    node.save()
    devices.revoke_device(device=node.device, actor=owner)
    rows = _rows("node_voice_state", preflight.check_live_voice())
    assert [(r.name, r.status, r.detail) for r in rows] == [
        ("node_voice_state", WARN, "no node paired (Mac session pending owner evaluation)")
    ]
