"""The readers over a real temporary `$HOME` (not the fixture factory):
grants, bounds and scrubbing, `cwd_prefixes`, symlink refusals, the
owner-turn view, and the Linux catalogue (D9, D10)."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from friday_node_linux.policy import LinuxPolicy
from friday_node_linux.readers_root import LinuxReaders, vetted_files

from .conftest import FAKE_GITHUB_TOKEN, NOW, claude_lines, write_jsonl

LIST_ARGS = {"since_hours": 24, "limit": 50}


def readers(home, *, claude=False, codex=False, repos=(), which=lambda _n: None, **kwargs):
    policy = LinuxPolicy()
    policy = policy.with_grant("claude", claude).with_grant("codex", codex)
    for name, root in repos:
        policy = policy.with_repo(name, root)
    return LinuxReaders(home=home, policy=policy, which=which, **kwargs)


def test_grants_off_by_default_nothing_is_read(home):
    r = readers(home)
    availability = r.availability()
    for name in (
        "claude.sessions.list",
        "claude.sessions.read",
        "usage.ccusage",
        "codex.threads.list",
        "codex.threads.read",
        "git.status",
        "claude.transcripts.owner_turns",
        "codex.transcripts.owner_turns",
    ):
        assert availability[name] == "not_granted", name
    outcome = r.read("claude.sessions.list", LIST_ARGS, now=NOW)
    assert outcome.status == "error" and outcome.output is None


def test_granted_sessions_are_bounded_scrubbed_rows(home):
    r = readers(home, claude=True)
    outcome = r.read("claude.sessions.list", LIST_ARGS, now=NOW)
    assert outcome.status == "ok"
    rows = {row["session_id"]: row for row in outcome.output["sessions"]}
    assert set(rows) == {"cc-acme-1", "cc-secret-1"}
    assert rows["cc-acme-1"]["cwd"] == "~/work/acme"
    assert rows["cc-acme-1"]["message_count"] == 2  # the tool_result line is not a message
    detail = r.read("claude.sessions.read", {"session_id": "cc-acme-1", "last_turns": 10}, now=NOW)
    text = json.dumps(detail.output)
    assert FAKE_GITHUB_TOKEN not in text
    assert "[SECRET:" in text
    assert "FILE-CONTENT-PHRASE" not in text  # never a tool result


def test_cwd_prefixes_are_honoured_at_segment_boundaries(home):
    r = readers(home, claude=True)
    outcome = r.read(
        "claude.sessions.list", {**LIST_ARGS, "cwd_prefixes": ["~/work/acme"]}, now=NOW
    )
    assert [row["session_id"] for row in outcome.output["sessions"]] == ["cc-acme-1"]


def test_a_symlinked_transcript_out_of_the_granted_directory_is_refused(home, tmp_path):
    escape = tmp_path / "outside" / "cc-escape-1.jsonl"
    projects = home / ".claude" / "projects"
    os.symlink(escape, projects / "-home-work-acme" / "cc-escape-1.jsonl")
    os.symlink(tmp_path / "outside", projects / "-linked-dir")
    r = readers(home, claude=True)
    outcome = r.read("claude.sessions.list", LIST_ARGS, now=NOW)
    ids = {row["session_id"] for row in outcome.output["sessions"]}
    assert "cc-escape-1" not in ids
    turns = r.read(
        "claude.transcripts.owner_turns",
        {
            "since": (datetime.fromtimestamp(NOW, UTC) - timedelta(days=1)).isoformat(),
            "cwd_prefixes": ["~/work"],
            "max_sessions": 10,
            "max_turns": 100,
        },
        now=NOW,
    )
    assert turns.status == "ok"
    assert "ESCAPE-PHRASE" not in json.dumps(turns.output)
    assert all("escape" not in str(rel) for rel, _ in vetted_files(home, "claude"))


def test_a_symlinked_grant_root_is_refused(home, tmp_path):
    real = home / ".claude" / "projects"
    moved = tmp_path / "projects-elsewhere"
    real.rename(moved)
    os.symlink(moved, real)
    r = readers(home, claude=True)
    assert r.read("claude.sessions.list", LIST_ARGS, now=NOW).output["sessions"] == []
    assert vetted_files(home, "claude") == []


def test_owner_turns_over_the_vetted_view(home, tmp_path):
    r = readers(home, claude=True, codex=True, scratch_dir=tmp_path)
    # Owner-turn prefixes are compared with the transcripts' literal cwd
    # (the 8B reader's rule): Claude Code and Codex record absolute paths.
    args = {
        "since": "2026-11-28T00:00:00Z",
        "cwd_prefixes": [str(home / "work" / "acme")],
        "max_sessions": 10,
        "max_turns": 100,
    }
    claude = r.read("claude.transcripts.owner_turns", args, now=NOW)
    assert claude.status == "ok"
    sessions = claude.output["sessions"]
    assert [s["session_id"] for s in sessions] == ["cc-acme-1"]
    text = json.dumps(claude.output)
    assert FAKE_GITHUB_TOKEN not in text and "Looking at it." not in text  # owner text only
    codex = r.read("codex.transcripts.owner_turns", args, now=NOW)
    assert codex.status == "ok"
    assert "Bump the acme client." in json.dumps(codex.output)
    # The view is removed after every call: nothing is copied or cached.
    assert list(tmp_path.glob("friday-node-turns-*")) == []


def test_owner_turns_need_their_grant(home):
    r = readers(home, claude=False, codex=True)
    args = {
        "since": "2026-11-28T00:00:00Z",
        "cwd_prefixes": ["~"],
        "max_sessions": 10,
        "max_turns": 100,
    }
    assert r.read("claude.transcripts.owner_turns", args, now=NOW).status == "error"
    assert r.read("codex.transcripts.owner_turns", args, now=NOW).status == "ok"


def test_usage_needs_ccusage_on_path(home):
    assert readers(home, claude=True).availability()["usage.ccusage"] == "unsupported"
    with_ccusage = readers(home, claude=True, which=lambda name: f"/usr/bin/{name}")
    assert with_ccusage.availability()["usage.ccusage"] == "granted"
    outcome = with_ccusage.read("usage.ccusage", {"period": "week"}, now=NOW)
    assert outcome.status == "ok"
    assert outcome.output["total_usd_micros"] == 2000  # two sessions x 0.001 USD


def test_git_status_only_for_registered_repos(home):
    assert readers(home).availability()["git.status"] == "not_granted"
    r = readers(home, repos=[("acme", "~/work/acme")])
    assert r.read("git.status", {"repo_path": "~/work/acme"}, now=NOW).status == "ok"
    assert r.read("git.status", {"repo_path": "~/work/other"}, now=NOW).status == "error"


def test_desktop_capabilities_are_unsupported_on_linux(home):
    r = readers(home, claude=True)
    for name in ("apps.frontmost", "windows.list"):
        assert r.availability()[name] == "unsupported"
        assert r.read(name, {}, now=NOW).status == "error"


def test_the_catalogue_is_read_only_and_has_no_action(home):
    catalogue = readers(home, claude=True).catalogue()
    names = [entry["name"] for entry in catalogue]
    assert names == sorted(
        [
            "apps.frontmost",
            "claude.sessions.list",
            "claude.sessions.read",
            "claude.transcripts.owner_turns",
            "codex.threads.list",
            "codex.threads.read",
            "codex.transcripts.owner_turns",
            "git.status",
            "usage.ccusage",
            "windows.list",
        ]
    )
    assert {entry["class"] for entry in catalogue} == {"read"}
    assert all(entry["version"] == 1 for entry in catalogue)


def test_every_read_rereads_the_files(home):
    r = readers(home, claude=True)
    first = r.read("claude.sessions.list", LIST_ARGS, now=NOW).output["sessions"]
    write_jsonl(
        home / ".claude" / "projects" / "-home-new" / "cc-new-1.jsonl",
        claude_lines("cc-new-1", str(home / "new"), NOW - 60),
    )
    second = r.read("claude.sessions.list", LIST_ARGS, now=NOW).output["sessions"]
    assert len(second) == len(first) + 1


@pytest.mark.parametrize("capability", ["claude.sessions.list", "codex.threads.list"])
def test_reads_are_counted_once(home, capability):
    r = readers(home, claude=True, codex=True)
    r.read(capability, LIST_ARGS, now=NOW)
    assert r.calls == [capability]
