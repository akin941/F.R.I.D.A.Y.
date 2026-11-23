"""The reference node's readers (Phase 9 brief branch 3, D7, D13 items 5-6):
bounded, read-only, scrubbed, `cwd_prefixes` honoured, never tool results or
file contents."""

import json
import os
import shutil

import pytest

from nodes.capabilities import load_contracts
from nodes.conformance import cases as C
from nodes.conformance.runner import build_reference_node
from nodes.protocol.canonical import canonicalize
from nodes.reference import readers as readers_module
from nodes.reference.confirm import FakeClock
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy, make_repo
from nodes.reference.readers import Readers, matches_prefixes, path_forms

NOW = C.NOW
ALL_READS = [
    ("claude.sessions.list", {"since_hours": 168, "limit": 50}),
    ("claude.sessions.read", {"session_id": "cc-acme-1", "last_turns": 20}),
    ("claude.sessions.read", {"session_id": "cc-friday-1", "last_turns": 20}),
    ("claude.sessions.read", {"session_id": "cc-secret-1", "last_turns": 20}),
    ("claude.sessions.read", {"session_id": "cc-scratch-1", "last_turns": 20}),
    ("codex.threads.list", {"since_hours": 168, "limit": 50}),
    ("codex.threads.read", {"thread_id": "cx-acme-1", "last_turns": 20}),
    ("codex.threads.read", {"thread_id": "cx-friday-1", "last_turns": 20}),
    ("usage.ccusage", {"period": "month"}),
    ("git.status", {"repo_path": "~/work/acme"}),
    ("apps.frontmost", {}),
    ("windows.list", {"limit": 30}),
]


@pytest.fixture
def home(tmp_path):
    return build_fixture_home(tmp_path)


def _readers(home, **kwargs):
    kwargs.setdefault("grants", {"claude": True, "codex": True})
    return Readers(home=home, policy=fixture_policy(home), **kwargs)


def _read(readers, capability, args):
    return readers.read(capability, args, now=NOW)


def test_every_reader_answers_within_its_result_schema(home):
    readers = _readers(home, window_titles=True)
    contracts = load_contracts()
    seen = set()
    for capability, args in ALL_READS:
        outcome = _read(readers, capability, args)
        assert outcome.status == "ok", capability
        contracts[capability].validate_result(outcome.output)
        seen.add(capability)
    assert seen == set(contracts) - {
        "claude.transcripts.owner_turns",
        "codex.transcripts.owner_turns",
    }


def test_readers_never_return_tool_results_file_contents_or_injected_text(home):
    readers = _readers(home, window_titles=True)
    text = json.dumps([_read(readers, c, a).output for c, a in ALL_READS], ensure_ascii=False)
    for phrase in C.NEVER_RETURNED:
        assert phrase not in text, phrase
    assert "test_invoice.py" not in text  # a tool_use input (a file path)
    assert "cat setup.py" not in text  # a function_call's arguments


def test_no_raw_fixture_secret_is_returned_by_any_reader(home):
    readers = _readers(home, window_titles=True)
    text = json.dumps([_read(readers, c, a).output for c, a in ALL_READS], ensure_ascii=False)
    for secret in C.FIXTURE_SECRETS:
        assert secret not in text
    assert "[SECRET:openai_key]" in text and "[SECRET:aws_key]" in text


def test_no_raw_secret_reaches_any_result_frame_of_the_reference_node(home):
    """A reference-node read of every fixture: no raw secret in any frame."""
    case = {"setup": {"now": NOW, "window_titles": True}}
    node = build_reference_node(case, home=home)
    node.connect(1)
    frames = []
    for index, (capability, args) in enumerate(ALL_READS):
        frames += node.receive(json.dumps(C.plain(f"scrub-{index}", capability, args, version=1)))
    assert [f["status"] for f in frames] == ["ok"] * len(ALL_READS)
    text = json.dumps(frames, ensure_ascii=False)
    for secret in C.FIXTURE_SECRETS:
        assert secret not in text


def test_cwd_prefixes_keep_rows_under_a_prefix_at_segment_boundaries(home):
    readers = _readers(home)
    rows = _read(
        readers,
        "claude.sessions.list",
        {"since_hours": 168, "limit": 50, "cwd_prefixes": ["~/work/acme"]},
    ).output["sessions"]
    assert [r["cwd"] for r in rows] == ["~/work/acme"]  # never ~/work/acme-secret
    rows = _read(
        readers,
        "claude.sessions.list",
        {"since_hours": 168, "limit": 50, "cwd_prefixes": ["~/work", "/tmp/scratch"]},
    ).output["sessions"]
    assert sorted(r["cwd"] for r in rows) == ["/tmp/scratch", "~/work/acme", "~/work/acme-secret"]


def test_cwd_prefixes_match_the_absolute_form_of_the_home_too(home):
    readers = _readers(home)
    prefix = os.path.join(str(home), "code")
    rows = _read(
        readers, "codex.threads.list", {"since_hours": 168, "limit": 50, "cwd_prefixes": [prefix]}
    ).output["threads"]
    assert [r["thread_id"] for r in rows] == ["cx-friday-1"]
    assert rows[0]["cwd"] == "~/code/friday"


