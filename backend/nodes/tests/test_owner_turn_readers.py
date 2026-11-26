"""The reference owner-turn readers, their contracts and conformance files
(Phase 8B branch 8, brief D13/D14/D17; moved from `integrations/` by the
nodes-backed transcript port, runbook A12). Pure Python: no database."""

import ast
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest

from nodes.capabilities import load_contracts
from nodes.conformance import build
from nodes.conformance import cases as C
from nodes.conformance.runner import run_case_in_process
from nodes.protocol.messages import ProtocolError
from nodes.reference import _scrubber_vendored, owner_turns
from nodes.reference.fixtures.factory import build_fixture_home, fixture_policy
from nodes.reference.owner_turns import (
    CAPABILITIES,
    CLAUDE,
    CODEX,
    FIXTURES_DIR,
    read_owner_turns,
)
from nodes.reference.readers import Readers

NOW = datetime(2026, 11, 21, tzinfo=UTC)
NOW_TEXT = "2026-11-21T00:00:00.000Z"
CORPUS = Path(__file__).resolve().parents[2] / "memory" / "tests" / "scrubber_corpus"
# No result frame may ever carry these (assistant text, tool calls and
# results, meta/sidechain/compact/summary lines, markers, unknown kinds,
# rows outside the prefixes, raw fixture secrets).
FORBIDDEN_PHRASES = [
    "ASSISTANT-ONLY-PHRASE",
    "TOOL-RESULT-PHRASE",
    "TOOL-MIXED-PHRASE",
    "META-PHRASE",
    "MARKER-PHRASE",
    "SIDECHAIN-PHRASE",
    "COMPACT-PHRASE",
    "SUMMARY-PHRASE",
    "IMAGE-PHRASE",
    "UNKNOWN-PHRASE",
    "EVENT-MSG-PHRASE",
    "DEVELOPER-PHRASE",
    "SCRATCH-PHRASE",
    "hunter2",
    "abcDEF123456789xyzTOKENvalue0001",
    "Zx81Kd02LmQpRt5UvWy7Ab9Cd3Ef4Gh",
]
PHASE9_CONTRACT_KEYS = {
    "name",
    "version",
    "class",
    "data_class",
    "description",
    "path_args",
    "workspace_resolution",
    "max_result_bytes",
    "args_schema",
    "result_schema",
}


def args(**overrides):
    base = {
        "since": "2026-11-20T00:00:00.000Z",
        "cwd_prefixes": ["/Users/owner/code", "/Users/owner/work/acme"],
        "max_sessions": 50,
        "max_turns": 200,
    }
    base.update(overrides)
    return base


def read(capability, root=FIXTURES_DIR, now=NOW, **overrides):
    reply = read_owner_turns(capability, args(**overrides), root=root, granted=True, now=now)
    assert reply["status"] == "ok", reply
    return reply["output"]


def turn_ids(output):
    return [t["turn_id"] for s in output["sessions"] for t in s["turns"]]


def frame_text(output):
    return json.dumps(output, ensure_ascii=False)


# --- contracts -------------------------------------------------------------------


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_contracts_follow_the_phase9_format(capability):
    contract = owner_turns.load_contract(capability)
    assert set(contract) == PHASE9_CONTRACT_KEYS
    assert contract["name"] == capability and contract["version"] == 1
    assert contract["class"] == "read" and contract["data_class"] == "personal"
    assert contract["workspace_resolution"] == "row_cwd"
    assert contract["path_args"] == ["cwd_prefixes"]
    jsonschema.Draft202012Validator.check_schema(contract["args_schema"])
    jsonschema.Draft202012Validator.check_schema(contract["result_schema"])


def test_both_contracts_share_their_schemas():
    claude, codex = (owner_turns.load_contract(c) for c in CAPABILITIES)
    assert claude["args_schema"] == codex["args_schema"]
    assert claude["result_schema"] == codex["result_schema"]


