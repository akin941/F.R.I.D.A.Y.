"""Node-side streaming scrub with carry-over (Phase 10 brief D12) and the
vendored scrubber (Phase 9 D13 item 6)."""

import base64
import json
from pathlib import Path

import pytest

from nodes.conformance import build
from nodes.conformance.cases import FIXTURE_SECRETS
from nodes.protocol.messages import MAX_OUTPUT_CHUNK_BYTES, MAX_TAIL_BYTES
from nodes.reference import vendor_scrubber
from nodes.reference._scrubber_vendored import scrub_text
from nodes.reference.stream_scrub import (
    MAX_OUTPUT_BYTES,
    SCRUB_CARRY_BYTES,
    OutputPipeline,
    StreamScrubber,
    split_utf8,
    utf8_len,
)

CORPUS = Path(__file__).resolve().parents[2] / "memory" / "tests" / "scrubber_corpus"


def _rows(name):
    rows = []
    for line in (CORPUS / f"{name}.jsonl").read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["text"] = row["text"].replace("<<>>", "")
            row["needles"] = [n.replace("<<>>", "") for n in row.get("needles", [])]
            rows.append(row)
    return rows


SECRET_ROWS = [row for row in _rows("secrets") if row["class"] == "secret"]


def test_vendored_scrubber_is_identical_to_the_source():
    assert vendor_scrubber.TARGET.read_text("utf-8") == vendor_scrubber.render()
    assert vendor_scrubber.main(["--check"]) == 0


def test_vendored_scrubber_reaches_full_secret_recall():
    for row in SECRET_ROWS:
        out = scrub_text(row["text"]).text
        assert not any(needle in out for needle in row["needles"]), row["id"]


@pytest.mark.parametrize("row", SECRET_ROWS, ids=[row["id"] for row in SECRET_ROWS])
def test_a_secret_split_at_every_byte_offset_never_leaks(row):
    """Every corpus secret, split at every byte offset (mid-UTF-8 too)
    across two chunks, is absent from the emitted output and the tail."""
    raw = ("prefix line\n" + row["text"] + " trailing\n").encode("utf-8")
    for offset in range(len(raw) + 1):
        pipeline = OutputPipeline()
        pieces = pipeline.feed("stdout", raw[:offset])
        pieces += pipeline.feed("stdout", raw[offset:])
        pieces += pipeline.finish()
        emitted = "".join(piece.data for piece in pieces)
        for needle in row["needles"]:
            assert needle not in emitted, (row["id"], offset)
            assert needle not in pipeline.tail, (row["id"], offset)


