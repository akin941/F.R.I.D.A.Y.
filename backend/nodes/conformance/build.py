"""Render (or `--check`) the committed conformance files (Phase 9 brief D5,
Phase 10 brief branch 2; `manage.py build_node_conformance`):

- `cases/v1/<name>.json`, `cases/v2/<name>.json`: one file per case of
  `nodes.conformance.cases`;
- `vectors/sandbox.json`: the `srt` settings documents and process specs
  the reference generator produces for a fixed set of mappings (the Swift
  `SandboxPolicy` must reproduce them byte for byte, Mac session M2);
- `vectors/scrub_stream.json`: the streaming scrub with carry-over (D12)
  over fixed chunk sequences (the Swift `OutputStreamer` must reproduce
  every emitted piece, Mac session M1). Inputs are base64 so the file never
  carries a raw (fake) secret; outputs are scrubbed;
- `vectors/scrub.json` (Phase 9 branch 3): every `memory/tests/
  scrubber_corpus/*.jsonl` item and every string of the reader fixtures
  (transcripts rendered with the home `/Users/owner`, and the desktop
  fixture) with its expected output from the vendored scrubber (no
  workspace terms). The Swift scrubber port must reproduce every
  `expected` byte for byte (Mac session M2). Inputs are base64. The
  owner-turn fixtures (`reference/fixtures/owner_turns/`) are reader
  fixtures too (runbook A12);
- `vectors/owner_turns.json` (runbook A12; the staged 8B
  `expected_owner_turns.json`): the owner-turn readers' output
  (`nodes.reference.owner_turns.read_owner_turns`) over the committed
  owner-turn fixtures at a fixed instant. The Swift readers (runbook B4)
  must produce the same output for the same files.

`check()` lists every file that differs from its definition, and every
stale case file that no definition renders. Pure Python.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from nodes.conformance.cases import all_cases, render_case
from nodes.reference import owner_turns
from nodes.reference._scrubber_vendored import scrub_text
from nodes.reference.fixtures.factory import (
    FIXTURES_DIR,
    policy_document,
    render_template,
    transcript_templates,
)
from nodes.reference.local_policy import LocalPolicy
from nodes.reference.sandbox import build_sandbox
from nodes.reference.stream_scrub import OutputPipeline, StreamScrubber

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parents[1]
CORPUS_DIR = BACKEND / "memory" / "tests" / "scrubber_corpus"
CORPUS = CORPUS_DIR / "secrets.jsonl"
SPLIT_MARKER = "<<>>"

VECTOR_HOME = "/Users/owner"
VECTOR_TEMP = "/private/var/folders/fr/T/friday-run-0001"
VECTOR_WORKTREE = "/Users/owner/Library/Caches/ai.friday.node/worktrees/wt-0001"
VECTOR_NODE_ENV = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
    "HOME": VECTOR_HOME,
    "PYTHONHASHSEED": "0",
    "GITHUB_TOKEN": "ghp_" + "F" * 36,
    "AWS_SECRET_ACCESS_KEY": "F" * 40,
    "SSH_AUTH_SOCK": "/private/tmp/agent.sock",
}


def _render(document) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def sandbox_document() -> dict:
    policy = LocalPolicy.from_document(policy_document(), home=VECTOR_HOME)
    vectors = []
    for repo in policy.repos:
        for kind in sorted(repo.commands):
            command = repo.commands[kind]
            write_root = repo.real_root if command.mode == "in_place" else VECTOR_WORKTREE
            for contract_max in (900, 60):
                spec = build_sandbox(
                    command,
                    home=VECTOR_HOME,
                    repo_root=repo.real_root,
                    write_root=write_root,
                    temp_dir=VECTOR_TEMP,
                    contract_max_runtime=contract_max,
                    node_env=VECTOR_NODE_ENV,
                )
                vectors.append(
                    {
                        "name": f"{repo.name}.{kind}.contract_max_{contract_max}",
                        "inputs": {
                            "mapping": command.document(),
                            "home": VECTOR_HOME,
                            "repo_root": repo.real_root,
                            "write_root": write_root,
                            "temp_dir": VECTOR_TEMP,
                            "contract_max_runtime": contract_max,
                            "node_env_names": sorted(VECTOR_NODE_ENV),
                        },
                        "expected": spec.document(),
                        "settings_canonical": spec.settings_bytes().decode("utf-8"),
                    }
                )
    return {
        "description": (
            "Friday Node srt sandbox vectors (Phase 10 brief D4). For each node-local mapping "
            "the reference generator (backend/nodes/reference/sandbox.py) produces `expected`; "
            "`settings_canonical` is the exact settings document written for srt (canonical "
            "JSON). The node environment (`vector_node_env`) carries secret-shaped names that "
            "must never reach a sandbox. TEST DATA ONLY: every value is fake."
        ),
        "vector_node_env": VECTOR_NODE_ENV,
        "vectors": vectors,
    }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _corpus_rows():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows.append((row["id"], row["text"].replace(SPLIT_MARKER, "")))
    return rows


def _text_case(name, chunks):
    scrubber = StreamScrubber()
    pushes = [scrubber.push(chunk) for chunk in chunks]
    return {
        "name": name,
        "chunks_b64": [_b64(chunk.encode("utf-8")) for chunk in chunks],
        "emitted": pushes,
        "flush": scrubber.flush(),
    }


def _pipeline_case(name, chunks):
    pipeline = OutputPipeline()
    pieces = []
    for stream, data in chunks:
        pieces += pipeline.feed(stream, data)
    pieces += pipeline.finish()
    return {
        "name": name,
        "chunks": [{"stream": stream, "data_b64": _b64(data)} for stream, data in chunks],
        "pieces": [
            {"seq": p.seq, "stream": p.stream, "data": p.data, "truncated": p.truncated}
            for p in pieces
        ],
        "tail": pipeline.tail,
        "output_bytes": pipeline.output_bytes,
        "truncated": pipeline.truncated,
    }


def scrub_stream_document() -> dict:
    text_cases = []
    for row_id, text in _corpus_rows():
        full = "prefix line\n" + text + " trailing\n"
        for fraction in (3, 2):
            cut = len(full) // fraction
            text_cases.append(_text_case(f"{row_id}.split_1_{fraction}", [full[:cut], full[cut:]]))
    words = " ".join(f"word{i}" for i in range(600))
    text_cases.append(_text_case("long_line_no_newline", [words[:1500], words[1500:]]))
    text_cases.append(
        _text_case("many_lines", [f"line {i}\n" for i in range(5)] + ["partial", " end\n"])
    )
    pem = (
        "-----BEGIN " + "RSA PRIVATE KEY-----\n" + "MIIEow" + "IBAAKCAQEA" * 6 + "\n"
        "abcdefFAKEFAKE==\n-----END " + "RSA PRIVATE KEY-----\n"
    )
    text_cases.append(_text_case("pem_in_three_chunks", [pem[:30], pem[30:80], pem[80:]]))
    turkish = "Testler geçti: çğıöşü ✓\n".encode()
    pipeline_cases = [
        _pipeline_case(
            "utf8_split_mid_sequence", [("stdout", turkish[:17]), ("stdout", turkish[17:])]
        ),
        _pipeline_case(
            "stdout_stderr_interleaved",
            [("stdout", b"ok 1\n"), ("stderr", b"warn: x\n"), ("stdout", b"ok 2\n")],
        ),
        _pipeline_case("over_8k_line", [("stdout", b"a" * 9000 + b"\n")]),
    ]
    return {
        "description": (
            "Friday Node streaming scrub vectors (Phase 10 brief D12). `text` cases: each "
            "base64 chunk is pushed (decoded as UTF-8) through one StreamScrubber "
            "(SCRUB_CARRY_BYTES=1024, 8 KiB chunks, at most 64 cut candidates); `emitted[i]` is "
            "what push i returns and `flush` what the final flush returns. `pipeline` cases: raw "
            "bytes per stream through the OutputPipeline (incremental UTF-8 decoding with "
            "replacement, per-stream carry, shared seq, 8 KiB pieces, 1 MiB cap, 4 KiB tail). "
            "The scrubber is backend/memory/ingestion/scrubber.py (vendored in "
            "nodes/reference/_scrubber_vendored.py) with no workspace terms."
        ),
        "carry_bytes": 1024,
        "max_chunk_bytes": 8192,
        "max_cut_candidates": 64,
        "text": text_cases,
        "pipeline": pipeline_cases,
    }


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _strings(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def fixture_strings() -> list:
    """Every string of the reader fixtures, rendered with `VECTOR_HOME`
    (unparseable lines whole), deduplicated and sorted."""
    found = set()
    for _relative, text in transcript_templates():
        for line in render_template(text, VECTOR_HOME).splitlines():
            if not line.strip():
                continue
            try:
                found.update(_strings(json.loads(line)))
            except ValueError:
                found.add(line)
    desktop = json.loads((FIXTURES_DIR / "desktop.json").read_text(encoding="utf-8"))
    found.update(_strings(desktop))
    for path in sorted(owner_turns.FIXTURES_DIR.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                found.update(_strings(json.loads(line)))
            except ValueError:
                found.add(line)
    return sorted(s for s in found if s)


# The owner-turn vector's fixed instant and arguments (the staged 8B values).
OWNER_TURNS_NOW = "2026-11-21T00:00:00.000Z"
OWNER_TURNS_ARGS = {
    "since": "2026-11-20T00:00:00.000Z",
    "cwd_prefixes": ["/Users/owner/code", "/Users/owner/work/acme"],
    "max_sessions": 50,
    "max_turns": 200,
}


def owner_turns_document() -> dict:
    now = datetime.fromisoformat(OWNER_TURNS_NOW.replace("Z", "+00:00")).astimezone(UTC)
    results = {}
    for capability in owner_turns.CAPABILITIES:
        reply = owner_turns.read_owner_turns(
            capability, OWNER_TURNS_ARGS, root=owner_turns.FIXTURES_DIR, granted=True, now=now
        )
        results[capability] = reply["output"]
    return {"now": OWNER_TURNS_NOW, "args": OWNER_TURNS_ARGS, "results": results}


def _scrub_item(item_id, text):
    return {
        "id": item_id,
        "input_b64": _b64(text.encode("utf-8")),
        "expected": scrub_text(text).text,
    }


def scrub_document() -> dict:
    items = []
    for path in sorted(CORPUS_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                text = row["text"].replace(SPLIT_MARKER, "")
                items.append(_scrub_item(f"corpus:{path.stem}:{row['id']}", text))
    for text in fixture_strings():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        items.append(_scrub_item(f"fixture:{digest}", text))
    return {
        "description": (
            "Friday Node scrub parity vectors (Phase 9 brief D13 item 6, Mac session M2). Each "
            "item's base64 UTF-8 input, scrubbed by backend/memory/ingestion/scrubber.py "
            "(vendored in nodes/reference/_scrubber_vendored.py) with no workspace terms, gives "
            "`expected` exactly. Items: every memory/tests/scrubber_corpus/*.jsonl row "
            "(`corpus:<file>:<id>`) and every string of the reader fixtures, transcripts "
            "rendered with the home /Users/owner (`fixture:<sha256 prefix>`). TEST DATA ONLY: "
            "every secret is fake."
        ),
        "items": items,
    }


def rendered_files() -> dict:
    """`{path relative to nodes/conformance: text}` of every generated file."""
    files = {}
    for version, cases in all_cases().items():
        for case in cases:
            files[f"cases/v{version}/{case['name']}.json"] = render_case(case)
    files["vectors/sandbox.json"] = _render(sandbox_document())
    files["vectors/scrub_stream.json"] = _render(scrub_stream_document())
    files["vectors/scrub.json"] = _render(scrub_document())
    files["vectors/owner_turns.json"] = _render(owner_turns_document())
    return files


def write() -> list:
    files = rendered_files()
    for version_dir in ("cases/v1", "cases/v2"):
        for stale in (ROOT / version_dir).glob("*.json"):
            if f"{version_dir}/{stale.name}" not in files:
                stale.unlink()
    for relative, text in files.items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def check() -> list:
    """The generated paths that are missing, stale or differ."""
    files = rendered_files()
    drift = []
    for relative, text in files.items():
        path = ROOT / relative
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            drift.append(relative)
    for version_dir in ("cases/v1", "cases/v2"):
        for existing in (ROOT / version_dir).glob("*.json"):
            if f"{version_dir}/{existing.name}" not in files:
                drift.append(f"{version_dir}/{existing.name}")
    return sorted(drift)
