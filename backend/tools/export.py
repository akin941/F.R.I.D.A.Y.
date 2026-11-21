"""Obsidian vault export -- the first real gated tool (MVP 5 branch 3,
brief §3). Scope: every `Memory` row `status=approved` in the run's
workspace, mirrored under `03_Workspaces/<workspace.id>/Memory/
<memory.id>.md` -- except `sensitivity=private` memories, which go to the
sync-ignored `_private/<workspace.id>/Memory/<memory.id>.md` tree instead
(Phase 4 branch 1; docs/memory/obsidian-vault-strategy.md). Each tree is
reconciled on its own, so a private memory's copy left in the main tree by
an earlier export is pruned by the next run.

Reconciliation is full-resync + conservative-prune, not an incremental
diff: every run rewrites every currently-approved memory's file (an edit
naturally reconciles -- the file is fully regenerated from the current row,
never patched), then scans the vault's `Memory/` folder and deletes a file
ONLY if it passes every one of: parses as valid frontmatter, `source:
friday_export` (or the pre-rename `jarvis_export`), `workspace` frontmatter
matches, `friday_id` (`jarvis_id`) parses as a UUID, and that UUID is not in
the current approved set. Any file failing any single check is left
untouched and counted as skipped, never deleted -- a human-authored note or
a file this job doesn't fully recognize must never be silently destroyed by
a prune pass.

Lease-fenced claim/finalize mirrors `memory.jobs`/`knowledge.jobs` exactly.
`memory.Memory` is imported directly (tools -> memory is an allowed
one-way dependency, mirroring assistant -> memory); no `for_actor()` here --
like `memory.jobs.finalize_extraction_success`, this is system/worker code,
not a view or agent tool, so ADR-001's `for_actor()` rule doesn't apply.
"""

import contextvars
import errno
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record_audit
from memory.models import Memory
from tools.models import VaultExportRun
from tools.registry import ToolSpec, ToolValidationError, register_tool
from tools.vault_entities import plan_entity_notes
from tools.vault_frontmatter import is_owner_edited, parse_frontmatter, render_frontmatter
from tools.vault_inbox import InboxStats, inbox_source_for, scan_inbox
from tools.vault_paths import (
    VaultPathError,
    VaultRootUnavailableError,
    atomic_write_validated,
    dir_fd_scope,
    ensure_validated_subdir,
    list_validated,
    open_existing_subdir,
    open_validated_dir,
    read_validated_bytes,
    unlink_validated,
    validate_vault_root_exists,
    validate_vault_root_string,
    write_new_validated,
)
from tools.work_log import record_tool_outcome
from workspaces.models import Workspace

logger = logging.getLogger(__name__)

# No gateway call in this task -- unlike memory/knowledge's workers, the
# budget only needs to cover local disk I/O over a workspace's approved
# memories, generous but not open-ended. Raise this if real vault sizes
# ever prove it too tight.
CLAIM_LEASE_SECONDS = 300
_SOFT_TIME_LIMIT = 120
_TIME_LIMIT = CLAIM_LEASE_SECONDS - 10

# A pending run still unclaimed after this long since its last update
# suggests its original on_commit dispatch was lost -- mirrors memory's/
# knowledge's identical recovery-grace constant.
PENDING_RECOVERY_GRACE_SECONDS = 60

# Every failure mode here (missing mount, permission, disk full, a detected
# symlink-swap race) could plausibly clear on a later attempt -- there is no
# vault-export equivalent of knowledge's deterministic "schema_invalid"
# case -- so, unlike memory/knowledge, every failure is retryable up to this
# ceiling rather than the caller choosing per error type.
MAX_EXPORT_RETRIES = 3

_ERROR_MESSAGES = {
    VaultExportRun.ErrorType.PATH_VALIDATION_FAILED: (
        "The configured vault path is not accessible."
    ),
    VaultExportRun.ErrorType.PERMISSION_DENIED: "Permission denied writing to the vault.",
    VaultExportRun.ErrorType.DISK_FULL: "The vault's filesystem is full.",
    VaultExportRun.ErrorType.UNKNOWN: "An unexpected filesystem error occurred.",
}