def test_rows_outside_the_prefixes_are_skipped_before_their_turns_are_parsed(home, monkeypatch):
    parsed = []
    original = readers_module._claude_content
    monkeypatch.setattr(
        readers_module, "_claude_content", lambda c: parsed.append(c) or original(c)
    )
    readers = _readers(home)
    out = _read(
        readers,
        "claude.sessions.list",
        {"since_hours": 168, "limit": 50, "cwd_prefixes": ["~/nowhere"]},
    )
    assert out.output == {"sessions": []}
    assert parsed == []


@pytest.mark.parametrize(
    ("prefix", "cwd", "expected"),
    [
        ("~/work", "/H/work/acme", True),
        ("~/work/acme", "/H/work/acme-secret", False),
        ("~/work/acme", "/H/work/acme", True),
        ("/H/work", "/H/work/acme", True),
        ("~", "/H/x", True),
        ("/", "/tmp/scratch", True),
        ("~/work/", "/H/work/a", True),
        ("~/work/./acme", "/H/work/acme/x", True),
        ("~/work", "/H/workshop", False),
        ("~/work", "/other/work", False),
    ],
)
def test_prefix_matching_rule(prefix, cwd, expected):
    _display, forms = path_forms(cwd, "/H")
    assert matches_prefixes([prefix], forms) is expected


def test_since_hours_limit_and_status_hint(home):
    readers = _readers(home)
    rows = _read(readers, "claude.sessions.list", {"since_hours": 168, "limit": 2}).output[
        "sessions"
    ]
    assert [r["session_id"] for r in rows] == ["cc-friday-1", "cc-secret-1"]  # newest first
    assert rows[0]["status_hint"] == "active" and rows[1]["status_hint"] == "idle"
    rows = _read(readers, "claude.sessions.list", {"since_hours": 24, "limit": 50}).output[
        "sessions"
    ]
    assert "cc-scratch-1" not in {r["session_id"] for r in rows}


def test_usage_totals_are_the_sums_of_the_returned_rows(home):
    readers = _readers(home)
    for period in ("today", "week", "month"):
        out = _read(readers, "usage.ccusage", {"period": period}).output
        rows = out["by_project"]
        assert out["total_usd_micros"] == sum(r["usd_micros"] for r in rows)
        assert out["tokens_in"] == sum(r["tokens_in"] for r in rows)
        assert out["tokens_out"] == sum(r["tokens_out"] for r in rows)
    today = _read(readers, "usage.ccusage", {"period": "today"}).output
    assert [r["cwd"] for r in today["by_project"]] == ["~/code/friday"]
    week = _read(readers, "usage.ccusage", {"period": "week"}).output
    assert week["total_usd_micros"] == 118_345  # 0.012345 + 0.02 + ... in micro-USD


def test_directory_grants_are_off_by_default_and_refused_by_the_reader_too(home):
    readers = Readers(home=home, policy=fixture_policy(home))
    availability = readers.availability()
    assert availability["claude.sessions.list"] == "not_granted"
    assert availability["codex.threads.read"] == "not_granted"
    assert availability["usage.ccusage"] == "not_granted"
    assert availability["apps.frontmost"] == "granted"
    outcome = _read(readers, "claude.sessions.list", {"since_hours": 24, "limit": 5})
    assert outcome.status == "error" and outcome.output is None
    assert Readers(home=home).availability()["git.status"] == "not_granted"  # no local repo list


def test_unknown_session_and_thread_are_errors(home):
    readers = _readers(home)
    assert _read(readers, "claude.sessions.read", {"session_id": "x1", "last_turns": 1}).status == (
        "error"
    )
    assert _read(readers, "codex.threads.read", {"thread_id": "x1", "last_turns": 1}).status == (
        "error"
    )


def test_git_status_counts_only_and_the_local_list(home):
    readers = _readers(home)
    repo = home / "work" / "acme"
    (repo / "README.md").write_text("changed SECRET-FILE-CONTENT\n", encoding="utf-8")
    (repo / "untracked-name-PHRASE.txt").write_text("x", encoding="utf-8")
    out = _read(readers, "git.status", {"repo_path": "~/work/acme"}).output
    assert out["dirty_files_count"] == 1 and out["untracked_count"] == 1
    assert out["branch"] == "main" and len(out["last_commit_sha"]) == 40
    text = json.dumps(out)
    assert "README" not in text and "PHRASE" not in text and "SECRET" not in text
    # A symlink resolving to a listed root is that repo; anything else is refused.
    assert _read(readers, "git.status", {"repo_path": "~/work/acme-link"}).status == "ok"
    assert _read(readers, "git.status", {"repo_path": "~/work/link"}).status == "error"
    assert _read(readers, "git.status", {"repo_path": "~/work/other"}).status == "error"


