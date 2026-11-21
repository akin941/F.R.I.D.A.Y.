"""The `srt` escape suite: static check, verdict logic, fake run, opt-in
real run (Phase 10 brief D11, ADR-014 decision 3)."""

import copy
import json
import os
import shutil
import sys

import pytest
from django.core.management import CommandError, call_command

from nodes.conformance.escape import MARKER, load_cases
from nodes.conformance.escape.runner import (
    CaseVerdict,
    check_policy_document,
    check_policy_file,
    export_policies,
    run_suite,
)
from nodes.reference.executor import CommandOutcome, SrtExecutor

CASES = load_cases()
CATEGORIES = {case["category"] for case in CASES}


def test_the_suite_covers_every_d11_family():
    assert CATEGORIES == {"write_outside", "read_denied", "network", "environment", "limits"}
    ids = {case["id"] for case in CASES}
    for required in (
        "write_home",
        "write_repo_parent",
        "write_tmp",
        "write_private_tmp",
        "write_through_tmp_symlink",
        "write_through_worktree_symlink",
        "net_tcp",
        "net_udp",
        "net_dns",
        "net_loopback",
        "net_hook_socket",
        "env_github_token",
        "limit_processes",
        "limit_output",
        "limit_runtime",
    ):
        assert required in ids
    from nodes.reference.sandbox import DENY_READ_HOME

    read_targets = {c["attempt"]["path"] for c in CASES if c["attempt"]["kind"] == "read"}
    for rel in (
        ".ssh",
        ".aws",
        ".config/gh",
        ".claude",
        ".codex",
        ".gnupg",
        ".netrc",
        ".docker",
        "Library/Keychains",
        "Library/Safari",
    ):
        assert rel in DENY_READ_HOME
        assert any(t.startswith("{home}/" + rel) for t in read_targets), rel


def test_the_fake_run_contains_every_case(tmp_path):
    report = run_suite("fake", base_dir=tmp_path)
    assert report.passed, report.table()
    assert len(report.verdicts) == len(CASES)
    assert all(v.static_ok for v in report.verdicts)
    assert report.table().endswith("ALL CONTAINED")


def test_the_static_check_flags_a_policy_that_does_not_cover_a_case(tmp_path):
    exported = export_policies(tmp_path)
    context = exported["context"]
    by_id = {case["id"]: case for case in CASES}
    leaky = copy.deepcopy(exported["documents"]["write_home"])
    leaky["settings"]["filesystem"]["allowWrite"].append(context["home"])
    assert check_policy_document(by_id["write_home"], leaky, context)
    tmp_writable = copy.deepcopy(exported["documents"]["write_tmp"])
    tmp_writable["settings"]["filesystem"]["allowWrite"].append("/tmp")
    assert check_policy_document(by_id["write_tmp"], tmp_writable, context)
    unread = copy.deepcopy(exported["documents"]["read_ssh"])
    unread["settings"]["filesystem"]["denyRead"].remove(context["home"] + "/.ssh")
    assert check_policy_document(by_id["read_ssh"], unread, context)
    net = copy.deepcopy(exported["documents"]["net_tcp"])
    net["settings"]["network"]["allowedDomains"] = ["1.1.1.1"]
    assert check_policy_document(by_id["net_tcp"], net, context)
    env = copy.deepcopy(exported["documents"]["env_github_token"])
    env["process"]["env"]["GITHUB_TOKEN"] = "x"
    assert check_policy_document(by_id["env_github_token"], env, context)
    limits = copy.deepcopy(exported["documents"]["limit_processes"])
    limits["limits"]["max_processes"] = 10_000
    assert check_policy_document(by_id["limit_processes"], limits, context)
    in_place = copy.deepcopy(exported["documents"]["write_home"])
    in_place["mode"] = "in_place"
    assert check_policy_document(by_id["write_home"], in_place, context)


def test_check_policy_round_trip_and_command(tmp_path):
    exported = export_policies(tmp_path)
    assert all(not reasons for _, reasons in check_policy_file(exported))
    path = tmp_path / "policies.json"
    call_command("node_escape_suite", "--export-policy", str(path))
    call_command("node_escape_suite", "--check-policy", str(path))
    document = json.loads(path.read_text())
    del document["documents"]["net_dns"]
    document["documents"]["write_home"]["settings"]["filesystem"]["allowWrite"].append(
        document["context"]["home"]
    )
    path.write_text(json.dumps(document))
    with pytest.raises(CommandError):
        call_command("node_escape_suite", "--check-policy", str(path))