def validate_vault_export_request(workspace, payload):  # noqa: ARG001 - payload unused, always {}
    """`ToolSpec.validate_request` for `vault_export` -- tier 1 only (brief
    §3): zero-filesystem-access config validation, safe to run in the web
    container at request time. Wraps `VaultPathError` in
    `ToolValidationError` since `tools` must not import `approvals` (the
    exception type `approvals.services.create_request` actually catches)."""
    try:
        validate_vault_root_string(settings.OBSIDIAN_VAULT_PATH)
    except VaultPathError as exc:
        raise ToolValidationError(str(exc)) from exc


def create_vault_export_run(approval_request):
    """`ToolSpec.create_execution` -- called, and committed, inside the
    approval decision's own transaction (durable-before-dispatch, brief
    §2/§3)."""
    run = VaultExportRun.objects.create(
        workspace=approval_request.workspace, approval_request=approval_request
    )
    return str(run.id)


def dispatch_vault_export_run(run_id):
    """`ToolSpec.dispatch` -- publishes to the broker via
    `transaction.on_commit`. Mirrors `memory.jobs.dispatch_extraction_job`'s
    non-propagating shape exactly: a broker PUBLICATION failure must never
    propagate into the approval decision's own response."""
    from tools.tasks import run_vault_export

    if settings.CELERY_TASK_ALWAYS_EAGER:
        run_vault_export.delay(run_id)
        return
    try:
        run_vault_export.delay(run_id)
    except Exception:
        logger.exception("Failed to publish vault export run %s to the broker", run_id)


def claim_vault_export_run(run_id):
    """Mirrors `memory.jobs.claim_extraction_job` exactly: `pending`, or
    `running` with an expired lease (takeover). Returns `(run_id,
    claim_token)` on success, `None` if not claimable -- a safe no-op."""
    now = timezone.now()
    with transaction.atomic():
        run = (
            VaultExportRun.objects.select_for_update(skip_locked=True)
            .filter(
                Q(pk=run_id)
                & (
                    Q(status=VaultExportRun.Status.PENDING)
                    | Q(status=VaultExportRun.Status.RUNNING, lease_expires_at__lt=now)
                )
            )
            .first()
        )
        if run is None:
            return None
        token = uuid.uuid4()
        run.status = VaultExportRun.Status.RUNNING
        run.claim_token = token
        run.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        run.save(update_fields=["status", "claim_token", "lease_expires_at", "updated_at"])
    return run_id, token


def _fenced(run_id, claim_token):
    """Re-lock and verify this attempt still owns the run. Mirrors
    `memory.jobs._fenced`/`knowledge.jobs._fenced`."""
    return (
        VaultExportRun.objects.select_for_update()
        .filter(pk=run_id, status=VaultExportRun.Status.RUNNING, claim_token=claim_token)
        .first()
    )


@dataclass
class ExportStats:
    """Everything one run counts (Phase 8B branch 7). Counts only -- no
    file name or text ever lands here, so it can go to the run row, the
    audit metadata and the work_log notice as-is."""

    written: int = 0
    updated: int = 0
    removed: int = 0
    entity_notes: int = 0
    conflicts_saved: int = 0
    conflicts_private: int = 0
    inbox: InboxStats = field(default_factory=InboxStats)

    def as_tuple(self):
        return self.written, self.updated, self.removed


def _as_stats(counts) -> ExportStats:
    if isinstance(counts, ExportStats):
        return counts
    written, updated, removed = counts
    return ExportStats(written=written, updated=updated, removed=removed)


def conflict_notice(stats: ExportStats, day: str) -> str:
    """The D12 work_log notice (Turkish, the owner's UI language): counts
    and folder names only, never a note's name or text."""
    folders = []
    if stats.conflicts_saved - stats.conflicts_private:
        folders.append(f"`_conflicts/{day}/`")
    if stats.conflicts_private:
        folders.append(f"`_private/_conflicts/{day}/`")
    where = " ve ".join(folders)
    return (
        f"Obsidian'da düzenlenmiş {stats.conflicts_saved} not {where} klasörüne "
        "kaydedildi; değişikliği web arayüzünde yapın."
    )