def test_git_status_of_a_repo_without_commits(tmp_path):
    home = build_fixture_home(tmp_path)
    shutil.rmtree(home / "code" / "friday")
    (home / "code" / "friday").mkdir()
    from nodes.reference.fixtures.factory import _git

    _git(home / "code" / "friday", "init", "-q")
    readers = _readers(home)
    out = _read(readers, "git.status", {"repo_path": "~/code/friday"}).output
    assert out["last_commit_sha"] is None and out["last_commit_at"] is None


def test_window_titles_only_when_enabled_locally_and_scrubbed(home):
    off = _read(_readers(home), "windows.list", {"limit": 30}).output
    assert all("titles" not in row for row in off["windows"])
    on = _read(_readers(home, window_titles=True), "windows.list", {"limit": 30}).output
    titles = [t for row in on["windows"] for t in row["titles"]]
    assert "Invoice - [PII:email]" in titles
    assert not any("owner.fixture@example.com" in t for t in titles)


def test_an_identifier_the_scrub_would_change_is_never_returned(home):
    stem = "sk-" + "proj-IdentFAKE0123456789abcdefGHIJKLmnopQRst"
    target = home / ".claude" / "projects" / "leak"
    target.mkdir()
    line = {
        "type": "user",
        "sessionId": stem,
        "cwd": str(home / "work" / "acme"),
        "timestamp": "2026-11-29T23:00:00.000Z",
        "message": {"role": "user", "content": "hello"},
    }
    (target / f"{stem}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    rows = _read(_readers(home), "claude.sessions.list", {"since_hours": 24, "limit": 50}).output
    assert stem not in json.dumps(rows)


def test_symlinked_and_oversized_transcripts_are_not_read(home, monkeypatch):
    outside = home / "outside.jsonl"
    line = {
        "type": "user",
        "cwd": str(home / "work" / "acme"),
        "timestamp": "2026-11-29T23:00:00.000Z",
        "message": {"role": "user", "content": "SYMLINK-PHRASE"},
    }
    outside.write_text(json.dumps(line) + "\n", encoding="utf-8")
    os.symlink(outside, home / ".claude" / "projects" / "-HOME-work-acme" / "cc-link-1.jsonl")
    monkeypatch.setattr(readers_module, "MAX_LINE_BYTES", 200)
    readers = _readers(home)
    rows = _read(readers, "claude.sessions.list", {"since_hours": 168, "limit": 50}).output
    assert "cc-link-1" not in json.dumps(rows)


def test_a_large_session_is_cut_to_the_result_budget_and_marked_truncated(home):
    target = home / ".claude" / "projects" / "big"
    target.mkdir()
    lines = []
    for index in range(20):
        lines.append(
            {
                "type": "user" if index % 2 == 0 else "assistant",
                "cwd": str(home / "work" / "acme"),
                "timestamp": f"2026-11-29T23:{index:02d}:00.000Z",
                "message": {"role": "user", "content": f"turn {index} " + "ğüşıöç " * 400},
            }
        )
    (target / "cc-big-1.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    readers = _readers(home)
    outcome = _read(readers, "claude.sessions.read", {"session_id": "cc-big-1", "last_turns": 20})
    assert outcome.status == "ok" and outcome.truncated
    assert len(canonicalize(outcome.output)) <= 65536
    turns = outcome.output["turns"]
    assert 0 < len(turns) < 20
    assert turns[-1]["text"].startswith("turn 19 ")  # the newest turns are kept
    assert all(len(turn["text"]) <= 2000 for turn in turns)
    case = {"setup": {"now": NOW}}
    node = build_reference_node(case, home=home)
    node.connect(1)
    args = {"session_id": "cc-big-1", "last_turns": 20}
    (frame,) = node.receive(json.dumps(C.plain("big", "claude.sessions.read", args, version=1)))
    assert frame["status"] == "ok" and frame["truncated"] is True


def test_every_call_rereads_the_files(home):
    readers = _readers(home)
    first = _read(readers, "codex.threads.list", {"since_hours": 168, "limit": 50}).output
    shutil.rmtree(home / ".codex")
    second = _read(readers, "codex.threads.list", {"since_hours": 168, "limit": 50}).output
    assert first["threads"] and second == {"threads": []}
    assert readers.calls == ["codex.threads.list", "codex.threads.list"]


def test_a_new_repo_needs_the_local_list(tmp_path):
    home = build_fixture_home(tmp_path)
    make_repo(home / "work" / "fresh", "fresh")
    out = _read(_readers(home), "git.status", {"repo_path": "~/work/fresh"})
    assert out.status == "error"


def test_the_reader_clock_is_the_node_clock(home):
    case = {"setup": {"now": NOW + 10 * 86400}}
    node = build_reference_node(case, home=home)
    assert isinstance(node.clock, FakeClock)
    node.connect(1)
    args = {"since_hours": 168, "limit": 50}
    envelope = C.plain(
        "clock", "claude.sessions.list", args, version=1, issued=NOW + 10 * 86400 - 5
    )
    (frame,) = node.receive(json.dumps(envelope))
    assert frame["output"] == {"sessions": []}  # everything is older than a week by then
