"""`00_Inbox/` import-once (Phase 8B branch 7, brief D12; ADR-012 decision 12).

Runs only inside an approved, claimed `vault_export` run (`tools.export`
checks the claim first), and only when the owner has enabled the
`obsidian_inbox` ingestion source -- a disabled or paused source means not
one byte of the inbox is read.

The scan: `00_Inbox/` top level and one level of subfolders, `.md` only, at
most `MAX_FILES_PER_RUN` notes, each at most `MAX_NOTE_BYTES` (checked on
the opened descriptor before reading). Skipped and counted, never moved:
anything under `_imported/` (or a hidden folder), a symlink or special
file, an oversized or non-UTF-8 note, a note with unclosed frontmatter or an
empty body, and anything carrying `source: friday_export` (the echo-chamber
guard: Friday's own export is never re-imported).

Each remaining note goes through `memory.ingestion.ports.record_episode`
with `mapping_key = "vault:<vault-relative folder>"`, so an unmapped
folder is dropped and counted by the port and the note is **left in place**
for the owner to add a rule. A stored (or already-stored) note is then
moved to `00_Inbox/_imported/<YYYY-MM-DD>/` with `renameat` under validated
descriptors. Dedupe is the episode's `external_id` (sha256 of the path plus
the content), so a move that fails after the episode committed never
produces a duplicate on the next run.

The `obsidian_inbox` source is NOT personal-class: a normal note is
extracted by the ordinary (cloud-capable) route, like a git commit. Only a
note whose frontmatter says `sensitivity: private` (or any value other than
`normal`: fail closed) becomes a `private` episode, which extraction routes
to local providers only.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.utils import timezone

from memory.ingestion.mapping import VAULT_INBOX_ROOT, resolve_workspace
from memory.ingestion.ports import DROPPED_UNMAPPED, DUPLICATE, STORED, record_episode
from memory.ingestion.scrubber import scrub_text
from memory.models import Episode, IngestionSource
from tools.vault_paths import (
    VaultFileTooLargeError,
    VaultPathEscapeError,
    ensure_validated_subdir,
    entry_kind,
    list_validated,
    open_existing_subdir,
    read_validated_bytes,
    rename_validated,
)

logger = logging.getLogger(__name__)

INBOX_DIR = VAULT_INBOX_ROOT
IMPORTED_DIR = "_imported"
MAX_FILES_PER_RUN = 200
MAX_NOTE_BYTES = 64 * 1024
EPISODE_KIND = "obsidian.inbox_note"
_EXPORT_SOURCES = frozenset({"friday_export", "jarvis_export"})
_MAX_UNIQUE_SUFFIX = 100


@dataclass
class InboxStats:
    waiting: int = 0  # dry pass: notes a run would try to import
    imported: int = 0
    duplicate: int = 0
    unmapped: int = 0
    skipped: int = 0
    truncated: int = 0
    deferred: int = 0  # over the per-run cap, left for the next run
    move_failed: int = 0


def inbox_source_for(workspace):
    """The workspace's `obsidian_inbox` source, if one is registered. The
    seed creates it (disabled) in the owner's personal workspace only, so
    a firm workspace's run never reads the inbox."""
    return IngestionSource.objects.filter(
        workspace=workspace, kind=IngestionSource.Kind.OBSIDIAN_INBOX
    ).first()


