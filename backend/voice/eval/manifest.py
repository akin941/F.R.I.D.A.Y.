"""The evaluation-set manifest and the private set directory (brief A4).

The **manifest** (`docs/voice/eval-manifest.md`) is committed: a Markdown
table of file name, sha256, condition and reference transcript -- no audio,
no path. The **set** is the owner's directory of 16 kHz mono PCM16 WAVs,
outside the repo (default `~/FridayEval/asr`, mode 0700, no symlinks);
it is biometric data and never enters the repo, CI or Friday's database.

A recording whose sha256 differs from its manifest row is refused (the
reference text no longer describes it); a row with an empty sha256 is
"not recorded yet" and is filled by `voice_eval --fill-hashes`, which never
overwrites a hash already written.
"""

import hashlib
import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path

MANIFEST_MARKER = "<!-- voice-eval-manifest v1 -->"
COLUMNS = ("file", "sha256", "condition", "reference")
# `<language>-<environment>`: tr = Turkish, cs = Turkish/English
# code-switched, en = English; quiet room or room noise (TV, fan).
CONDITIONS = ("tr-quiet", "tr-noise", "cs-quiet", "cs-noise", "en-quiet", "en-noise")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.wav$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# 30 s of 16 kHz mono PCM16 plus a header; a bigger file is not an utterance.
MAX_CLIP_BYTES = 2 * 1024 * 1024
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


class ManifestError(ValueError):
    """The manifest or the set directory is unusable. Messages carry file
    names and line numbers only, never a reference transcript."""


@dataclass(frozen=True)
class Entry:
    file: str
    sha256: str
    condition: str
    reference: str
    line: int = 0

    @property
    def language(self):
        """The normalisation language: code-switched text is Turkish."""
        return "en" if self.condition.startswith("en-") else "tr"

    @property
    def group(self):
        return self.condition.split("-", 1)[0]


def _escape(text):
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _unescape(text):
    return text.replace("\\|", "|").replace("\\\\", "\\")


def parse_manifest(text):
    """Every table row after `MANIFEST_MARKER` -> `[Entry]`, validated."""
    lines = text.splitlines()
    try:
        start = lines.index(MANIFEST_MARKER)
    except ValueError:
        raise ManifestError(f"manifest has no `{MANIFEST_MARKER}` marker") from None
    entries, seen_files, seen_hashes, header = [], set(), {}, False
    for number, raw in enumerate(lines[start + 1 :], start=start + 2):
        line = raw.strip()
        if not line.startswith("|"):
            if header and line:
                break  # the table ended
            continue
        cells = [_unescape(c.strip()) for c in _CELL_SPLIT.split(line.strip("|"))]
        if not header:
            if tuple(c.lower() for c in cells) != COLUMNS:
                raise ManifestError(f"line {number}: header must be | {' | '.join(COLUMNS)} |")
            header = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # the separator row
        if len(cells) != len(COLUMNS):
            raise ManifestError(f"line {number}: expected {len(COLUMNS)} cells")
        file, sha, condition, reference = cells
        if not FILE_NAME.match(file):
            raise ManifestError(f"line {number}: bad file name {file!r}")
        if file in seen_files:
            raise ManifestError(f"line {number}: {file} is listed twice")
        sha = sha.lower()
        if sha and not SHA256.match(sha):
            raise ManifestError(f"line {number}: {file}: sha256 must be 64 hex digits or empty")
        if sha and sha in seen_hashes:
            raise ManifestError(f"line {number}: {file} has the same sha256 as {seen_hashes[sha]}")
        if condition not in CONDITIONS:
            raise ManifestError(
                f"line {number}: {file}: condition must be one of {', '.join(CONDITIONS)}"
            )
        if not reference.strip():
            raise ManifestError(f"line {number}: {file}: empty reference")
        seen_files.add(file)
        if sha:
            seen_hashes[sha] = file
        entries.append(Entry(file, sha, condition, reference, number))
    if not header:
        raise ManifestError("manifest has no table after the marker")
    return entries