def test_the_contracts_are_server_known_read_contracts():
    """Runbook A12: they live in `nodes/capabilities/v1/` (ten v1 read
    contracts) and load like the other eight."""
    contracts = load_contracts()
    assert len(contracts) == 10
    for capability in CAPABILITIES:
        contract = contracts[capability]
        assert (contract.capability_class, contract.data_class) == ("read", "personal")
        assert owner_turns.CONTRACT_DIR / f"{capability}.json" == (
            Path(build.ROOT).parent / "capabilities" / "v1" / f"{capability}.json"
        )


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("/", True),
        ("~", True),
        ("~/code", True),
        ("/Users/owner/code friday", True),
        ("code/friday", False),
        ("~code", False),
        ("/a\x00b", False),
    ],
)
def test_the_path_patterns_hold_under_the_strict_full_match_validator(value, valid):
    """The staged 8B patterns were prefix-only (`^(/|~$|~/)`), which the
    protocol's full-match validator refuses for every real path."""
    contract = load_contracts()[CLAUDE]
    args = {"since": "2026-11-20T00:00:00.000Z", "cwd_prefixes": [value]}
    args |= {"max_sessions": 1, "max_turns": 1}
    if valid:
        contract.validate_args(args)
    else:
        with pytest.raises(ProtocolError):
            contract.validate_args(args)
    row = {
        "session_id": "s",
        "cwd": value,
        "turns": [{"turn_id": "t", "at": NOW_TEXT, "text": "x"}],
    }
    output = {"sessions": [row], "next_since": NOW_TEXT, "truncated": False}
    if valid:
        contract.validate_result(output)
    else:
        with pytest.raises(ProtocolError):
            contract.validate_result(output)


# --- owner turns only ----------------------------------------------------------------


def test_claude_returns_only_owner_turns():
    output = read(CLAUDE)
    assert turn_ids(output) == ["u-001", "u-014", "u-015", "u-020", "u-101"]
    assert [s["session_id"] for s in output["sessions"]] == ["cc-sess-1", "cc-sess-2"]


def test_codex_returns_only_owner_turns():
    output = read(CODEX)
    assert [s["session_id"] for s in output["sessions"]] == ["cx-thread-1", "cx-thread-2"]
    assert [t["text"] for s in output["sessions"] for t in s["turns"]] == [
        "Rename the reconciliation purpose in the runbook.",
        "The token dump from my shell:\nexport API_TOKEN=[SECRET:assignment]",
        "Acme staging runs on the blue cluster.",
    ]


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_no_excluded_text_in_any_result_frame(capability):
    text = frame_text(read(capability))
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in text, phrase


def test_every_claude_marker_is_excluded():
    for marker in owner_turns.CLAUDE_MARKERS:
        line = {
            "type": "user",
            "uuid": "u",
            "sessionId": "s",
            "cwd": "/a",
            "timestamp": "2026-11-20T00:00:00.000Z",
            "message": {"role": "user", "content": f"  {marker} body"},
        }
        assert owner_turns.claude_line_turn(line) is None, marker


def test_every_codex_marker_is_excluded():
    for marker in owner_turns.CODEX_MARKERS:
        line = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"{marker} body"}],
            },
        }
        assert owner_turns.codex_line_text(line) is None, marker


# The server re-check of the markers (memory.ingestion.transcripts) is
# compared with these readers in tools/tests/test_transcript_node_port.py:
# `nodes` may not import `memory`, not even in tests.


def test_turn_ids_are_stable_across_reads():
    assert turn_ids(read(CODEX)) == turn_ids(read(CODEX))


