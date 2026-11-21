"""The eight v1 read capability contracts (Phase 9 brief D7) and the
catalogue matching rule: name, version and the sha256 of both schemas."""

import copy

import pytest

from nodes.capabilities import (
    CONTRACT_DIR,
    NOT_INVOCABLE_REASONS,
    check_catalogue_entry,
    get_contract,
    load_contracts,
)
from nodes.protocol import ProtocolError, sha256_hex
from nodes.protocol.messages import MAX_RESULT_BYTES

EIGHT = {
    "claude.sessions.list",
    "claude.sessions.read",
    "codex.threads.list",
    "codex.threads.read",
    "usage.ccusage",
    "git.status",
    "apps.frontmost",
    "windows.list",
}


def test_exactly_the_eight_read_capabilities():
    contracts = load_contracts()
    assert set(contracts) == EIGHT
    assert {path.stem for path in CONTRACT_DIR.glob("*.json")} == EIGHT
    for contract in contracts.values():
        assert contract.capability_class == "read"
        assert contract.version == 1
        assert 0 < contract.max_result_bytes <= MAX_RESULT_BYTES


def test_data_classes_and_workspace_resolution():
    by_name = load_contracts()
    personal = {name for name, c in by_name.items() if c.data_class == "personal"}
    assert personal == {
        "claude.sessions.read",
        "codex.threads.read",
        "apps.frontmost",
        "windows.list",
    }
    assert by_name["git.status"].path_args == ("repo_path",)
    assert by_name["git.status"].workspace_resolution == "path_arg"
    for name in ("claude.sessions.list", "codex.threads.list", "usage.ccusage"):
        assert by_name[name].workspace_resolution == "row_cwd"
        assert "cwd_prefixes" in by_name[name].path_args
    for name in ("apps.frontmost", "windows.list"):
        assert by_name[name].workspace_resolution == "personal_only"


def test_amounts_are_integer_micro_usd():
    result = get_contract("usage.ccusage").result_schema["properties"]
    assert result["total_usd_micros"]["type"] == "integer"
    row = result["by_project"]["items"]["properties"]
    assert set(row) == {"cwd", "usd_micros", "tokens_in", "tokens_out"}
    assert "total_usd" not in result


def test_list_results_carry_no_text():
    for name, rows_key in (("claude.sessions.list", "sessions"), ("codex.threads.list", "threads")):
        row = get_contract(name).result_schema["properties"][rows_key]["items"]["properties"]
        assert "text" not in row and "title" not in row


def test_git_status_returns_counts_never_file_names():
    props = get_contract("git.status").result_schema["properties"]
    assert set(props) == {
        "branch",
        "ahead",
        "behind",
        "dirty_files_count",
        "untracked_count",
        "last_commit_sha",
        "last_commit_at",
    }


# --- args validation (the server never sends args outside its contract) ---


@pytest.mark.parametrize(
    "repo_path",
    [
        "~/work/acme",
        "/Users/owner/work/acme",
        "/Users/owner/My Projects/çalışma",
        "~/work/a.b-c_d",
    ],
)
def test_valid_paths(repo_path):
    get_contract("git.status").validate_args({"repo_path": repo_path})


@pytest.mark.parametrize(
    "repo_path",
    [
        "",
        "~",
        "/",
        "relative/path",
        "~/work/../secret",
        "~/work/..",
        "/./etc",
        "~/work/a\x00b",
        "~/work/‮exe.txt",
        "~/work/a;rm -rf",
        "~/work/$(id)",
        "~/work/`id`",
        "~/work/a|b",
        "~/work/a\nb",
        "~/work/acme\n",
        "~/work//double",
        "~/" + "a" * 1100,
    ],
)
def test_hostile_paths_are_outside_the_contract(repo_path):
    with pytest.raises(ProtocolError) as exc:
        get_contract("git.status").validate_args({"repo_path": repo_path})
    assert exc.value.code == "args_contract"