def render_table(entries):
    rows = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for e in entries:
        rows.append(f"| {e.file} | {e.sha256} | {e.condition} | {_escape(e.reference)} |")
    return "\n".join(rows)


def replace_table(text, entries):
    """`text` with the table after the marker replaced by `entries`
    (everything before the marker and after the table is kept)."""
    lines = text.splitlines()
    start = lines.index(MANIFEST_MARKER)
    i = start + 1
    while i < len(lines) and not lines[i].strip().startswith("|"):
        i += 1
    j = i
    while j < len(lines) and lines[j].strip().startswith("|"):
        j += 1
    new = lines[:i] + render_table(entries).splitlines() + lines[j:]
    return "\n".join(new) + ("\n" if text.endswith("\n") else "")


def expand(path):
    return Path(os.path.expanduser(str(path)))


def check_private_dir(directory, *, label="evaluation set"):
    """Refuse a directory others could read, a symlink, or one owned by
    another user (root inside a container is allowed: bind mounts there
    report the host owner)."""
    directory = expand(directory)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        raise ManifestError(f"{label} directory {directory} does not exist") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ManifestError(f"{label} directory {directory} is not a real directory")
    if info.st_mode & 0o077:
        raise ManifestError(
            f"{label} directory {directory} is readable by others "
            f"(mode {stat.S_IMODE(info.st_mode):04o}); run `chmod 700` on it"
        )
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid not in (None, 0) and info.st_uid != uid:
        raise ManifestError(f"{label} directory {directory} is not owned by this user")
    return directory


def read_clip(directory, name):
    """The clip's bytes, read without following a symlink, size-capped."""
    path = Path(directory) / name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ManifestError(f"{name}: cannot be opened ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CLIP_BYTES:
            raise ManifestError(f"{name}: not a regular file of at most {MAX_CLIP_BYTES} B")
        return handle.read(MAX_CLIP_BYTES + 1)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SetCheck:
    clips: list  # [(Entry, bytes)] -- recorded, hash verified
    unrecorded: list  # [Entry] -- no hash in the manifest yet
    missing: list  # [Entry] -- hashed in the manifest, file absent
    unlisted: list  # [str] -- WAVs in the directory the manifest does not name


def load_set(directory, entries):
    """Match the set directory against the manifest. A hash mismatch is a
    `ManifestError` (always fatal); missing and unlisted files are
    reported for the caller to decide."""
    directory = check_private_dir(directory)
    clips, unrecorded, missing, mismatched = [], [], [], []
    for entry in entries:
        if not entry.sha256:
            unrecorded.append(entry)
            continue
        data = read_clip(directory, entry.file)
        if data is None:
            missing.append(entry)
            continue
        if sha256_hex(data) != entry.sha256:
            mismatched.append(entry.file)
            continue
        clips.append((entry, data))
    if mismatched:
        raise ManifestError(
            "sha256 differs from the manifest (re-recorded or edited?): " + ", ".join(mismatched)
        )
    listed = {e.file for e in entries}
    unlisted = sorted(
        p.name for p in directory.iterdir() if p.suffix.lower() == ".wav" and p.name not in listed
    )
    return SetCheck(clips, unrecorded, missing, unlisted)


def fill_hashes(directory, entries):
    """`(entries with empty hashes filled from the files, [filled names])`.
    A hash already in the manifest is never changed (a mismatch stays an
    error for `load_set`)."""
    directory = check_private_dir(directory)
    out, filled, seen = [], [], {e.sha256: e.file for e in entries if e.sha256}
    for entry in entries:
        if entry.sha256:
            out.append(entry)
            continue
        data = read_clip(directory, entry.file)
        if data is None:
            out.append(entry)
            continue
        digest = sha256_hex(data)
        if digest in seen:
            raise ManifestError(f"{entry.file} is the same recording as {seen[digest]}")
        seen[digest] = entry.file
        out.append(replace(entry, sha256=digest))
        filled.append(entry.file)
    return out, filled