# --- cwd_prefixes ---------------------------------------------------------------------


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_cwd_prefixes_are_honoured(capability):
    personal = read(capability, cwd_prefixes=["/Users/owner/code"])
    assert {s["cwd"] for s in personal["sessions"]} == {"/Users/owner/code/friday"}
    firm = read(capability, cwd_prefixes=["/Users/owner/work/acme"])
    assert all(s["cwd"].startswith("/Users/owner/work/acme") for s in firm["sessions"])
    # The segment boundary: `/Users/owner/co` is not a prefix of `/code`.
    assert read(capability, cwd_prefixes=["/Users/owner/co"])["sessions"] == []


# --- caps, truncated, next_since -----------------------------------------------------


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_caps_set_truncated(capability):
    one = read(capability, max_turns=1)
    assert len(turn_ids(one)) == 1 and one["truncated"] is True
    assert one["next_since"] == one["sessions"][0]["turns"][0]["at"]
    sessions = read(capability, max_sessions=1)
    assert len(sessions["sessions"]) == 1 and sessions["truncated"] is True
    assert read(capability)["truncated"] is False


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_next_since_is_monotonic_and_pages_cover_everything(capability):
    """Paging with the server's rule (D13): the next `since` is the returned
    `next_since`, moved on by 1 ms when it did not advance."""
    seen = []
    since = "2026-11-20T00:00:00.000Z"
    for _ in range(40):
        page = read(capability, since=since, max_turns=2)
        assert page["next_since"] >= since
        seen.extend(t for t in turn_ids(page) if t not in seen)
        if not page["truncated"]:
            break
        if page["next_since"] == since:
            bumped = owner_turns.parse_instant(since) + timedelta(milliseconds=1)
            since = owner_turns.format_instant(bumped)
        else:
            since = page["next_since"]
    assert seen == turn_ids(read(capability))


def test_nothing_returned_keeps_next_since_at_since():
    page = read(CLAUDE, since="2026-11-20T23:00:00.000Z")
    assert page == {"sessions": [], "next_since": "2026-11-20T23:00:00.000Z", "truncated": False}


def _tie_root(tmp_path, turns):
    path = tmp_path / "claude" / "projects" / "tie" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "user",
                    "uuid": uuid,
                    "sessionId": "s",
                    "cwd": "/Users/owner/code/t",
                    "timestamp": at,
                    "message": {"role": "user", "content": f"turn {uuid}"},
                }
            )
            + "\n"
            for uuid, at in turns
        )
    )
    return tmp_path


def test_turns_tied_with_since_are_returned_again_and_none_is_skipped(tmp_path):
    tied = "2026-11-20T10:00:00.000Z"
    root = _tie_root(
        tmp_path, [("a", "2026-11-20T09:00:00.000Z")] + [(f"t{i}", tied) for i in range(5)]
    )
    first = read(CLAUDE, root=root, max_turns=3)
    assert turn_ids(first) == ["a", "t0", "t1"] and first["truncated"] is True
    assert first["next_since"] == tied
    second = read(CLAUDE, root=root, since=first["next_since"], max_turns=5)
    assert turn_ids(second) == ["t0", "t1", "t2", "t3", "t4"]  # inclusive, nothing skipped
    assert second["truncated"] is False


def test_turns_are_ordered_by_at_then_turn_id(tmp_path):
    root = _tie_root(
        tmp_path,
        [
            ("z", "2026-11-20T10:00:00.000Z"),
            ("b", "2026-11-20T09:00:00.000Z"),
            ("a", "2026-11-20T10:00:00.000Z"),
        ],
    )
    assert turn_ids(read(CLAUDE, root=root)) == ["b", "a", "z"]


def test_the_result_byte_budget_truncates(tmp_path, monkeypatch):
    root = _tie_root(tmp_path, [(f"t{i}", f"2026-11-20T10:00:0{i}.000Z") for i in range(5)])
    monkeypatch.setattr(owner_turns, "MAX_RESULT_BYTES", 300)
    page = read(CLAUDE, root=root)
    assert page["truncated"] is True and 0 < len(turn_ids(page)) < 5


# --- the argument contract ---------------------------------------------------------------


