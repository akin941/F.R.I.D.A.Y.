"""The friday-voice/v1 conformance suite (brief D6): both families pass
against the reference implementations, in process and as real processes;
the runners catch a non-conforming peer; `--check` catches drift."""

import base64
import copy
import json
import shutil

import pytest

from friday_voice import cli
from friday_voice.child import ChildCore
from friday_voice.conformance import build, runner
from friday_voice.conformance.definitions import child_cases, host_cases
from friday_voice.reference import host as host_module

HOST_CASES = build.load_cases("host")
CHILD_CASES = build.load_cases("child")


def test_the_files_on_disk_are_the_definitions():
    assert build.check() == []
    assert [c["id"] for c in HOST_CASES] == sorted(c["id"] for c in host_cases())
    assert [c["id"] for c in CHILD_CASES] == sorted(c["id"] for c in child_cases())


@pytest.mark.parametrize("case", HOST_CASES, ids=lambda c: c["id"])
def test_every_host_case_passes_against_the_reference_host(case):
    assert runner.run_host_case(case) == []


@pytest.mark.parametrize("case", CHILD_CASES, ids=lambda c: c["id"])
def test_every_child_case_passes_against_the_reference_child(case):
    assert runner.run_child_case(case) == []


LIVE_HOST = [c for c in HOST_CASES if c["clock"] != "manual"]
LIVE_CHILD = [c for c in CHILD_CASES if c["clock"] != "manual"]


@pytest.mark.parametrize("case", LIVE_CHILD, ids=lambda c: c["id"])
def test_every_child_case_passes_against_a_real_friday_voice_run(case):
    assert runner.run_child_case_live(case) == []


@pytest.mark.parametrize("case", LIVE_HOST, ids=lambda c: c["id"])
def test_every_host_case_passes_with_the_hostile_child_as_a_real_process(case):
    assert runner.run_host_case_live(case) == []


def test_the_families_cover_the_brief():
    host_ids = {c["id"] for c in HOST_CASES}
    child_ids = {c["id"] for c in CHILD_CASES}
    # D6 host family: oversize, unknown type, policy-shaped name, play for an
    # unknown utterance, odd PCM, release on watchdog and on child exit.
    assert {
        "host.oversize_frame",
        "host.unknown_type",
        "host.policy_shaped_type",
        "host.policy_shaped_property",
        "host.play_unknown_utterance",
        "host.play_odd_pcm",
        "host.watchdog",
        "host.child_exit_backoff",
    } <= host_ids
    # D6 child family: release on every fail-closed input, nothing before credential.
    assert {
        "child.nothing_before_credential",
        "child.link_down_releases",
        "child.mute_releases",
        "child.mute_unreadable_releases",
        "child.oversize_frame",
        "child.stdin_eof",
        "child.credential_expiry",
    } <= child_ids


def test_every_live_case_is_synchronised():
    """A host-side step that follows a child_send needs an expect in
    between, or a real-time run would race (the Swift harness relies on
    this too)."""
    for case in HOST_CASES:
        pending_send = False
        for step in case["steps"]:
            if step["op"] == "child_send":
                pending_send = True
            elif step["op"] == "expect":
                pending_send = False
            elif step["op"] in ("host_local", "host_capture"):
                assert not pending_send, case["id"]


# --- negative controls: the runners catch a non-conforming peer ---


def test_a_host_without_a_watchdog_fails_its_case(monkeypatch):
    monkeypatch.setattr(host_module, "WATCHDOG_MS", 10**9)
    case = next(c for c in HOST_CASES if c["id"] == "host.watchdog")
    assert runner.run_host_case(case) != []


def test_a_host_that_trusts_the_child_on_link_loss_fails_its_case(monkeypatch):
    original = host_module.HostCore.set_link

    def lax(self, up):
        self.link_up = True  # forgets its own link state
        self._send({"type": "link", "state": "up" if up else "down"})

    monkeypatch.setattr(host_module.HostCore, "set_link", lax)
    case = next(c for c in HOST_CASES if c["id"] == "host.local_link_down")
    assert runner.run_host_case(case) != []
    monkeypatch.setattr(host_module.HostCore, "set_link", original)


def test_a_child_that_ignores_link_loss_fails_its_case(monkeypatch):
    monkeypatch.setattr(ChildCore, "_on_link", lambda self, header, payload: [])
    case = next(c for c in CHILD_CASES if c["id"] == "child.link_down_releases")
    assert runner.run_child_case(case) != []


def test_a_child_that_talks_before_its_credential_fails_its_case(monkeypatch):
    original = ChildCore.receive

    def chatty(self, header, payload=b""):
        out = original(self, header, payload)
        if header["type"] == "hello":
            out.append(({"type": "earcon", "earcon": "wake"}, b""))
        return out

    monkeypatch.setattr(ChildCore, "receive", chatty)
    case = next(c for c in CHILD_CASES if c["id"] == "child.nothing_before_credential")
    assert runner.run_child_case(case) != []


def test_a_wrong_expectation_is_reported():
    case = copy.deepcopy(next(c for c in HOST_CASES if c["id"] == "host.handshake"))
    case["steps"][-1]["mic"] = "released"
    assert runner.run_host_case(case) == ["step 3: mic is 'held', expected 'released'"]


# --- drift ---


def test_check_fails_on_a_changed_case_file(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    shutil.copytree(build.CASES_DIR, cases)
    vectors = tmp_path / "frames.json"
    shutil.copy(build.VECTORS_FILE, vectors)
    monkeypatch.setattr(build, "CASES_DIR", cases)
    monkeypatch.setattr(build, "VECTORS_FILE", vectors)
    monkeypatch.setattr(build, "PACKAGE_DIR", tmp_path / "package")
    assert build.check() == []

    target = cases / "host" / "host.watchdog.json"
    data = json.loads(target.read_text("utf-8"))
    data["steps"][4]["ms"] = 20000
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (cases / "child" / "child.extra.json").write_text("{}\n", encoding="utf-8")
    vectors.write_text(vectors.read_text("utf-8").replace("u-1", "u-2"), encoding="utf-8")
    assert sorted(build.check()) == [
        "cases/child/child.extra.json",
        "cases/host/host.watchdog.json",
        "frames.json",
    ]
    assert cli.main(["conformance", "--check"]) == 1


def test_the_cli_reports_all_pass(capsys):
    assert cli.main(["conformance", "--check"]) == 0
    assert cli.main(["conformance"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all-pass"


def test_frames_in_cases_are_base64_of_exact_bytes():
    for case in HOST_CASES + CHILD_CASES:
        for step in case["steps"]:
            if "frame_b64" in step:
                assert (
                    base64.b64encode(base64.b64decode(step["frame_b64"])).decode()
                    == (step["frame_b64"])
                )


def test_the_hostile_child_refuses_an_unknown_case(tmp_path):
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "friday_voice", "conformance-child", "--case", "nope"],
        capture_output=True,
        timeout=30,
    )
    assert done.returncode == 2
    assert b"unknown_case" in done.stderr
