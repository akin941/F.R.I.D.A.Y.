"""Branch 3a manifest and private-set handling (brief A4): the committed
manifest parses and names no audio in the repo; a changed file fails the
hash check; hashes are filled once and never overwritten; the set
directory must be private."""

import os
from pathlib import Path

import pytest
from django.conf import settings

from voice.eval import fixtures
from voice.eval.manifest import (
    MANIFEST_MARKER,
    Entry,
    ManifestError,
    fill_hashes,
    load_set,
    parse_manifest,
    render_table,
    replace_table,
    sha256_hex,
)

REPO = Path(settings.BASE_DIR).parent
COMMITTED = REPO / "docs" / "voice" / "eval-manifest.md"


def manifest_text(rows):
    return f"# Manifest\n\nintro\n\n{MANIFEST_MARKER}\n{render_table(rows)}\n\ntrailer\n"


def private_dir(tmp_path, name="asr"):
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def test_committed_manifest_parses():
    entries = parse_manifest(COMMITTED.read_text(encoding="utf-8"))
    assert entries
    assert {e.condition for e in entries} >= {"tr-quiet", "tr-noise", "cs-quiet"}


def test_no_audio_is_committed_next_to_the_eval_code():
    """Real audio is never committed: no WAV (or other audio) under the
    eval code, its tests or docs/voice."""
    roots = [REPO / "docs" / "voice", REPO / "backend" / "voice"]
    found = [
        p
        for root in roots
        for p in root.rglob("*")
        if p.suffix.lower() in {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus"}
    ]
    assert not found


def test_roundtrip_and_pipe_escaping():
    rows = [Entry("a.wav", "", "tr-quiet", "bir | iki \\ üç")]
    parsed = parse_manifest(manifest_text(rows))
    assert parsed[0].reference == "bir | iki \\ üç"
    assert parsed[0].language == "tr" and parsed[0].group == "tr"


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("| ../x.wav |  | tr-quiet | a |", "bad file name"),
        ("| a.wav | abc | tr-quiet | a |", "64 hex"),
        ("| a.wav |  | loud | a |", "condition"),
        ("| a.wav |  | tr-quiet |  |", "empty reference"),
        ("| a.wav |  | tr-quiet |", "expected 4 cells"),
    ],
)
def test_bad_rows_are_refused(row, message):
    text = (
        f"{MANIFEST_MARKER}\n| file | sha256 | condition | reference |\n|---|---|---|---|\n{row}\n"
    )
    with pytest.raises(ManifestError, match=message):
        parse_manifest(text)


def test_duplicates_are_refused():
    sha = "a" * 64
    rows = [Entry("a.wav", sha, "tr-quiet", "x"), Entry("b.wav", sha, "tr-quiet", "y")]
    with pytest.raises(ManifestError, match="same sha256"):
        parse_manifest(manifest_text(rows))
    rows = [Entry("a.wav", "", "tr-quiet", "x"), Entry("a.wav", "", "tr-noise", "y")]
    with pytest.raises(ManifestError, match="listed twice"):
        parse_manifest(manifest_text(rows))


def test_missing_marker_is_refused():
    with pytest.raises(ManifestError, match="marker"):
        parse_manifest("| file | sha256 | condition | reference |\n")


def test_hash_check_fails_on_a_changed_file(tmp_path):
    directory = private_dir(tmp_path)
    clip = fixtures.clip(speech=0.5)
    (directory / "a.wav").write_bytes(clip)
    entries = [Entry("a.wav", sha256_hex(clip), "tr-quiet", "merhaba")]
    assert len(load_set(directory, entries).clips) == 1
    (directory / "a.wav").write_bytes(fixtures.clip(speech=0.6))
    with pytest.raises(ManifestError, match="sha256 differs"):
        load_set(directory, entries)


def test_load_set_reports_unrecorded_missing_and_unlisted(tmp_path):
    directory = private_dir(tmp_path)
    clip = fixtures.clip(speech=0.5)
    (directory / "extra.wav").write_bytes(clip)
    entries = [
        Entry("todo.wav", "", "tr-quiet", "a"),
        Entry("gone.wav", "b" * 64, "tr-quiet", "b"),
    ]
    check = load_set(directory, entries)
    assert [e.file for e in check.unrecorded] == ["todo.wav"]
    assert [e.file for e in check.missing] == ["gone.wav"]
    assert check.unlisted == ["extra.wav"] and check.clips == []


def test_fill_hashes_fills_once_and_keeps_existing(tmp_path):
    directory = private_dir(tmp_path)
    one, two = fixtures.clip(speech=0.5), fixtures.clip(speech=0.7)
    (directory / "one.wav").write_bytes(one)
    (directory / "two.wav").write_bytes(two)
    entries = [
        Entry("one.wav", "", "tr-quiet", "a"),
        Entry("two.wav", "c" * 64, "tr-noise", "b"),  # stale hash: kept
        Entry("later.wav", "", "cs-quiet", "c"),
    ]
    updated, filled = fill_hashes(directory, entries)
    assert filled == ["one.wav"]
    assert updated[0].sha256 == sha256_hex(one)
    assert updated[1].sha256 == "c" * 64
    assert updated[2].sha256 == ""
    text = replace_table(manifest_text(entries), updated)
    assert text.startswith("# Manifest") and text.rstrip().endswith("trailer")
    assert parse_manifest(text)[0].sha256 == sha256_hex(one)


def test_fill_hashes_refuses_the_same_recording_twice(tmp_path):
    directory = private_dir(tmp_path)
    clip = fixtures.clip(speech=0.5)
    (directory / "a.wav").write_bytes(clip)
    (directory / "b.wav").write_bytes(clip)
    entries = [Entry("a.wav", "", "tr-quiet", "a"), Entry("b.wav", "", "tr-quiet", "b")]
    with pytest.raises(ManifestError, match="same recording"):
        fill_hashes(directory, entries)


def test_set_directory_must_be_private(tmp_path):
    directory = private_dir(tmp_path)
    os.chmod(directory, 0o755)
    with pytest.raises(ManifestError, match="readable by others"):
        load_set(directory, [])
    with pytest.raises(ManifestError, match="does not exist"):
        load_set(tmp_path / "nope", [])
    link = tmp_path / "link"
    link.symlink_to(directory)
    with pytest.raises(ManifestError, match="not a real directory"):
        load_set(link, [])


def test_a_symlinked_clip_is_refused(tmp_path):
    directory = private_dir(tmp_path)
    target = tmp_path / "elsewhere.wav"
    target.write_bytes(fixtures.clip())
    (directory / "a.wav").symlink_to(target)
    with pytest.raises(ManifestError, match="cannot be opened"):
        load_set(directory, [Entry("a.wav", "d" * 64, "tr-quiet", "x")])