@transaction.atomic
def finalize_vault_export_success(run_id, claim_token, counts):
    run = _fenced(run_id, claim_token)
    if run is None:
        return False
    stats = _as_stats(counts)
    written, updated, removed = stats.as_tuple()
    run.status = VaultExportRun.Status.SUCCEEDED
    run.claim_token = None
    run.lease_expires_at = None
    run.error_type = ""
    run.error_message = ""
    run.written_count = written
    run.updated_count = updated
    run.removed_count = removed
    run.conflicts_saved = stats.conflicts_saved
    run.entity_notes_count = stats.entity_notes
    run.inbox_imported_count = stats.inbox.imported
    run.inbox_unmapped_count = stats.inbox.unmapped
    run.inbox_skipped_count = stats.inbox.skipped
    run.save(
        update_fields=[
            "status",
            "claim_token",
            "lease_expires_at",
            "error_type",
            "error_message",
            "written_count",
            "updated_count",
            "removed_count",
            "conflicts_saved",
            "entity_notes_count",
            "inbox_imported_count",
            "inbox_unmapped_count",
            "inbox_skipped_count",
            "updated_at",
        ]
    )
    # vault_export naming per docs/memory/obsidian-vault-strategy.md.
    # Metadata carries counts and workspace id only -- never file contents.
    record_audit(
        action="vault_export",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=run.workspace,
        resource=run,
        metadata={
            "workspace": str(run.workspace_id),
            "written": written,
            "updated": updated,
            "removed": removed,
            "entity_notes": stats.entity_notes,
            "conflicts_saved": stats.conflicts_saved,
            "inbox_imported": stats.inbox.imported,
            "inbox_duplicate": stats.inbox.duplicate,
            "inbox_unmapped": stats.inbox.unmapped,
            "inbox_skipped": stats.inbox.skipped,
            "inbox_truncated": stats.inbox.truncated,
            "inbox_deferred": stats.inbox.deferred,
            "inbox_move_failed": stats.inbox.move_failed,
        },
    )
    # work_log (ADR-008 decision 4, brief D13): counts only, like the audit
    # row -- never memory titles or file names.
    summary = f"Vault export run {run.id}: {written} written, {updated} updated, {removed} removed."
    if stats.entity_notes:
        summary += f" {stats.entity_notes} entity notes."
    if stats.inbox.imported or stats.inbox.unmapped:
        summary += (
            f" Inbox: {stats.inbox.imported} imported, {stats.inbox.unmapped} unmapped"
            " (left in place)."
        )
    record_tool_outcome(
        run.approval_request,
        outcome="succeeded",
        summary=summary,
        sensitivity=Memory.Sensitivity.NORMAL,
    )
    if stats.conflicts_saved:
        # The D12 notice: its own work_log row, so the owner sees it as a
        # separate item rather than a clause inside the success line.
        record_tool_outcome(
            run.approval_request,
            outcome="conflicts_saved",
            summary=conflict_notice(stats, timezone.localdate().isoformat()),
            sensitivity=Memory.Sensitivity.NORMAL,
        )
    return True


