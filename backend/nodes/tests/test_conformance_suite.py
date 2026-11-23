"""The conformance suite v1 + v2 against the reference node, in process and
over a localhost `wsproto` socket (Phase 10 brief branch 2 "Done when")."""

import copy
import json
import threading

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from nodes.conformance import build
from nodes.conformance import cases as C
from nodes.conformance.gateway import (
    ConformanceGateway,
    ExternalHook,
    HookClient,
    play_external,
    run_case_over_socket,
    self_signed_context,
)
from nodes.conformance.runner import GatewayChecker, format_table, run_case_in_process
from nodes.keys import SigningKeyUnavailable, published_test_key_ids, signing_keyring
from nodes.protocol.messages import NODE_CHECKS_V2
from nodes.reference.fixtures.factory import build_fixture_home

ALL = [case for cases in C.all_cases().values() for case in cases]


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    return build_fixture_home(tmp_path_factory.mktemp("conformance-home"))


@pytest.fixture(scope="module")
def worktrees(tmp_path_factory):
    return tmp_path_factory.mktemp("conformance-worktrees")


@pytest.mark.parametrize("case", ALL, ids=lambda c: c["name"])
def test_every_case_passes_in_process(case, home, worktrees):
    result = run_case_in_process(case, home=home, worktree_base=worktrees)
    assert result.passed, result.failures


def test_every_case_passes_over_a_localhost_wsproto_socket(home, worktrees):
    gateway = ConformanceGateway()
    try:
        results = [
            run_case_over_socket(gateway, case, home=home, worktree_base=worktrees) for case in ALL
        ]
    finally:
        gateway.close()
    table = format_table(results)
    assert all(r.passed for r in results), table
    assert table.endswith("ALL PASS")
    assert f"v1: {len(C.all_cases()[1])}/{len(C.all_cases()[1])} passed" in table
    assert f"v2: {len(C.all_cases()[2])}/{len(C.all_cases()[2])} passed" in table


def test_cases_over_tls_with_a_pinned_self_signed_certificate(home, worktrees):
    server, client, _ = self_signed_context()
    gateway = ConformanceGateway(ssl_context=server)
    names = {"v2_gate_command_runs_after_click", "v2_owner_times_out", "v1_read_ok"}
    try:
        for case in [c for c in ALL if c["name"] in names]:
            result = run_case_over_socket(
                gateway, case, home=home, worktree_base=worktrees, client_ssl=client
            )
            assert result.passed, result.failures
    finally:
        gateway.close()


def test_the_external_hook_path(home, worktrees, tmp_path):
    gateway = ConformanceGateway()
    hook = ExternalHook(str(tmp_path / "hook.sock"))
    client = HookClient(hook.path, gateway.host, gateway.port, home=home, worktree_base=worktrees)
    waiter = threading.Thread(target=hook.wait_for_node, daemon=True)
    waiter.start()
    client.start()
    waiter.join(10)
    names = ["v2_socket_dropped_while_running", "v2_kill_switch_closes_window", "v1_replay"]
    try:
        for case in [c for c in ALL if c["name"] in names]:
            result = play_external(gateway, hook, case)
            assert result.passed, result.failures
    finally:
        hook.close()
        gateway.close()
        client.join(5)
    assert client.error is None


def test_a_failing_node_is_reported(home):
    case = copy.deepcopy(next(c for c in ALL if c["name"] == "v2_owner_denies"))
    case["expect"]["frames"][-1]["refusal_code"] = "confirm_timeout"
    result = run_case_in_process(case, home=home)
    assert not result.passed
    assert "FAIL" in format_table([result])


def test_the_v2_cases_cover_every_node_check_from_13_to_24():
    pinned = {c["check"] for c in C.all_cases()[2] if c["check"]}
    assert set(range(1, 25)) - {18} <= pinned
    codes = {code for _, codes in NODE_CHECKS_V2 for code in codes}
    seen = {
        m.get("refusal_code")
        for c in C.all_cases()[2]
        for m in c["expect"]["frames"]
        if m.get("refusal_code")
    }
    # Check 18 is unreachable by a frame (branch 1 record); nonce_cache_full
    # needs 10 000 live nonces and is unit-tested in test_protocol_v2_verify.
    assert codes - seen <= {"nonce_cache_full"}