def parse_inbox_note(text: str):
    """`(meta, body)` or `None`. Owner notes carry arbitrary YAML
    frontmatter (lists, nested keys), so unlike the export's strict parser
    this only reads top-level `key: value` lines and ignores the rest. An
    opening `---` without a closing one is `None` (skipped, left in place):
    reading such a note as all-body could miss its `sensitivity: private`.
    Values are lowercased and unquoted; only `source` and `sensitivity` are
    used."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() != "---":
            continue
        meta = {}
        for raw in lines[1:index]:
            if not raw.strip() or raw[:1] in (" ", "\t", "-", "#"):
                continue
            key, sep, value = raw.partition(":")
            if sep:
                meta[key.strip().lower()] = value.strip().strip("'\"").strip().lower()
        return meta, "".join(lines[index + 1 :])
    return None


def note_sensitivity(meta) -> str:
    value = meta.get("sensitivity", "")
    if value in ("", "normal"):
        return Episode.Sensitivity.NORMAL
    return Episode.Sensitivity.PRIVATE


def _external_id(rel_path: str, data: bytes) -> str:
    digest = hashlib.sha256(rel_path.encode("utf-8") + b"\x00" + data).hexdigest()
    return f"sha256:{digest}"


def _candidates(vault_root_real, inbox_fd, inbox_path, stats, stack):
    """`(folder_fd, folder_path, rel_folder, name)` for every `.md` regular
    file at the top level and one subfolder level down, in a stable order.
    Subfolder fds are pushed on `stack` (closed by the caller)."""
    found = []
    for name in sorted(list_validated(vault_root_real, inbox_fd, inbox_path)):
        if name.startswith(".") or name == IMPORTED_DIR:
            continue
        try:
            kind = entry_kind(inbox_fd, inbox_path, name)
        except FileNotFoundError:
            continue
        if kind == "file":
            if name.endswith(".md"):
                found.append((inbox_fd, inbox_path, INBOX_DIR, name))
            continue
        if kind != "dir":
            stats.skipped += 1  # a symlink or special file: never followed
            continue
        sub_path = f"{inbox_path}/{name}"
        try:
            sub_fd = open_existing_subdir(vault_root_real, inbox_fd, inbox_path, name)
        except VaultPathEscapeError:
            stats.skipped += 1
            continue
        if sub_fd is None:
            continue
        stack.append(sub_fd)
        for child in sorted(list_validated(vault_root_real, sub_fd, sub_path)):
            if child.startswith(".") or not child.endswith(".md"):
                continue
            try:
                child_kind = entry_kind(sub_fd, sub_path, child)
            except FileNotFoundError:
                continue
            if child_kind == "file":
                found.append((sub_fd, sub_path, f"{INBOX_DIR}/{name}", child))
            else:
                stats.skipped += 1
    return found


class _ImportedFolder:
    """`00_Inbox/_imported/<date>/`, created on first use only."""

    def __init__(self, vault_root_real, inbox_fd, inbox_path):
        self.vault_root_real = vault_root_real
        self.inbox_fd = inbox_fd
        self.inbox_path = inbox_path
        self.day = timezone.localdate().isoformat()
        self.path = f"{inbox_path}/{IMPORTED_DIR}/{self.day}"
        self._fds = []
        self.fd = None

    def ensure(self):
        if self.fd is None:
            imported_fd = ensure_validated_subdir(
                self.vault_root_real, self.inbox_fd, self.inbox_path, IMPORTED_DIR
            )
            self._fds.append(imported_fd)
            self.fd = ensure_validated_subdir(
                self.vault_root_real, imported_fd, f"{self.inbox_path}/{IMPORTED_DIR}", self.day
            )
            self._fds.append(self.fd)
        return self.fd

    def close(self):
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds = []


def _move(imported, folder_fd, folder_path, name):
    dst_fd = imported.ensure()
    stem = Path(name).stem
    for attempt in range(_MAX_UNIQUE_SUFFIX):
        dst_name = name if attempt == 0 else f"{stem}.{attempt}.md"
        try:
            rename_validated(
                imported.vault_root_real,
                folder_fd,
                folder_path,
                name,
                dst_fd,
                imported.path,
                dst_name,
            )
            return
        except FileExistsError:
            continue
    raise FileExistsError("no free name under _imported/")


def scan_inbox(vault_root_real, root_fd, workspace, *, dry_run=False) -> InboxStats:
    """The scan (and, unless `dry_run`, the import and move). Callers:
    `tools.export` inside a claimed run, and the approval's read-only dry
    pass (`dry_run=True`: counts only -- no episode, no move, no mkdir)."""
    stats = InboxStats()
    source = inbox_source_for(workspace)
    if source is None or not source.is_accepting:
        return stats
    inbox_path = f"{vault_root_real}/{INBOX_DIR}"
    inbox_fd = open_existing_subdir(vault_root_real, root_fd, vault_root_real, INBOX_DIR)
    if inbox_fd is None:
        return stats
    stack = [inbox_fd]
    imported = _ImportedFolder(vault_root_real, inbox_fd, inbox_path)
    try:
        candidates = _candidates(vault_root_real, inbox_fd, inbox_path, stats, stack)
        if len(candidates) > MAX_FILES_PER_RUN:
            stats.deferred = len(candidates) - MAX_FILES_PER_RUN
            candidates = candidates[:MAX_FILES_PER_RUN]
        for folder_fd, folder_path, rel_folder, name in candidates:
            _import_one(
                vault_root_real,
                source,
                stats,
                imported,
                folder_fd,
                folder_path,
                rel_folder,
                name,
                dry_run=dry_run,
            )
    finally:
        imported.close()
        for fd in reversed(stack):
            os.close(fd)
    return stats


def _import_one(
    vault_root_real, source, stats, imported, folder_fd, folder_path, rel_folder, name, *, dry_run
):
    try:
        data, info = read_validated_bytes(
            vault_root_real, folder_fd, folder_path, name, max_bytes=MAX_NOTE_BYTES
        )
        text = data.decode("utf-8")
    except (VaultFileTooLargeError, OSError, UnicodeDecodeError):
        stats.skipped += 1
        return
    parsed = parse_inbox_note(text)
    if parsed is None:
        stats.skipped += 1
        return
    meta, body = parsed
    if meta.get("source") in _EXPORT_SOURCES:
        stats.skipped += 1  # the echo-chamber guard
        return
    body = body.strip()
    if not body:
        stats.skipped += 1
        return
    mapping_key = f"vault:{rel_folder}"
    if dry_run:
        if resolve_workspace(source, mapping_key) is None:
            stats.unmapped += 1
        else:
            stats.waiting += 1
        return
    result = record_episode(
        source=source,
        kind=EPISODE_KIND,
        external_id=_external_id(f"{rel_folder}/{name}", data),
        occurred_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
        mapping_key=mapping_key,
        owner_text=body,
        metadata={"vault_folder": rel_folder},
        sensitivity=note_sensitivity(meta),
    )
    if result.status == STORED:
        stats.imported += 1
        if len(scrub_text(body).text) > Episode.MAX_TEXT_CHARS:
            stats.truncated += 1
    elif result.status == DUPLICATE:
        stats.duplicate += 1
    else:
        # Unmapped (counted by the port) or paused meanwhile: left in place.
        if result.status == DROPPED_UNMAPPED:
            stats.unmapped += 1
        else:
            stats.skipped += 1
        return
    # The episode is committed (record_episode's own transaction; the caller
    # holds none open). A failed move is logged and counted; the next run
    # sees a duplicate and moves it then.
    try:
        _move(imported, folder_fd, folder_path, name)
    except OSError:
        logger.exception("Moving an imported inbox note failed")
        stats.move_failed += 1