@transaction.atomic
def finalize_vault_export_failure(run_id, claim_token, *, error_type):
    run = _fenced(run_id, claim_token)
    if run is None:
        return False
    run.retry_count += 1
    run.status = (
        VaultExportRun.Status.PENDING
        if run.retry_count < MAX_EXPORT_RETRIES
        else VaultExportRun.Status.FAILED
    )
    run.claim_token = None
    run.lease_expires_at = None
    run.error_type = error_type
    run.error_message = _ERROR_MESSAGES[error_type]
    run.save(
        update_fields=[
            "status",
            "retry_count",
            "claim_token",
            "lease_expires_at",
            "error_type",
            "error_message",
            "updated_at",
        ]
    )
    if run.status == VaultExportRun.Status.FAILED:
        record_audit(
            action="vault_export.failed",
            actor=None,
            actor_type=AuditLog.ActorType.SYSTEM,
            workspace=run.workspace,
            resource=run,
            metadata={"workspace": str(run.workspace_id), "error_type": error_type},
        )
        # Terminal failures only (a retryable one is not an outcome yet);
        # the closed-vocabulary error type, never the exception text.
        record_tool_outcome(
            run.approval_request,
            outcome="failed",
            summary=(
                f"Vault export run {run.id} failed after {run.retry_count} attempts: {error_type}."
            ),
            sensitivity=Memory.Sensitivity.NORMAL,
        )
    return True


# Top-level vault folders (docs/memory/obsidian-vault-strategy.md): the
# synced workspace tree, and the sync-ignored tree for private memories.
MAIN_TREE = "03_Workspaces"
PRIVATE_TREE = "_private"
# Owner-edited export files are copied here before being overwritten or
# pruned (D12). A private file's copy stays inside `_private/`.
CONFLICTS_DIR = "_conflicts"
MEMORY_FOLDER = "Memory"
ENTITY_FOLDER = "Entities"
_MAX_UNIQUE_SUFFIX = 100


# --- the approved-run gate (D12) ---------------------------------------------


class VaultRunNotClaimed(Exception):
    """A vault write or an inbox import was attempted outside an approved,
    claimed `vault_export` run (CLAUDE.md rule 4): refused."""


_CLAIMED_RUN: contextvars.ContextVar = contextvars.ContextVar("vault_export_claimed_run")


@contextmanager
def claimed_run_scope(run_id, claim_token):
    """Verify this attempt owns `run_id` -- `running`, holding
    `claim_token`, for an APPROVED `vault_export` request -- and mark the
    current context as inside that run. Every vault write
    (`export_workspace_memories`) and the inbox import (`import_inbox`)
    refuses to run without it, so a direct call can never write the vault
    or create episodes."""
    run = (
        VaultExportRun.objects.select_related("workspace", "approval_request")
        .filter(
            pk=run_id,
            status=VaultExportRun.Status.RUNNING,
            claim_token=claim_token,
            approval_request__status="approved",
            approval_request__tool_name="vault_export",
        )
        .first()
    )
    if run is None or claim_token is None:
        raise VaultRunNotClaimed("No approved, claimed vault_export run.")
    token = _CLAIMED_RUN.set(run.workspace_id)
    try:
        yield run
    finally:
        _CLAIMED_RUN.reset(token)


def _require_claimed_run(workspace):
    if _CLAIMED_RUN.get(None) != workspace.pk:
        raise VaultRunNotClaimed("Vault writes happen only inside an approved, claimed run.")


# --- planning: what each folder should mirror ---------------------------------


def plan_workspace(workspace):
    """`{tree: {folder: {filename: text}}}` -- exactly what this run will
    write: approved memories (private ones in `_private/`) and entity notes
    (`tools.vault_entities`). A pure DB read, shared by the run and the
    approval's dry pass."""
    approved = list(Memory.objects.filter(workspace=workspace, status=Memory.Status.APPROVED))
    private_memories = {
        f"{m.id}.md": render_frontmatter(m)
        for m in approved
        if m.sensitivity == Memory.Sensitivity.PRIVATE
    }
    shared_memories = {
        f"{m.id}.md": render_frontmatter(m)
        for m in approved
        if m.sensitivity != Memory.Sensitivity.PRIVATE
    }
    open_entities, private_entities = plan_entity_notes(workspace)
    # `_private/` first: a note moving between trees is written to its new
    # tree before the old tree's copy is pruned.
    return {
        PRIVATE_TREE: {MEMORY_FOLDER: private_memories, ENTITY_FOLDER: private_entities},
        MAIN_TREE: {MEMORY_FOLDER: shared_memories, ENTITY_FOLDER: open_entities},
    }