def test_the_brief_case_list_is_present():
    names = {c["name"] for c in ALL}
    for name in (
        "v2_gate_without_grant",
        "v2_read_with_grant",
        "v2_confirm_with_grant",
        "v2_grant_unpinned_key",
        "v2_grant_bad_signature",
        "v2_grant_envelope_domain",
        "v2_gate_ttl_121",
        "v2_grant_expired",
        "v2_expired_after_confirm",
        "v2_confirm_expired_after_confirm",
        "v2_replay_same_grant_twice",
        "v2_replay_grant_on_new_envelope",
        "v2_class_gate_for_confirm_entry",
        "v2_class_confirm_for_gate_entry",
        "v2_args_outside_node_schema",
        "v2_not_configured_unmapped_repo",
        "v2_not_configured_unmapped_kind",
        "v2_not_configured_disabled_recipe",
        "v2_not_configured_bundle_id",
        "v2_not_configured_symlink",
        "v2_owner_denies",
        "v2_owner_times_out",
        "v2_socket_dropped_window_open",
        "v2_socket_dropped_while_running",
        "v2_busy_confirming",
        "v2_policy_shaped_policy_set",
        "v2_policy_shaped_allowlist_add",
        "v2_policy_shaped_sandbox_update",
        "v2_policy_shaped_recipe_add",
        "v2_policy_shaped_grant_add",
    ):
        assert name in names
    for field in (
        "node_id",
        "workspace_id",
        "capability",
        "capability_version",
        "args_sha256",
        "nonce",
        "issued_at",
        "expires_at",
    ):
        assert f"v2_grant_{field}_differs_from_envelope" in names
        assert f"v2_envelope_{field}_differs_from_grant" in names


def test_the_gateway_counts_stray_output_and_confirmation_frames():
    checker = GatewayChecker(2)
    stray = C._uuid4("stray")
    checker.node_sent(
        {
            "v": 2,
            "type": "output",
            "id": C._uuid4("o"),
            "invocation_id": stray,
            "seq": 0,
            "stream": "stdout",
            "data": "x",
            "truncated": False,
        }
    )
    checker.node_sent(
        {
            "v": 2,
            "type": "confirmation",
            "id": C._uuid4("c"),
            "invocation_id": stray,
            "state": "shown",
            "at": 0,
        }
    )
    assert checker.frame_errors == 2
    checker.server_sent({"type": "invoke", "invocation_id": stray})
    checker.node_sent(
        {
            "v": 2,
            "type": "confirmation",
            "id": C._uuid4("c2"),
            "invocation_id": stray,
            "state": "shown",
            "at": 0,
        }
    )
    assert checker.frame_errors == 2
    checker.node_sent({"v": 2, "type": "nonsense"})
    assert checker.frame_errors == 3


def test_the_committed_files_are_current():
    assert build.check() == []
    call_command("build_node_conformance", "--check")


def test_a_changed_definition_is_drift(monkeypatch):
    original = build.rendered_files

    def changed():
        files = original()
        files["cases/v2/v2_owner_denies.json"] += " "
        return files

    monkeypatch.setattr(build, "rendered_files", changed)
    assert build.check() == ["cases/v2/v2_owner_denies.json"]
    with pytest.raises(CommandError):
        call_command("build_node_conformance", "--check")


def test_the_case_files_are_what_the_swift_target_loads():
    for version, cases in C.all_cases().items():
        for case in cases:
            path = build.ROOT / "cases" / f"v{version}" / f"{case['name']}.json"
            assert json.loads(path.read_text("utf-8")) == json.loads(C.render_case(case))


def test_the_conformance_keys_are_refused_as_server_keys():
    assert C.SIGNING.key_id in published_test_key_ids()
    assert C.UNPINNED.key_id in published_test_key_ids()
    with override_settings(NODE_SIGNING_KEY=C.KEYS["signing"]["private_key_b64"]):
        with pytest.raises(SigningKeyUnavailable) as exc:
            signing_keyring()
    assert exc.value.reason == "test_key"


def test_node_conformance_server_command_prints_all_pass(capsys):
    call_command(
        "node_conformance_server",
        "--protocol",
        "2",
        "--case",
        "v2_owner_denies",
        "--case",
        "v2_gate_command_runs_after_click",
    )
    out = capsys.readouterr().out
    assert "ALL PASS" in out and "v2: 2/2 passed" in out