def test_a_secret_split_across_three_chunks_never_leaks():
    for row in SECRET_ROWS:
        raw = ("x\n" + row["text"] + "\n").encode("utf-8")
        third = max(len(raw) // 3, 1)
        pipeline = OutputPipeline()
        pieces = []
        for start in range(0, len(raw), third):
            pieces += pipeline.feed("stderr", raw[start : start + third])
        pieces += pipeline.finish()
        emitted = "".join(piece.data for piece in pieces)
        assert not any(needle in emitted for needle in row["needles"]), row["id"]


def test_benign_text_passes_through_unchanged():
    text = "".join(row["text"] + "\n" for row in _rows("benign"))
    pipeline = OutputPipeline()
    pieces = pipeline.feed("stdout", text.encode())
    pieces += pipeline.finish()
    assert "".join(p.data for p in pieces) == scrub_text(text).text


def test_complete_lines_are_emitted_and_the_partial_line_is_carried():
    scrubber = StreamScrubber()
    assert scrubber.push("line one\nline two\npart") == "line one\nline two\n"
    assert scrubber.carry_size == 4
    assert scrubber.push("ial\n") == "partial\n"
    assert scrubber.flush() == ""


def test_the_carry_is_bounded():
    scrubber = StreamScrubber()
    for _ in range(20):
        scrubber.push("w" * 3000 + " ")
        assert scrubber.carry_size <= SCRUB_CARRY_BYTES + MAX_OUTPUT_CHUNK_BYTES


def test_a_cut_never_lands_inside_a_code_point_and_pieces_fit_8k():
    text = "ğ" * 5000  # two bytes each
    pieces = split_utf8(text)
    assert "".join(pieces) == text
    assert all(utf8_len(piece) <= MAX_OUTPUT_CHUNK_BYTES for piece in pieces)
    raw = ("çok güzel ✓ " * 50).encode()
    for offset in range(1, 30):
        pipeline = OutputPipeline()
        pieces = pipeline.feed("stdout", raw[:offset]) + pipeline.feed("stdout", raw[offset:])
        pieces += pipeline.finish()
        assert "".join(p.data for p in pieces) == raw.decode()


def test_output_over_one_mib_is_truncated_and_seq_increases():
    pipeline = OutputPipeline()
    pieces = []
    for _ in range(40):
        pieces += pipeline.feed("stdout", (b"z" * 63 + b"\n") * 1024)
    pieces += pipeline.finish()
    assert pipeline.truncated
    assert pipeline.output_bytes == 40 * 64 * 1024
    assert sum(utf8_len(p.data) for p in pieces) <= MAX_OUTPUT_BYTES
    assert [p.seq for p in pieces] == list(range(len(pieces)))
    assert all(utf8_len(p.data) <= MAX_OUTPUT_CHUNK_BYTES for p in pieces)


def test_the_tail_is_the_last_4k_of_scrubbed_text():
    pipeline = OutputPipeline()
    pipeline.feed("stdout", b"a" * 10_000 + b"\n")
    pipeline.feed("stderr", "son satır ✓\n".encode())
    pipeline.finish()
    assert utf8_len(pipeline.tail) <= MAX_TAIL_BYTES
    assert pipeline.tail.endswith("son satır ✓\n")


def test_unknown_stream_is_refused():
    with pytest.raises(ValueError):
        OutputPipeline().feed("stdin", b"x")


def test_scrub_stream_vectors_are_current_and_carry_no_raw_secret():
    path = build.ROOT / "vectors" / "scrub_stream.json"
    assert path.read_text("utf-8") == build.rendered_files()["vectors/scrub_stream.json"]
    text = path.read_text("utf-8")
    for row in SECRET_ROWS:
        assert not any(needle in text for needle in row["needles"]), row["id"]
    document = json.loads(text)
    assert {"text", "pipeline"} <= set(document)
    assert len(document["text"]) >= len(SECRET_ROWS) * 2


# --- Phase 9 branch 3: recall over the whole corpus, scrub.json parity ------

ALL_SECRET_ROWS = [
    row for name in ("secrets", "transcripts") for row in _rows(name) if row["class"] == "secret"
]


def test_vendored_scrubber_reaches_full_secret_recall_on_the_whole_corpus():
    """The corpus files are read as data; nothing imports `memory`."""
    assert len(ALL_SECRET_ROWS) > len(SECRET_ROWS)
    for row in ALL_SECRET_ROWS:
        out = scrub_text(row["text"]).text
        assert not any(needle in out for needle in row["needles"]), row["id"]


def test_scrub_vectors_are_current_complete_and_carry_no_raw_secret():
    path = build.ROOT / "vectors" / "scrub.json"
    text = path.read_text("utf-8")
    assert text == build.rendered_files()["vectors/scrub.json"]
    for row in ALL_SECRET_ROWS:
        assert not any(needle in text for needle in row["needles"]), row["id"]
    for secret in FIXTURE_SECRETS[:4]:
        assert secret not in text
    items = json.loads(text)["items"]
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    corpus_ids = {
        f"corpus:{name}:{row['id']}"
        for name in ("benign", "pii", "secrets", "transcripts")
        for row in _rows(name)
    }
    assert corpus_ids <= set(ids)
    inputs = {base64.b64decode(item["input_b64"]).decode("utf-8") for item in items}
    assert set(build.fixture_strings()) <= inputs
    for item in items:
        raw = base64.b64decode(item["input_b64"]).decode("utf-8")
        assert scrub_text(raw).text == item["expected"], item["id"]


def test_fixture_strings_include_the_transcripts_with_their_fake_secrets():
    strings = build.fixture_strings()
    joined = "\n".join(strings)
    for secret in FIXTURE_SECRETS:
        assert secret in joined
    assert any("/Users/owner/work/acme" in s for s in strings)
    assert not any("{{HOME}}" in s or "<<>>" in s for s in strings)


def test_a_changed_scrub_vector_is_drift(monkeypatch):
    original = build.rendered_files

    def changed():
        files = original()
        files["vectors/scrub.json"] = files["vectors/scrub.json"].replace("PII:email", "PII:x", 1)
        return files

    monkeypatch.setattr(build, "rendered_files", changed)
    assert build.check() == ["vectors/scrub.json"]