# --- conflicts (D12) ------------------------------------------------------------


class _ConflictSink:
    """Writes conflict copies for one tier: the open tree's go to
    `_conflicts/<date>/<ws>/`, a `_private/` file's to
    `_private/_conflicts/<date>/<ws>/`. The tier is the tree of the path
    being overwritten, never the edited frontmatter. Directories are
    created lazily (validated descriptors, `O_NOFOLLOW` -- a symlinked
    `_conflicts` raises `VaultPathEscapeError`, failing the run BEFORE the
    edited file is touched). `dry_run` only counts."""

    def __init__(self, vault_root_real, root_fd, tree, workspace, stats, *, dry_run):
        self.vault_root_real = vault_root_real
        self.root_fd = root_fd
        self.private = tree == PRIVATE_TREE
        self.workspace = workspace
        self.stats = stats
        self.dry_run = dry_run
        self.day = timezone.localdate().isoformat()
        self._fds = []
        self._fd = None
        self._path = None

    def _ensure(self):
        if self._fd is not None:
            return self._fd, self._path
        parent_fd, parent_path = self.root_fd, self.vault_root_real
        chain = [CONFLICTS_DIR, self.day, str(self.workspace.id)]
        if self.private:
            chain = [PRIVATE_TREE, *chain]
        for name in chain:
            fd = ensure_validated_subdir(self.vault_root_real, parent_fd, parent_path, name)
            self._fds.append(fd)
            parent_fd, parent_path = fd, f"{parent_path}/{name}"
        self._fd, self._path = parent_fd, parent_path
        return self._fd, self._path

    def save(self, filename, data: bytes):
        self.stats.conflicts_saved += 1
        if self.private:
            self.stats.conflicts_private += 1
        if self.dry_run:
            return
        fd, path = self._ensure()
        stem = filename[:-3] if filename.endswith(".md") else filename
        for attempt in range(_MAX_UNIQUE_SUFFIX):
            name = filename if attempt == 0 else f"{stem}.{attempt}.md"
            try:
                write_new_validated(self.vault_root_real, fd, path, name, data)
                return
            except FileExistsError:
                continue
        raise FileExistsError("no free conflict-copy name")

    def close(self):
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds = []


def _read_existing(vault_root_real, folder_fd, folder_path, filename):
    """`(raw bytes, text or None)` of an existing file, or `None` when it
    cannot be inspected (a symlink, a FIFO, unreadable): such a file is
    never copied -- a rename replaces a symlink itself, never its target,
    so nothing of the owner's is destroyed by overwriting it."""
    try:
        data, _ = read_validated_bytes(vault_root_real, folder_fd, folder_path, filename)
    except OSError:
        return None
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError:
        return data, None


def _edited(existing) -> bool:
    data, text = existing
    # Undecodable bytes at an export path are not ours: keep a copy.
    return text is None or is_owner_edited(text)


# --- the write + conservative prune pass -----------------------------------------


def export_workspace_memories(vault_root_real, workspace, stats=None):
    """Tier 2 has already passed by the time this is called, inside a
    claimed run (`claimed_run_scope`; refused otherwise). For each tree
    (`_private/` first, then `03_Workspaces/`) and each of its folders
    (`Memory/`, `Entities/`), opens the `<tree>/<workspace.id>/<folder>/`
    chain as validated directory descriptors (each `mkdir`-if-missing then
    re-opened with `O_NOFOLLOW`), then runs the write + conservative-prune
    pass inside it against that folder's own planned set. Before an
    existing file is overwritten or pruned, an owner edit (its body no
    longer matching its `content_sha256`) is copied to the tier's
    `_conflicts/<date>/<ws>/`. Returns `(written, updated, removed)` summed
    over every folder; `stats` (an `ExportStats`), when given, also
    receives the entity-note and conflict counts.

    Serializes concurrent exports for the same workspace (Codex review
    finding) via a `select_for_update` lock on the workspace row, held for
    the whole write+prune pass -- not just a quick claim. Without it, two
    overlapping runs' approved-id snapshots can race: an older run's prune
    pass, still holding a snapshot from before a new approval, could delete
    a file a newer run just legitimately wrote for that same memory.
    """
    _require_claimed_run(workspace)
    stats = stats if stats is not None else ExportStats()
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=workspace.pk)
        plan = plan_workspace(workspace)
        stats.entity_notes = len(plan[PRIVATE_TREE][ENTITY_FOLDER]) + len(
            plan[MAIN_TREE][ENTITY_FOLDER]
        )
        with dir_fd_scope(open_validated_dir(vault_root_real, vault_root_real)) as root_fd:
            for tree, folders in plan.items():
                _export_tree(vault_root_real, root_fd, tree, workspace, folders, stats)
        return stats.as_tuple()