@pytest.mark.parametrize(
    "args",
    [
        {"since_hours": 24, "limit": 10, "extra": True},
        {"since_hours": "24", "limit": 10},
        {"since_hours": 0, "limit": 10},
        {"since_hours": 24, "limit": 51},
        {"since_hours": 24.0, "limit": 10},
        {"since_hours": 24, "limit": 10, "cwd_prefixes": []},
        {"since_hours": 24, "limit": 10, "cwd_prefixes": ["~/w"] * 21},
        {"limit": 10},
    ],
)
def test_out_of_contract_list_args(args):
    with pytest.raises(ProtocolError):
        get_contract("claude.sessions.list").validate_args(args)


def test_frontmost_takes_no_args():
    get_contract("apps.frontmost").validate_args({})
    with pytest.raises(ProtocolError):
        get_contract("apps.frontmost").validate_args({"anything": 1})


# --- result validation ---


def test_result_validation_and_size_cap():
    contract = get_contract("git.status")
    ok = {
        "branch": "main",
        "ahead": 1,
        "behind": 0,
        "dirty_files_count": 2,
        "untracked_count": 0,
        "last_commit_sha": "a" * 40,
        "last_commit_at": 1_800_000_000,
    }
    contract.validate_result(ok)
    with pytest.raises(ProtocolError) as exc:
        contract.validate_result({**ok, "files": ["secret.txt"]})
    assert exc.value.code == "result_schema"
    with pytest.raises(ProtocolError) as exc:
        contract.validate_result({**ok, "branch": "x" * 5000})
    assert exc.value.code == "result_too_large"


def test_read_turn_text_is_bounded():
    contract = get_contract("claude.sessions.read")
    turn = {"role": "user", "at": 1, "text": "x" * 2001, "tool_calls": 0, "tool_results": 0}
    with pytest.raises(ProtocolError):
        contract.validate_result({"session_id": "s1", "turns": [turn]})
    contract.validate_result({"session_id": "s1", "turns": [{**turn, "text": "x" * 2000}]})


# --- catalogue matching ---


def test_a_conforming_entry_is_invocable():
    for contract in load_contracts().values():
        assert check_catalogue_entry(contract.catalogue_entry()) is None


def _entry(**overrides):
    entry = get_contract("git.status").catalogue_entry()
    entry.update(overrides)
    return entry


def test_not_invocable_reasons():
    wider = copy.deepcopy(get_contract("git.status").args_schema)
    wider["properties"]["repo_path"]["maxLength"] = 4096
    result = copy.deepcopy(get_contract("git.status").result_schema)
    result["properties"]["files"] = {"type": "array", "maxItems": 10}
    cases = {
        "unknown_capability": _entry(name="shell.exec"),
        "class_not_read": _entry(**{"class": "gate"}),
        "version_mismatch": _entry(version=2),
        "args_schema_mismatch": _entry(args_schema=wider),
        "result_schema_mismatch": _entry(result_schema=result),
        "not_granted": _entry(availability="not_granted"),
        "unsupported": _entry(availability="unsupported"),
    }
    for reason, entry in cases.items():
        assert reason in NOT_INVOCABLE_REASONS
        assert check_catalogue_entry(entry) == reason, reason


def test_schema_hashes_are_canonical_and_key_order_free():
    contract = get_contract("git.status")
    reordered = dict(reversed(list(contract.args_schema.items())))
    assert sha256_hex(reordered) == contract.args_schema_sha256
    assert check_catalogue_entry(_entry(args_schema=reordered)) is None


def test_a_float_inside_an_advertised_schema_never_matches():
    schema = copy.deepcopy(get_contract("git.status").args_schema)
    schema["properties"]["repo_path"]["maxLength"] = 1024.0
    assert check_catalogue_entry(_entry(args_schema=schema)) == "args_schema_mismatch"