def test_not_granted_reads_nothing():
    reply = read_owner_turns(CLAUDE, args(), root=None, granted=False, now=NOW)
    assert reply == {"status": "refused", "code": "not_granted"}


def test_an_unknown_capability_is_refused():
    reply = read_owner_turns("claude.sessions.read", args(), root=None, granted=True, now=NOW)
    assert reply == {"status": "refused", "code": "unknown_capability"}


def test_since_is_bounded_to_seven_days():
    ok = args(since=(NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    assert read_owner_turns(CLAUDE, ok, root=FIXTURES_DIR, granted=True, now=NOW)["status"] == "ok"
    old = args(since=(NOW - timedelta(days=7, seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    reply = read_owner_turns(CLAUDE, old, root=FIXTURES_DIR, granted=True, now=NOW)
    assert reply == {"status": "refused", "code": "args_contract"}
    # The server's clamp (now - 7 days + 5 minutes) is always accepted.
    clamped = args(since=(NOW - timedelta(days=7) + timedelta(minutes=5)).isoformat())
    assert (
        read_owner_turns(CLAUDE, clamped, root=FIXTURES_DIR, granted=True, now=NOW)["status"]
        == "ok"
    )


# --- the folded conformance cases and the vector ---------------------------------------------

OWNER_CASES = [
    case
    for case in C.all_cases()[1]
    if case["steps"] and case["steps"][0].get("frame", {}).get("capability") in CAPABILITIES
]


def test_the_8b_cases_are_folded_into_the_v1_suite():
    """Runbook A12: every staged 8B case id is a v1 frame-level case, for
    both capabilities (the staged `owner_turns_cases.json` is gone)."""
    labels = {
        "not_granted",
        "cwd_prefixes_missing",
        "cwd_prefixes_empty",
        "cwd_prefixes_too_many",
        "cwd_prefix_relative",
        "cwd_prefix_duplicate",
        "since_older_than_7_days",
        "since_malformed",
        "since_missing",
        "max_turns_oversize",
        "max_turns_zero",
        "max_turns_bool",
        "max_sessions_oversize",
        "unknown_arg",
        "since_exactly_7_days",
        "only",
        "max_turns_truncates",
        "max_sessions_truncates",
    }
    expected = {
        f"v1_read_{tool}_owner_turns_{label}" for tool in ("claude", "codex") for label in labels
    }
    assert {case["name"] for case in OWNER_CASES} == expected
    assert not (Path(build.ROOT).parents[1] / "integrations" / "node_reference").exists()


@pytest.mark.parametrize("case", OWNER_CASES, ids=lambda case: case["name"])
def test_the_reference_node_passes_each_owner_turn_case(case, tmp_path):
    home = build_fixture_home(tmp_path)
    result = run_case_in_process(case, home=home, worktree_base=tmp_path / "wt")
    assert result.failures == []


@pytest.mark.parametrize(
    "case",
    [c for c in OWNER_CASES if c["expect"]["frames"][0].get("refusal_code") == "args_schema"],
    ids=lambda case: case["name"],
)
def test_every_schema_refusal_is_refused_by_the_reader_too(case):
    """Defence in depth: the args the node's schema refuses are refused by
    the reader itself (`args_contract`), as the staged 8B cases pinned."""
    frame = case["steps"][0]["frame"]
    now = datetime.fromtimestamp(C.NOW, UTC)
    reply = read_owner_turns(
        frame["capability"], frame["args"], root=FIXTURES_DIR, granted=True, now=now
    )
    assert reply == {"status": "refused", "code": "args_contract"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            frame["args"], owner_turns.load_contract(frame["capability"])["args_schema"]
        )


def test_the_conformance_files_are_up_to_date():
    assert build.check() == []


def test_the_owner_turn_vector_matches_the_readers():
    expected = json.loads((Path(build.ROOT) / "vectors" / "owner_turns.json").read_text("utf-8"))
    assert expected["now"] == NOW_TEXT and expected["args"] == args()
    for capability in CAPABILITIES:
        assert expected["results"][capability] == read(capability, now=NOW)


def test_the_scrub_vector_covers_the_owner_turn_fixtures_and_the_corpus():
    document = json.loads((Path(build.ROOT) / "vectors" / "scrub.json").read_text("utf-8"))
    ids = {item["id"] for item in document["items"]}
    assert sum(1 for i in ids if i.startswith("corpus:transcripts:")) == len(TRANSCRIPT_CORPUS)
    strings = set(build.fixture_strings())
    assert "Use the cheap tier for triage calls." in strings  # an owner-turn fixture string
    for text in strings:
        digest = build.hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        assert f"fixture:{digest}" in ids


# --- the reference node's dispatch (readers.py) ---------------------------------------------


@pytest.fixture
def home(tmp_path):
    return build_fixture_home(tmp_path)


def _readers(home, **grants):
    return Readers(
        home=home, policy=fixture_policy(home), grants={"claude": True, "codex": True, **grants}
    )


def _node_args(**overrides):
    return C._owner_turn_args(**overrides)


def _node_read(readers, capability, **overrides):
    return readers.read(capability, _node_args(**overrides), now=C.NOW)


def test_the_readers_advertise_the_owner_turn_reads_behind_the_directory_grants(home):
    assert {name: _readers(home).availability()[name] for name in CAPABILITIES} == {
        CLAUDE: "granted",
        CODEX: "granted",
    }
    off = _readers(home, claude=False, codex=False)
    assert {name: off.availability()[name] for name in CAPABILITIES} == {
        CLAUDE: "not_granted",
        CODEX: "not_granted",
    }
    # The reader refuses too (defence in depth), and reads nothing.
    assert _node_read(off, CLAUDE).status == "error"
    assert _readers(home, codex=False).read(CODEX, _node_args(), now=C.NOW).status == "error"


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_the_dispatch_returns_valid_bounded_scrubbed_owner_turns(home, capability):
    outcome = _node_read(_readers(home), capability)
    assert outcome.status == "ok" and outcome.truncated is False
    load_contracts()[capability].validate_result(outcome.output)
    text = json.dumps(outcome.output, ensure_ascii=False)
    for phrase in (*C.FIXTURE_SECRETS, *C.NEVER_RETURNED, *C.NOT_OWNER_TURNS):
        assert phrase not in text, phrase
    assert {s["cwd"] for s in outcome.output["sessions"]} == {"~/work/acme", "~/code/friday"}


def test_the_cwd_is_reported_in_the_form_of_the_matching_prefix(home):
    readers = _readers(home)
    absolute = _node_read(readers, CLAUDE, cwd_prefixes=[f"{home}/work/acme"]).output
    assert [s["cwd"] for s in absolute["sessions"]] == [f"{home}/work/acme"]
    tilde = _node_read(readers, CLAUDE, cwd_prefixes=["~/work/acme"]).output
    assert [s["cwd"] for s in tilde["sessions"]] == ["~/work/acme"]
    # Both forms asked for: the absolute one wins.
    both = _node_read(readers, CLAUDE, cwd_prefixes=["~/work", f"{home}/work/acme"]).output
    assert [s["cwd"] for s in both["sessions"]] == [f"{home}/work/acme", "~/work/acme-secret"]


def test_the_prefix_match_is_at_segment_boundaries(home):
    readers = _readers(home)
    assert _node_read(readers, CLAUDE, cwd_prefixes=["~/work/ac"]).output["sessions"] == []
    names = [
        s["session_id"]
        for s in _node_read(readers, CLAUDE, cwd_prefixes=["~/work/acme"]).output["sessions"]
    ]
    assert names == ["cc-acme-1"]  # never ~/work/acme-secret


def test_a_symlinked_transcript_is_never_read(home, tmp_path):
    outside = tmp_path / "outside" / "evil.jsonl"
    outside.parent.mkdir()
    line = {
        "type": "user",
        "uuid": "u-evil",
        "sessionId": "cc-evil-1",
        "cwd": f"{home}/work/acme",
        "timestamp": "2026-11-29T23:00:00.000Z",
        "message": {"role": "user", "content": "EVIL-PHRASE"},
    }
    outside.write_text(json.dumps(line) + "\n")
    link = Path(home) / ".claude" / "projects" / "-HOME-work-acme" / "cc-evil-1.jsonl"
    link.symlink_to(outside)
    output = _node_read(_readers(home), CLAUDE).output
    assert "EVIL-PHRASE" not in json.dumps(output)


def test_an_identifier_the_scrub_would_change_is_not_returned(home):
    path = Path(home) / ".claude" / "projects" / "-HOME-work-acme" / "cc-acme-1.jsonl"
    secret = "AKIA" + "FIXTUREFAKE12345"
    line = {
        "type": "user",
        "uuid": secret,
        "sessionId": "cc-acme-1",
        "cwd": f"{home}/work/acme",
        "timestamp": "2026-11-29T23:00:00.000Z",
        "message": {"role": "user", "content": "IDENT-PHRASE"},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    text = json.dumps(_node_read(_readers(home), CLAUDE).output)
    assert "IDENT-PHRASE" not in text and secret not in text


def test_codex_turn_ids_equal_the_transcript_root_form(home):
    """The dispatch hashes the same `codex/sessions/...` relative path as
    `read_owner_turns` over a transcript root (the Linux node's view)."""
    node = _node_read(_readers(home), CODEX).output
    root = Path(home) / "view"
    (root / "codex").mkdir(parents=True)
    (root / "codex" / "sessions").symlink_to(Path(home) / ".codex" / "sessions")
    prefixes = [f"{home}/work/acme", f"{home}/code/friday"]
    now = datetime.fromtimestamp(C.NOW, UTC)
    direct = read_owner_turns(
        CODEX, _node_args(cwd_prefixes=prefixes), root=root, granted=True, now=now
    )["output"]
    ids = [t["turn_id"] for s in node["sessions"] for t in s["turns"]]
    assert ids and ids == [t["turn_id"] for s in direct["sessions"] for t in s["turns"]]


def test_a_canonical_size_cut_marks_the_owner_turn_result_truncated(home):
    """`_fit` (the canonical size bound) cutting an owner-turn result sets
    its own `truncated` flag too."""
    readers = _readers(home)
    small = dataclasses.replace(readers.contracts[CLAUDE], max_result_bytes=400)
    readers.contracts = {**readers.contracts, CLAUDE: small}
    outcome = _node_read(readers, CLAUDE)
    assert outcome.status == "ok" and outcome.truncated is True
    assert outcome.output["truncated"] is True
    assert len(outcome.output["sessions"]) < 2


def _interleaved_output():
    """Two sessions whose turns interleave in time: dropping the last
    session whole would drop `b-1` (09:01) and keep `a-2` (09:02)."""
    return {
        "sessions": [
            {
                "session_id": "cc-a",
                "cwd": "~/work/acme",
                "turns": [
                    {"turn_id": "a-1", "at": "2026-11-29T09:00:00.000Z", "text": "x" * 40},
                    {"turn_id": "a-2", "at": "2026-11-29T09:02:00.000Z", "text": "x" * 40},
                ],
            },
            {
                "session_id": "cc-b",
                "cwd": "~/work/acme",
                "turns": [
                    {"turn_id": "b-1", "at": "2026-11-29T09:01:00.000Z", "text": "x" * 40},
                    {"turn_id": "b-2", "at": "2026-11-29T09:03:00.000Z", "text": "x" * 40},
                ],
            },
        ],
        "next_since": "2026-11-29T09:03:00.000Z",
        "truncated": False,
    }


def _dropped_and_kept(original, fitted):
    kept = {t["turn_id"] for s in fitted["sessions"] for t in s["turns"]}
    every = [t for s in original["sessions"] for t in s["turns"]]
    dropped = [t for t in every if t["turn_id"] not in kept]
    return dropped, [t for t in every if t["turn_id"] in kept]


@pytest.mark.parametrize("limit", [200, 300, 400, 500])
def test_a_canonical_size_cut_never_moves_the_cursor_past_a_dropped_turn(home, limit):
    """Review follow-up (A12): `_fit` drops the newest turns first and
    recomputes `next_since` from the turns left, so every dropped turn is
    at or after it (the next pull, `at >= since`, returns it again)."""
    from nodes.protocol.canonical import canonicalize
    from nodes.reference.readers import _fit_owner_turns

    readers = _readers(home)
    original = _interleaved_output()
    small = dataclasses.replace(readers.contracts[CLAUDE], max_result_bytes=limit)
    readers.contracts = {**readers.contracts, CLAUDE: small}
    fitted, truncated = readers._fit(CLAUDE, original)
    assert truncated is True and fitted["truncated"] is True
    assert len(canonicalize(fitted)) <= limit or not fitted["sessions"]
    dropped, kept = _dropped_and_kept(original, fitted)
    assert dropped, "the limit must force a cut"
    assert all(t["at"] >= fitted["next_since"] for t in dropped)
    if kept:
        assert fitted["next_since"] == max(t["at"] for t in kept)
    assert all(s["turns"] for s in fitted["sessions"])
    assert fitted == _fit_owner_turns(original, limit)


def test_the_canonical_cut_keeps_the_oldest_turns_across_sessions(home):
    from nodes.protocol.canonical import canonicalize

    original = _interleaved_output()
    readers = _readers(home)
    # Room for exactly the three oldest turns (a-1, b-1, a-2).
    three = {
        **original,
        "sessions": [
            {**original["sessions"][0]},
            {**original["sessions"][1], "turns": original["sessions"][1]["turns"][:1]},
        ],
        "next_since": "2026-11-29T09:02:00.000Z",
        "truncated": True,
    }
    limit = len(canonicalize(three))
    small = dataclasses.replace(readers.contracts[CLAUDE], max_result_bytes=limit)
    readers.contracts = {**readers.contracts, CLAUDE: small}
    fitted, _ = readers._fit(CLAUDE, original)
    assert fitted == three


def test_an_owner_turn_result_within_the_bound_is_not_cut(home):
    readers = _readers(home)
    original = _interleaved_output()
    assert readers._fit(CLAUDE, original) == (original, False)


# --- scrub ----------------------------------------------------------------------------------


def _corpus(name):
    rows = []
    for line in (CORPUS / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["text"] = row["text"].replace("<<>>", "")
            row["needles"] = [n.replace("<<>>", "") for n in row["needles"]]
            rows.append(row)
    return rows


TRANSCRIPT_CORPUS = _corpus("transcripts.jsonl")


def test_the_vendored_node_scrubber_has_full_secrets_recall_on_transcripts():
    secrets = [r for r in TRANSCRIPT_CORPUS if r["class"] == "secret"]
    assert len(secrets) >= 8
    missed = [
        r["id"]
        for r in secrets + _corpus("secrets.jsonl")
        if any(n in _scrubber_vendored.scrub_text(r["text"]).text for n in r["needles"])
    ]
    assert missed == []


def test_corpus_has_both_tools_and_turkish_pii():
    assert {r["tool"] for r in TRANSCRIPT_CORPUS} == {"claude", "codex"}
    assert {r["type"] for r in TRANSCRIPT_CORPUS if r["class"] == "pii"} >= {
        "tckn",
        "phone",
        "iban",
    }


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_no_raw_corpus_secret_in_any_result_frame(capability, tmp_path):
    texts = [r["text"] for r in TRANSCRIPT_CORPUS]
    if capability == CLAUDE:
        path = tmp_path / "claude" / "projects" / "p" / "s.jsonl"
        lines = [
            {
                "type": "user",
                "uuid": f"c{i}",
                "sessionId": "s",
                "cwd": "/Users/owner/code/p",
                "timestamp": f"2026-11-20T10:00:{i:02d}.000Z",
                "message": {"role": "user", "content": text},
            }
            for i, text in enumerate(texts)
        ]
    else:
        path = tmp_path / "codex" / "sessions" / "2026" / "11" / "20" / "rollout.jsonl"
        lines = [
            {
                "timestamp": "2026-11-20T10:00:00.000Z",
                "type": "session_meta",
                "payload": {"id": "cx", "cwd": "/Users/owner/code/p"},
            }
        ] + [
            {
                "timestamp": f"2026-11-20T10:00:{i:02d}.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
            for i, text in enumerate(texts)
        ]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))
    output = read(capability, root=tmp_path)
    assert len(turn_ids(output)) == len(texts)
    frame = frame_text(output)
    for row in TRANSCRIPT_CORPUS:
        for needle in row["needles"]:
            assert needle not in frame, row["id"]


def test_texts_are_cut_to_4000_characters_after_scrubbing(tmp_path):
    root = _tie_root(tmp_path, [("long", "2026-11-20T10:00:00.000Z")])
    path = root / "claude" / "projects" / "tie" / "s.jsonl"
    line = json.loads(path.read_text())
    line["message"]["content"] = "word " * 2000
    path.write_text(json.dumps(line) + "\n")
    text = read(CLAUDE, root=root)["sessions"][0]["turns"][0]["text"]
    assert len(text) == 4000


# --- purity -------------------------------------------------------------------------------


def test_the_owner_turn_readers_import_only_the_stdlib_and_the_vendored_scrubber():
    import sys

    tree = ast.parse(Path(owner_turns.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names - set(sys.stdlib_module_names) == {"nodes.reference"}


# --- the protocol's result bound ---------------------------------------------------------


def test_a_full_budget_result_fits_the_protocol_result_bound_however_it_is_measured(tmp_path):
    """The contract's `max_result_bytes` is the protocol's 64 KiB; the
    reader's 60 KiB budget leaves room for the gateway's spaced measure
    (`json.dumps` with the default separators), with many short turns in
    50 sessions (the most separators) or long multi-byte texts."""
    from nodes.protocol.canonical import canonicalize
    from nodes.protocol.messages import MAX_RESULT_BYTES

    assert owner_turns.MAX_RESULT_BYTES < load_contracts()[CLAUDE].max_result_bytes
    assert load_contracts()[CLAUDE].max_result_bytes == MAX_RESULT_BYTES
    for label, text, sessions in (("short", "ok", 50), ("long", "çğıöşü " * 600, 3)):
        path = tmp_path / label / "claude" / "projects" / "p"
        path.mkdir(parents=True)
        for s in range(sessions):
            lines = [
                {
                    "type": "user",
                    "uuid": f"u-{s}-{i}",
                    "sessionId": f"s-{s}",
                    "cwd": "/Users/owner/code/p",
                    "timestamp": f"2026-11-20T10:{s:02d}:{i:02d}.000Z",
                    "message": {"role": "user", "content": text},
                }
                for i in range(5)
            ]
            (path / f"s-{s}.jsonl").write_text(
                "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
            )
        output = read(CLAUDE, root=tmp_path / label, cwd_prefixes=["/Users/owner/code"])
        load_contracts()[CLAUDE].validate_result(output)
        assert len(canonicalize(output)) <= MAX_RESULT_BYTES
        assert len(json.dumps(output, ensure_ascii=False).encode("utf-8")) <= MAX_RESULT_BYTES
    assert output["truncated"] is True  # the long texts hit the budget