def _export_tree(vault_root_real, root_fd, tree, workspace, folders, stats, *, dry_run=False):
    sink = _ConflictSink(vault_root_real, root_fd, tree, workspace, stats, dry_run=dry_run)
    opener = open_existing_subdir if dry_run else ensure_validated_subdir
    tree_path = f"{vault_root_real}/{tree}"
    ws_path = f"{tree_path}/{workspace.id}"
    try:
        tree_fd = opener(vault_root_real, root_fd, vault_root_real, tree)
        if tree_fd is None:
            return
        with dir_fd_scope(tree_fd):
            ws_fd = opener(vault_root_real, tree_fd, tree_path, str(workspace.id))
            if ws_fd is None:
                return
            with dir_fd_scope(ws_fd):
                for folder, files in folders.items():
                    folder_fd = opener(vault_root_real, ws_fd, ws_path, folder)
                    if folder_fd is None:
                        continue
                    with dir_fd_scope(folder_fd):
                        _export_folder(
                            vault_root_real,
                            folder_fd,
                            f"{ws_path}/{folder}",
                            workspace,
                            files,
                            stats,
                            sink,
                            dry_run=dry_run,
                        )
    finally:
        sink.close()


def _export_folder(
    vault_root_real, folder_fd, folder_path, workspace, files, stats, sink, *, dry_run=False
):
    """Write + conservative prune for one folder. `files` is exactly the
    set this folder should mirror; a recognized export file for any other
    id (deleted, archived, trashed, or belonging in the other tree) is
    prunable."""
    wanted_ids = {uuid.UUID(name[:-3]) for name in files}
    existing_files = set(list_validated(vault_root_real, folder_fd, folder_path))

    for filename, content in files.items():
        if filename in existing_files:
            existing = _read_existing(vault_root_real, folder_fd, folder_path, filename)
            if existing is not None and _edited(existing):
                sink.save(filename, existing[0])
            stats.updated += 1
        else:
            stats.written += 1
        if not dry_run:
            atomic_write_validated(vault_root_real, folder_fd, folder_path, filename, content)

    for filename in existing_files:
        if filename in files or not filename.endswith(".md"):
            continue
        existing = _read_existing(vault_root_real, folder_fd, folder_path, filename)
        if existing is None or existing[1] is None:
            # Unreadable, or not valid UTF-8 (Codex review finding) -- skip,
            # never destroy what we can't inspect.
            continue
        if _is_prunable(parse_frontmatter(existing[1]), workspace, wanted_ids):
            if is_owner_edited(existing[1]):
                sink.save(filename, existing[0])
            if not dry_run:
                unlink_validated(vault_root_real, folder_fd, folder_path, filename)
            stats.removed += 1


# Export-owned `source` tags -> the frontmatter key holding the memory id.
# Files exported before the Jarvis -> Friday rename (2026-11-20) carry
# `source: jarvis_export` / `jarvis_id`; they are just as export-owned, so
# the prune treats both alike (else they would be orphaned forever). A
# still-approved memory's legacy file is rewritten in the new format by the
# same run (same file name).
EXPORT_SOURCE_ID_KEYS = {"friday_export": "friday_id", "jarvis_export": "jarvis_id"}


