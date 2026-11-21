"""The reference owner-turn readers, their contracts and conformance cases
(Phase 8B branch 8, brief D13/D14/D17). Pure Python: no database."""

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest

from integrations.cli import _scrubber_vendored
from integrations.node_reference import build_conformance, owner_turns
from integrations.node_reference.owner_turns import (
    CAPABILITIES,
    CLAUDE,
    CODEX,
    FIXTURES_DIR,
    read_owner_turns,
)

NOW = datetime(2026, 11, 21, tzinfo=UTC)
CORPUS = Path(__file__).resolve().parents[2] / "memory" / "tests" / "scrubber_corpus"
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
    for phrase in build_conformance.FORBIDDEN_PHRASES:
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


def test_the_server_recheck_covers_every_reader_marker():
    from memory.ingestion.transcripts import INJECTED_MARKERS

    assert set(owner_turns.CLAUDE_MARKERS) | set(owner_turns.CODEX_MARKERS) == set(INJECTED_MARKERS)


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


# --- conformance cases and expected output --------------------------------------------------


CASES = json.loads(build_conformance.CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["id"])
def test_conformance_case(case):
    now = owner_turns.parse_instant(CASES["now"])
    reply = read_owner_turns(
        case["capability"], case["args"], root=FIXTURES_DIR, granted=case["granted"], now=now
    )
    assert reply["status"] == case["expect"]
    if case["expect"] == "refused":
        assert reply["code"] == case["code"]
        return
    output = reply["output"]
    contract = owner_turns.load_contract(case["capability"])
    jsonschema.validate(case["args"], contract["args_schema"])
    jsonschema.validate(output, contract["result_schema"])
    for phrase in case.get("forbidden_phrases", []):
        assert phrase not in frame_text(output)
    if "truncated" in case:
        assert output["truncated"] is case["truncated"]
    if "turns" in case:
        assert len(turn_ids(output)) == case["turns"]
    if "sessions" in case:
        assert len(output["sessions"]) == case["sessions"]


@pytest.mark.parametrize(
    "case",
    [c for c in CASES["cases"] if c.get("code") == "args_contract"],
    ids=lambda case: case["id"],
)
def test_schema_level_refusals_agree_with_the_contract_schema(case):
    """Every `args_contract` case is refused by the schema too, except the
    age bound and the uniqueness of normalised prefixes the schema cannot
    express (the reader checks them on top)."""
    if case["id"].endswith("since_older_than_7_days"):
        pytest.skip("the 7-day bound is not expressible in JSON Schema")
    schema = owner_turns.load_contract(case["capability"])["args_schema"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(case["args"], schema)


def test_the_conformance_files_are_up_to_date():
    assert build_conformance.main(["--check"]) == 0


def test_expected_owner_turns_match_the_readers():
    expected = json.loads(build_conformance.EXPECTED_PATH.read_text(encoding="utf-8"))
    for capability in CAPABILITIES:
        assert expected["results"][capability] == read(capability, now=NOW)


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


def test_the_readers_import_only_the_stdlib_and_the_vendored_scrubber():
    import sys

    tree = ast.parse(Path(owner_turns.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names - set(sys.stdlib_module_names) == {"integrations.cli"}