def test_the_phase_9_case_list_is_present():
    """Phase 9 brief branch 3 "Conformance cases" (the ADR-013 release gate)."""
    names = {c["name"] for c in C.all_cases()[1]}
    for name in (
        "v1_unknown_capability",
        "v1_args_extra_key",
        "v1_args_wrong_type",
        "v1_args_oversize",
        "v1_args_traversal",
        "v1_args_nul",
        "v1_args_rtl_override",
        "v1_args_shell_meta",
        "v1_args_over_length_array",
        "v1_replay",
        "v1_expired",
        "v1_not_yet_valid",
        "v1_ttl_too_long",
        "v1_bad_signature",
        "v1_unknown_key",
        "v1_args_hash_mismatch",
        "v1_args_changed_after_signing",
        "v1_wrong_node",
        "v1_non_null_grant",
        "v1_class_not_read",
        "v1_not_granted",
        "v1_unknown_type",
        "v1_policy_shaped_policy_set",
        "v1_policy_shaped_allowlist_add",
        "v1_policy_shaped_catalogue_set",
        "v1_policy_shaped_config_update",
        "v1_policy_shaped_grant_add",
        "v1_oversize_frame",
        "v1_malformed_json",
        "v1_three_bad_frames_close",
        "v1_bye_revoked_no_reconnect",
        "v1_server_silence_reconnect",
        "v1_disconnect_mid_invoke_no_late_result",
        # the readers (branch 3)
        "v1_read_sessions_list_cwd_prefixes",
        "v1_read_session_detail_scrubbed",
        "v1_read_codex_thread_detail_scrubbed",
        "v1_read_usage_cwd_prefixes",
        "v1_read_git_status_unlisted_repo",
        "v1_read_windows_titles_off",
        "v1_read_directory_not_granted",
    ):
        assert name in names, name
    pinned = {c["check"] for c in C.all_cases()[1] if c["check"]}
    assert set(range(1, 17)) <= pinned  # every v1 node check (D5) has a case


def test_no_reader_runs_on_any_refusal_case():
    """Every case whose frames are refusals or frame errors pins
    reader_calls == 0, and the runner catches a node whose reader ran."""
    refusal_cases = [c for c in ALL if C._refusals_only(c["expect"]["frames"])]
    assert len(refusal_cases) > 60
    for case in refusal_cases:
        assert case["expect"]["reader_calls"] == 0, case["name"]
    for name in ("v1_read_ok", "v1_replay", "v1_read_session_detail_scrubbed"):
        case = next(c for c in ALL if c["name"] == name)
        assert case["expect"]["reader_calls"] == 1


def test_a_reader_running_on_a_refusal_path_is_reported(home):
    case = copy.deepcopy(next(c for c in ALL if c["name"] == "v1_args_traversal"))
    case["steps"].insert(
        0, {"op": "send", "frame": C.plain("extra-ok", "apps.frontmost", {}, version=1)}
    )
    case["expect"]["frames"].insert(0, {"type": "result", "status": "ok"})
    result = run_case_in_process(case, home=home)
    assert not result.passed
    assert any("reader_calls" in failure for failure in result.failures)


def test_a_raw_secret_in_a_read_result_is_reported(home):
    case = copy.deepcopy(next(c for c in ALL if c["name"] == "v1_read_session_detail_scrubbed"))
    case["setup"]["window_titles"] = True
    raw = "[SECRET:openai_key]"
    case["expect"]["absent_b64"] = C._absent(raw)
    result = run_case_in_process(case, home=home)
    assert "a raw secret reached a frame" in result.failures


def test_every_v1_case_passes_through_the_command_over_a_socket(capsys):
    """Phase 9 branch 3 "Done when": node_conformance_server against the
    reference node prints all-pass."""
    call_command("node_conformance_server", "--protocol", "1")
    out = capsys.readouterr().out
    total = len(C.all_cases()[1])
    assert f"v1: {total}/{total} passed" in out and "ALL PASS" in out


def test_node_conformance_server_refuses_an_unknown_case():
    with pytest.raises(CommandError):
        call_command("node_conformance_server", "--case", "nope")
