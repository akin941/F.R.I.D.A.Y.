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
jarvis_export`, `workspace` frontmatter matches, `jarvis_id` parses as a
UUID, and that UUID is not in the current approved set. Any file failing
any single check is left untouched and counted as skipped, never deleted --
a human-authored note or a file this job doesn't fully recognize must never
be silently destroyed by a prune pass.

Lease-fenced claim/finalize mirrors `memory.jobs`/`knowledge.jobs` exactly.
`memory.Memory` is imported directly (tools -> memory is an allowed
one-way dependency, mirroring assistant -> memory); no `for_actor()` here --
like `memory.jobs.finalize_extraction_success`, this is system/worker code,
not a view or agent tool, so ADR-001's `for_actor()` rule doesn't apply.
"""

import errno
import logging
import uuid
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
from tools.vault_frontmatter import parse_frontmatter, render_frontmatter
from tools.vault_paths import (
    VaultPathError,
    VaultRootUnavailableError,
    atomic_write_validated,
    dir_fd_scope,
    ensure_validated_subdir,
    list_validated,
    open_validated_dir,
    read_validated,
    unlink_validated,
    validate_vault_root_exists,
    validate_vault_root_string,
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


@transaction.atomic
def finalize_vault_export_success(run_id, claim_token, counts):
    run = _fenced(run_id, claim_token)
    if run is None:
        return False
    written, updated, removed = counts
    run.status = VaultExportRun.Status.SUCCEEDED
    run.claim_token = None
    run.lease_expires_at = None
    run.error_type = ""
    run.error_message = ""
    run.written_count = written
    run.updated_count = updated
    run.removed_count = removed
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
        },
    )
    # work_log (ADR-008 decision 4, brief D13): counts only, like the audit
    # row -- never memory titles or file names.
    record_tool_outcome(
        run.approval_request,
        outcome="succeeded",
        summary=(
            f"Vault export run {run.id}: {written} written, {updated} updated, {removed} removed."
        ),
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


def export_workspace_memories(vault_root_real, workspace):
    """Tier 2 has already passed by the time this is called. For each tree
    (`_private/` first, then `03_Workspaces/`), opens the
    `<tree>/<workspace.id>/Memory/` chain as validated directory
    descriptors (each `mkdir`-if-missing then re-opened with `O_NOFOLLOW`),
    then runs the write + conservative-prune pass inside it against that
    tree's own memory set: private memories in `_private/`, all others in
    `03_Workspaces/`. The private tree is written before the main tree is
    pruned, so a memory moving between trees is never missing from the
    vault in between. Returns `(written, updated, removed)` summed over
    both trees.

    Serializes concurrent exports for the same workspace (Codex review
    finding) via a `select_for_update` lock on the workspace row, held for
    the whole write+prune pass -- not just a quick claim. Without it, two
    overlapping runs' approved-id snapshots can race: an older run's prune
    pass, still holding a snapshot from before a new approval, could delete
    a file a newer run just legitimately wrote for that same memory.
    """
    with transaction.atomic():
        Workspace.objects.select_for_update().get(pk=workspace.pk)
        approved = list(Memory.objects.filter(workspace=workspace, status=Memory.Status.APPROVED))
        private = [m for m in approved if m.sensitivity == Memory.Sensitivity.PRIVATE]
        shared = [m for m in approved if m.sensitivity != Memory.Sensitivity.PRIVATE]
        totals = (0, 0, 0)
        with dir_fd_scope(open_validated_dir(vault_root_real, vault_root_real)) as root_fd:
            for tree, memories in ((PRIVATE_TREE, private), (MAIN_TREE, shared)):
                counts = _export_tree(vault_root_real, root_fd, tree, workspace, memories)
                totals = tuple(total + count for total, count in zip(totals, counts, strict=True))
        return totals


def _export_tree(vault_root_real, root_fd, tree, workspace, memories):
    tree_path = f"{vault_root_real}/{tree}"
    with dir_fd_scope(
        ensure_validated_subdir(vault_root_real, root_fd, vault_root_real, tree)
    ) as tree_fd:
        ws_dir_path = f"{tree_path}/{workspace.id}"
        with dir_fd_scope(
            ensure_validated_subdir(vault_root_real, tree_fd, tree_path, str(workspace.id))
        ) as ws_fd:
            memory_dir_path = f"{ws_dir_path}/Memory"
            with dir_fd_scope(
                ensure_validated_subdir(vault_root_real, ws_fd, ws_dir_path, "Memory")
            ) as memory_fd:
                return _export_memory_dir(
                    vault_root_real, memory_fd, memory_dir_path, workspace, memories
                )


def _export_memory_dir(vault_root_real, memory_fd, memory_path, workspace, approved):
    """Write + conservative prune for one tree's Memory folder. `approved`
    is exactly the set this folder should mirror; a recognized export file
    for any other memory (deleted, archived, or belonging in the other
    tree) is prunable."""
    approved_ids = {memory.id for memory in approved}
    approved_filenames = {f"{memory.id}.md" for memory in approved}
    existing_files = set(list_validated(vault_root_real, memory_fd, memory_path))

    written = updated = removed = 0
    for memory in approved:
        filename = f"{memory.id}.md"
        atomic_write_validated(
            vault_root_real, memory_fd, memory_path, filename, render_frontmatter(memory)
        )
        if filename in existing_files:
            updated += 1
        else:
            written += 1

    for filename in existing_files:
        if filename in approved_filenames or not filename.endswith(".md"):
            continue
        try:
            text = read_validated(vault_root_real, memory_fd, memory_path, filename)
        except (OSError, UnicodeDecodeError):
            # Unreadable, or not valid UTF-8 (Codex review finding --
            # `.read()` raises UnicodeDecodeError, a ValueError subclass,
            # not OSError) -- skip, never destroy what we can't inspect.
            continue
        if _is_prunable(parse_frontmatter(text), workspace, approved_ids):
            unlink_validated(vault_root_real, memory_fd, memory_path, filename)
            removed += 1

    return written, updated, removed


def _is_prunable(fields, workspace, approved_ids) -> bool:
    if fields is None:
        return False
    if fields.get("source") != "jarvis_export":
        return False
    if fields.get("workspace") != str(workspace.id):
        return False
    try:
        parsed = uuid.UUID(fields.get("jarvis_id", ""))
    except (ValueError, AttributeError):
        return False
    return parsed not in approved_ids


def _classify_os_error(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return VaultExportRun.ErrorType.PERMISSION_DENIED
    if getattr(exc, "errno", None) == errno.ENOSPC:
        return VaultExportRun.ErrorType.DISK_FULL
    return VaultExportRun.ErrorType.UNKNOWN


def process_claimed_vault_export_run(run_id, claim_token):
    """The claimed run's actual work: tier-2 vault-root check, then the
    write + prune pass. Every failure mode maps to a sanitized, closed-
    vocabulary `error_type` -- the underlying exception's own text (which
    routinely embeds the real vault path and the host username) is logged,
    never persisted."""
    run = VaultExportRun.objects.select_related("workspace").get(pk=run_id)
    try:
        vault_root_real = validate_vault_root_exists(settings.OBSIDIAN_VAULT_PATH)
    except VaultRootUnavailableError:
        finalize_vault_export_failure(
            run_id, claim_token, error_type=VaultExportRun.ErrorType.PATH_VALIDATION_FAILED
        )
        return

    try:
        counts = export_workspace_memories(vault_root_real, run.workspace)
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

    finalize_vault_export_success(run_id, claim_token, counts)


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
    }


def _register():
    register_tool(
        ToolSpec(
            name="vault_export",
            gated=True,
            create_execution=create_vault_export_run,
            dispatch=dispatch_vault_export_run,
            validate_request=validate_vault_export_request,
            describe_execution=describe_vault_export_execution,
        )
    )