def _is_prunable(fields, workspace, approved_ids) -> bool:
    if fields is None:
        return False
    id_key = EXPORT_SOURCE_ID_KEYS.get(fields.get("source"))
    if id_key is None:
        return False
    if fields.get("workspace") != str(workspace.id):
        return False
    try:
        parsed = uuid.UUID(fields.get(id_key, ""))
    except (ValueError, AttributeError):
        return False
    return parsed not in approved_ids


def import_inbox(vault_root_real, workspace, stats=None):
    """The `00_Inbox/` import-once (`tools.vault_inbox`), inside a claimed
    run only. Runs BEFORE the export's workspace-lock transaction, with no
    transaction of its own open: each episode commits in
    `record_episode`'s own transaction before its note is moved, so a later
    failure of the export pass can never roll back an episode whose note
    has already been moved to `_imported/`."""
    _require_claimed_run(workspace)
    stats = stats if stats is not None else ExportStats()
    with dir_fd_scope(open_validated_dir(vault_root_real, vault_root_real)) as root_fd:
        stats.inbox = scan_inbox(vault_root_real, root_fd, workspace)
    return stats.inbox


def dry_pass(workspace):
    """The approval's read-only preview (D12): what a run would do now --
    conflicts it would save and inbox notes waiting -- or `None` when the
    vault is not reachable from this process (the web container never has
    it mounted). Writes nothing, creates no directory, records nothing."""
    try:
        vault_root_real = validate_vault_root_exists(settings.OBSIDIAN_VAULT_PATH)
    except VaultPathError:
        return None
    stats = ExportStats()
    try:
        plan = plan_workspace(workspace)
        with dir_fd_scope(open_validated_dir(vault_root_real, vault_root_real)) as root_fd:
            for tree, folders in plan.items():
                _export_tree(
                    vault_root_real, root_fd, tree, workspace, folders, stats, dry_run=True
                )
            stats.inbox = scan_inbox(vault_root_real, root_fd, workspace, dry_run=True)
    except (VaultPathError, OSError):
        logger.exception("Vault dry pass failed")
        return None
    return stats