class _ScriptedExecutor:
    """Scripted executor results for the verdict logic."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.case = self.context = None

    def prepare(self, case, context):
        self.case, self.context = case, context

    def start(self, spec):
        return _Run(self.behaviour(self.case, self.context, spec))


class _Run:
    def __init__(self, result):
        self.chunks, self.outcome = result
        self.terminated = False

    def read(self):
        chunks, self.chunks = self.chunks, []
        return chunks

    def poll(self):
        return CommandOutcome(exit_code=-15) if self.terminated else self.outcome

    def terminate(self):
        self.terminated = True


def _one(case_id):
    return [case for case in CASES if case["id"] == case_id]


def test_a_reported_escape_fails_the_case(tmp_path):
    executor = _ScriptedExecutor(
        lambda c, x, s: ([("stdout", (MARKER + "\n").encode())], CommandOutcome(0))
    )
    report = run_suite("fake", base_dir=tmp_path, cases=_one("net_tcp"), executor=executor)
    assert not report.passed
    assert "the script reported an escape" in report.verdicts[0].reasons


def test_a_write_target_left_behind_fails_the_case(tmp_path):
    def behaviour(case, context, spec):
        target = (
            case["attempt"]["path"]
            .replace("{home}", context["home"])
            .replace("{run}", context["run"])
        )
        with open(target, "w") as handle:
            handle.write("x")
        return [], CommandOutcome(0)

    report = run_suite(
        "fake", base_dir=tmp_path, cases=_one("write_home"), executor=_ScriptedExecutor(behaviour)
    )
    assert not report.passed
    assert "a write target outside the worktree exists" in report.verdicts[0].reasons


def test_a_command_that_outlives_the_cap_is_stopped_and_contained(tmp_path):
    report = run_suite(
        "fake",
        base_dir=tmp_path,
        cases=_one("limit_runtime"),
        executor=_ScriptedExecutor(lambda c, x, s: ([], None)),
    )
    assert report.passed, report.table()


def test_a_runtime_case_that_finishes_on_its_own_fails(tmp_path):
    report = run_suite(
        "fake",
        base_dir=tmp_path,
        cases=_one("limit_runtime"),
        executor=_ScriptedExecutor(lambda c, x, s: ([], CommandOutcome(0))),
    )
    assert not report.passed


def test_runaway_output_is_truncated_by_the_pipeline(tmp_path):
    chunk = b"y" * 65536
    report = run_suite(
        "fake",
        base_dir=tmp_path,
        cases=_one("limit_output"),
        executor=_ScriptedExecutor(lambda c, x, s: ([("stdout", chunk)] * 40, CommandOutcome(0))),
    )
    assert report.passed, report.table()
    short = run_suite(
        "fake",
        base_dir=tmp_path / "b",
        cases=_one("limit_output"),
        executor=_ScriptedExecutor(lambda c, x, s: ([("stdout", b"y")], CommandOutcome(0))),
    )
    assert "runaway output was not truncated" in short.verdicts[0].reasons


def test_verdict_passes_only_when_static_and_run_hold():
    assert CaseVerdict("a", "x", True, True).passed
    assert not CaseVerdict("a", "x", False, True).passed
    assert not CaseVerdict("a", "x", True, False).passed


def test_without_a_sandbox_the_scripts_really_escape(tmp_path):
    """An UNSANDBOXED run (test-only: SrtExecutor with no srt prefix) proves
    the scripts are real attempts: a write to home and a canary read
    succeed, so the verdict logic catches them; the environment case stays
    contained because the generated environment never carries the token."""
    executor = SrtExecutor(srt_path=sys.executable, argv_template=())
    cases = [c for c in CASES if c["id"] in ("write_home", "read_ssh", "env_github_token")]
    report = run_suite("srt", base_dir=tmp_path, cases=cases, executor=executor)
    by_id = {v.id: v for v in report.verdicts}
    assert not by_id["write_home"].contained
    assert not by_id["read_ssh"].contained
    assert by_id["env_github_token"].contained
    assert all(v.static_ok for v in report.verdicts)
    assert not os.path.exists(str(tmp_path / "home" / "work" / "acme" / ".wt"))


def test_node_escape_suite_command_fake_passes(capsys):
    call_command("node_escape_suite")
    assert "ALL CONTAINED" in capsys.readouterr().out


def test_node_escape_suite_srt_refuses_without_srt(monkeypatch):
    monkeypatch.setattr(SrtExecutor, "available", classmethod(lambda cls: False))
    with pytest.raises(CommandError):
        call_command("node_escape_suite", "--executor", "srt")


@pytest.mark.srt
@pytest.mark.skipif(shutil.which("srt") is None, reason="srt is not installed")
def test_the_escape_suite_under_real_srt(tmp_path):  # pragma: no cover - opt-in
    report = run_suite("srt", base_dir=tmp_path)
    assert report.passed, report.table()