def _classify_os_error(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return VaultExportRun.ErrorType.PERMISSION_DENIED
    if getattr(exc, "errno", None) == errno.ENOSPC:
        return VaultExportRun.ErrorType.DISK_FULL
    return VaultExportRun.ErrorType.UNKNOWN


def process_claimed_vault_export_run(run_id, claim_token):
    """The claimed run's actual work: tier-2 vault-root check, then (inside
    `claimed_run_scope`) the `00_Inbox/` import and the write + prune pass.
    Every failure mode maps to a sanitized, closed-vocabulary `error_type`
    -- the underlying exception's own text (which routinely embeds the real
    vault path and the host username) is logged, never persisted."""
    run = VaultExportRun.objects.select_related("workspace").get(pk=run_id)
    try:
        vault_root_real = validate_vault_root_exists(settings.OBSIDIAN_VAULT_PATH)
    except VaultRootUnavailableError:
        finalize_vault_export_failure(
            run_id, claim_token, error_type=VaultExportRun.ErrorType.PATH_VALIDATION_FAILED
        )
        return

    stats = ExportStats()
    try:
        with claimed_run_scope(run_id, claim_token):
            import_inbox(vault_root_real, run.workspace, stats)
            export_workspace_memories(vault_root_real, run.workspace, stats)
    except VaultRunNotClaimed:
        # Lost the claim (lease takeover) or the approval is gone: no-op,
        # like a fenced finalize that finds nothing to finalize.
        logger.warning("Vault export run %s is no longer claimed by this attempt", run_id)
        return
    except VaultPathError:
        logger.exception("Vault path escape detected during export run %s", run_id)
        finalize_vault_export_failure(
            run_id, claim_token, error_type=VaultExportRun.ErrorType.PATH_VALIDATION_FAILED
        )
        return
    except OSError as exc:
        logger.exception("Filesystem error during vault export run %s", run_id)
        finalize_vault_export_failure(run_id, claim_token, error_type=_classify_os_error(exc))
        return

    finalize_vault_export_success(run_id, claim_token, stats)


def describe_vault_export_execution(approval_request):
    """`ToolSpec.describe_execution` (brief §4): a JSON-safe run-status
    summary for the UI, or `None` if no `VaultExportRun` exists yet (the
    approval hasn't been decided, or `create_execution` hasn't run yet --
    a transient window `dispatch` closes almost immediately). Only the
    already-sanitized fields (brief §3 -- `error_message` never carries
    the underlying exception's own text or any path) are exposed.

    Reads the reverse `vault_export_run` accessor rather than issuing its
    own query: on the unpaginated approvals list, `approvals.views`
    `select_related("vault_export_run")`s the queryset up front (Codex
    review, branch 4) precisely so this is a cache hit, not a fresh N+1
    query per row."""
    run = getattr(approval_request, "vault_export_run", None)
    if run is None:
        return None
    return {
        "status": run.status,
        "error_type": run.error_type,
        "error_message": run.error_message,
        "written_count": run.written_count,
        "updated_count": run.updated_count,
        "removed_count": run.removed_count,
        "retry_count": run.retry_count,
        # Phase 8B branch 7 (D12): the conflict count on the approval detail.
        "conflicts_saved": run.conflicts_saved,
        "entity_notes_count": run.entity_notes_count,
        "inbox_imported_count": run.inbox_imported_count,
        "inbox_unmapped_count": run.inbox_unmapped_count,
        "inbox_skipped_count": run.inbox_skipped_count,
    }


# Shown to the decider with the counts (D12): the inbox import is not
# personal-class, so the owner must flag sensitive notes themselves.
INBOX_ROUTING_NOTE = (
    "00_Inbox notları bulut sağlayıcılarla da işlenebilir; hassas bir notu "
    "`sensitivity: private` ile işaretleyin (yalnızca yerel modeller)."
)


def describe_vault_export_request(approval_request):
    """`ToolSpec.describe_request` (D12): what the decider is approving, as
    counts only -- the memories and entity notes the run will write, and,
    while the request is still pending and the vault is reachable from
    this process, a read-only dry pass: conflicts it would save and inbox
    notes waiting. `None` counts mean "not known here" (the web container
    has no vault mount). Never a file name, a title or any note text."""
    workspace = approval_request.workspace
    approved = Memory.objects.filter(workspace=workspace, status=Memory.Status.APPROVED)
    private_memories = approved.filter(sensitivity=Memory.Sensitivity.PRIVATE).count()
    open_entities, private_entities = plan_entity_notes(workspace)
    source = inbox_source_for(workspace)
    inbox_enabled = bool(source and source.is_accepting)
    summary = {
        "kind": "vault_export",
        "memories": approved.count() - private_memories,
        "private_memories": private_memories,
        "entity_notes": len(open_entities),
        "private_entity_notes": len(private_entities),
        "inbox_enabled": inbox_enabled,
        "inbox_waiting": None,
        "inbox_unmapped": None,
        "conflicts_detected": None,
        "dry_pass": "skipped",
        "inbox_routing_note": INBOX_ROUTING_NOTE if inbox_enabled else "",
    }
    if approval_request.status != "pending":
        return summary
    stats = dry_pass(workspace)
    if stats is None:
        summary["dry_pass"] = "vault_unavailable"
        return summary
    summary.update(
        dry_pass="ok",
        conflicts_detected=stats.conflicts_saved,
        inbox_waiting=stats.inbox.waiting,
        inbox_unmapped=stats.inbox.unmapped,
    )
    return summary


def _register():
    register_tool(
        ToolSpec(
            name="vault_export",
            gated=True,
            create_execution=create_vault_export_run,
            dispatch=dispatch_vault_export_run,
            validate_request=validate_vault_export_request,
            describe_execution=describe_vault_export_execution,
            describe_request=describe_vault_export_request,
            describe_request_per_workspace=True,
        )
    )
