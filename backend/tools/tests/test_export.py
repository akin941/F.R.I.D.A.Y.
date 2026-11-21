import datetime
import threading
import time
import uuid
from unittest import mock

import pytest
from django.db import connection, transaction

from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, approve_request, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.models import Memory
from tools import export
from tools.models import VaultExportRun
from tools.tests.vault_helpers import claimed_run, export_in_run
from tools.vault_paths import VaultPathEscapeError
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="vault-owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_memory(workspace, **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.SEMANTIC,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.USER_CREATED,
        "title": "A memory",
        "content": "Some content.",
        "fingerprint": uuid.uuid4().hex,
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


def make_run(workspace, requested_by):
    """`VaultExportRun.approval_request` is a required FK -- every run in
    this test suite is tied to a real (already-approved, for test purposes)
    `ApprovalRequest`, mirroring what `create_vault_export_run` actually
    receives in production."""
    request = ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name="vault_export",
        status=ApprovalRequest.Status.APPROVED,
        requested_by=requested_by,
    )
    run_id = export.create_vault_export_run(request)
    return VaultExportRun.objects.get(id=run_id)


def memory_path(vault_root, workspace, memory):
    return vault_root / "03_Workspaces" / str(workspace.id) / "Memory" / f"{memory.id}.md"


class TestDescribeExecution:
    """brief §4: the UI's run-status indicator reads this, not the raw
    `VaultExportRun` row -- only sanitized fields are ever exposed."""

    def test_none_when_no_run_exists_yet(self, workspace, owner):
        request = ApprovalRequest.objects.create(
            workspace=workspace,
            tool_name="vault_export",
            status=ApprovalRequest.Status.PENDING,
            requested_by=owner,
        )
        assert export.describe_vault_export_execution(request) is None

    def test_reflects_a_pending_runs_status(self, workspace, owner):
        run = make_run(workspace, owner)
        summary = export.describe_vault_export_execution(run.approval_request)
        assert summary == {
            "status": VaultExportRun.Status.PENDING,
            "error_type": "",
            "error_message": "",
            "written_count": 0,
            "updated_count": 0,
            "removed_count": 0,
            "retry_count": 0,
            "conflicts_saved": 0,
            "conflicts_private": 0,
            "conflicts_folders": [],
            "entity_notes_count": 0,
            "inbox_imported_count": 0,
            "inbox_unmapped_count": 0,
            "inbox_skipped_count": 0,
        }

    def test_names_the_conflict_folders_of_a_run_with_conflicts(self, workspace, owner):
        run = make_run(workspace, owner)
        run.status = VaultExportRun.Status.SUCCEEDED
        run.conflicts_saved = 3
        run.conflicts_private = 1
        run.conflicts_day = datetime.date(2026, 11, 20)
        run.save()
        fresh_request = ApprovalRequest.objects.get(pk=run.approval_request_id)
        summary = export.describe_vault_export_execution(fresh_request)
        assert summary["conflicts_saved"] == 3
        assert summary["conflicts_private"] == 1
        assert summary["conflicts_folders"] == [
            "_conflicts/2026-11-20/",
            "_private/_conflicts/2026-11-20/",
        ]

    def test_only_private_conflicts_name_only_the_private_folder(self, workspace, owner):
        run = make_run(workspace, owner)
        run.status = VaultExportRun.Status.SUCCEEDED
        run.conflicts_saved = 1
        run.conflicts_private = 1
        run.conflicts_day = datetime.date(2026, 11, 20)
        run.save()
        fresh_request = ApprovalRequest.objects.get(pk=run.approval_request_id)
        summary = export.describe_vault_export_execution(fresh_request)
        assert summary["conflicts_folders"] == ["_private/_conflicts/2026-11-20/"]

    def test_reflects_a_succeeded_runs_counts(self, workspace, owner, tmp_path, settings):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        make_memory(workspace)
        run = make_run(workspace, owner)
        claimed = export.claim_vault_export_run(str(run.id))
        export.process_claimed_vault_export_run(*claimed)
        # Re-fetch: a fresh instance, exactly like the view does per
        # request, rather than the `run.approval_request` object whose
        # `vault_export_run` reverse cache was populated back at creation
        # time (still "pending") by Django's automatic bidirectional
        # OneToOneField caching.
        fresh_request = ApprovalRequest.objects.get(pk=run.approval_request_id)
        summary = export.describe_vault_export_execution(fresh_request)
        assert summary["status"] == VaultExportRun.Status.SUCCEEDED
        assert summary["written_count"] == 1

    def test_sanitized_error_message_never_leaks_the_real_path(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        make_memory(workspace)
        run = make_run(workspace, owner)
        claimed = export.claim_vault_export_run(str(run.id))
        secret_path = "/Users/definitely-secret-username/vault"
        with mock.patch(
            "tools.export.export_workspace_memories",
            side_effect=PermissionError(f"Permission denied: {secret_path}"),
        ):
            export.process_claimed_vault_export_run(*claimed)
        fresh_request = ApprovalRequest.objects.get(pk=run.approval_request_id)
        summary = export.describe_vault_export_execution(fresh_request)
        assert secret_path not in summary["error_message"]
        assert summary["error_type"] == VaultExportRun.ErrorType.PERMISSION_DENIED


class TestReconciliation:
    def test_approved_memory_gets_a_file(self, workspace, tmp_path):
        memory = make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert (written, updated, removed) == (1, 0, 0)
        assert memory_path(tmp_path, workspace, memory).exists()

    def test_second_run_counts_as_updated_not_written(self, workspace, tmp_path):
        make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert (written, updated, removed) == (0, 1, 0)

    def test_editing_content_regenerates_the_file(self, workspace, tmp_path):
        memory = make_memory(workspace, content="Original content.")
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        memory.content = "Edited content."
        memory.save(update_fields=["content"])
        export_in_run(vault_root_real, workspace)
        text = memory_path(tmp_path, workspace, memory).read_text()
        assert "Edited content." in text
        assert "Original content." not in text

    def test_forgetting_a_memory_removes_its_file(self, workspace, tmp_path):
        memory = make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        memory.status = Memory.Status.ARCHIVED
        memory.save(update_fields=["status"])
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert removed == 1
        assert not memory_path(tmp_path, workspace, memory).exists()

    def test_hard_deleting_a_memory_removes_its_file(self, workspace, tmp_path):
        memory = make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        path = memory_path(tmp_path, workspace, memory)
        memory.delete()
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert removed == 1
        assert not path.exists()

    def test_pending_and_rejected_memories_are_never_exported(self, workspace, tmp_path):
        make_memory(workspace, status=Memory.Status.PENDING_APPROVAL, fingerprint="p")
        make_memory(workspace, status=Memory.Status.REJECTED, fingerprint="r")
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert (written, updated, removed) == (0, 0, 0)

    def test_path_like_characters_in_title_never_affect_file_location(self, workspace, tmp_path):
        memory = make_memory(workspace, title="../../etc/passwd", content="../not/a/path")
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        # The file lives exactly where memory.id dictates -- nowhere else.
        assert memory_path(tmp_path, workspace, memory).exists()
        all_md_files = list(tmp_path.rglob("*.md"))
        assert all_md_files == [memory_path(tmp_path, workspace, memory)]


def private_memory_path(vault_root, workspace, memory):
    return vault_root / "_private" / str(workspace.id) / "Memory" / f"{memory.id}.md"


class TestPrivateMemories:
    """Phase 4 branch 1: `private` memories go to the sync-ignored
    `_private/` tree (docs/memory/obsidian-vault-strategy.md), never the
    synced `03_Workspaces/` tree."""

    def test_private_memory_lands_only_under_private(self, workspace, tmp_path):
        private = make_memory(workspace, sensitivity=Memory.Sensitivity.PRIVATE, fingerprint="p")
        normal = make_memory(workspace, fingerprint="n")
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        written, updated, removed = export_in_run(vault_root_real, workspace)

        assert (written, updated, removed) == (2, 0, 0)
        assert private_memory_path(tmp_path, workspace, private).exists()
        assert not memory_path(tmp_path, workspace, private).exists()
        assert memory_path(tmp_path, workspace, normal).exists()
        assert not private_memory_path(tmp_path, workspace, normal).exists()
        # Nothing private anywhere in the synced tree.
        synced = tmp_path / "03_Workspaces"
        assert all(str(private.id) not in p.read_text() for p in synced.rglob("*.md"))

    def test_old_main_tree_copy_of_a_private_memory_is_pruned(self, workspace, tmp_path):
        """A private memory exported before this change left a copy in the
        main tree; the next run moves it."""
        memory = make_memory(workspace, sensitivity=Memory.Sensitivity.PRIVATE)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        # Recreate the pre-change layout: the file in the main tree.
        legacy = memory_path(tmp_path, workspace, memory)
        legacy.write_text(private_memory_path(tmp_path, workspace, memory).read_text())

        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert (written, updated, removed) == (0, 1, 1)
        assert not legacy.exists()
        assert private_memory_path(tmp_path, workspace, memory).exists()

    def test_memory_moving_between_trees_follows_its_sensitivity(self, workspace, tmp_path):
        memory = make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        memory.sensitivity = Memory.Sensitivity.PRIVATE
        memory.save(update_fields=["sensitivity"])
        export_in_run(vault_root_real, workspace)
        assert private_memory_path(tmp_path, workspace, memory).exists()
        assert not memory_path(tmp_path, workspace, memory).exists()

        memory.sensitivity = Memory.Sensitivity.NORMAL
        memory.save(update_fields=["sensitivity"])
        export_in_run(vault_root_real, workspace)
        assert memory_path(tmp_path, workspace, memory).exists()
        assert not private_memory_path(tmp_path, workspace, memory).exists()

    def test_human_note_in_the_private_tree_is_never_pruned(self, workspace, tmp_path):
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)
        note = tmp_path / "_private" / str(workspace.id) / "Memory" / "diary.md"
        note.write_text("my own words")
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert removed == 0
        assert note.read_text() == "my own words"


class TestConservativePrune:
    def _memory_dir(self, workspace, tmp_path):
        return tmp_path / "03_Workspaces" / str(workspace.id) / "Memory"

    def _plant(self, workspace, tmp_path, filename, text):
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)  # creates the dir chain
        directory = self._memory_dir(workspace, tmp_path)
        (directory / filename).write_text(text)
        return vault_root_real

    def test_malformed_frontmatter_is_skipped_not_deleted(self, workspace, tmp_path):
        vault_root_real = self._plant(workspace, tmp_path, "stray.md", "not frontmatter at all")
        export_in_run(vault_root_real, workspace)
        assert (self._memory_dir(workspace, tmp_path) / "stray.md").exists()

    def test_wrong_source_is_skipped_not_deleted(self, workspace, tmp_path):
        text = (
            "---\nfriday_id: "
            + str(uuid.uuid4())
            + "\nworkspace: "
            + str(workspace.id)
            + "\nsource: human\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, "human-note.md", text)
        export_in_run(vault_root_real, workspace)
        assert (self._memory_dir(workspace, tmp_path) / "human-note.md").exists()

    def test_mismatched_workspace_is_skipped_not_deleted(self, workspace, tmp_path):
        other_workspace_id = uuid.uuid4()
        text = (
            "---\nfriday_id: "
            + str(uuid.uuid4())
            + "\nworkspace: "
            + str(other_workspace_id)
            + "\nsource: friday_export\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, "foreign.md", text)
        export_in_run(vault_root_real, workspace)
        assert (self._memory_dir(workspace, tmp_path) / "foreign.md").exists()

    def test_non_uuid_friday_id_is_skipped_not_deleted(self, workspace, tmp_path):
        text = (
            "---\nfriday_id: not-a-uuid\nworkspace: "
            + str(workspace.id)
            + "\nsource: friday_export\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, "bad-id.md", text)
        export_in_run(vault_root_real, workspace)
        assert (self._memory_dir(workspace, tmp_path) / "bad-id.md").exists()

    def test_valid_own_export_no_longer_approved_is_pruned(self, workspace, tmp_path):
        stale_id = uuid.uuid4()
        text = (
            "---\nfriday_id: "
            + str(stale_id)
            + "\nworkspace: "
            + str(workspace.id)
            + "\nsource: friday_export\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, f"{stale_id}.md", text)
        written, updated, removed = export_in_run(vault_root_real, workspace)
        assert removed == 1
        assert not (self._memory_dir(workspace, tmp_path) / f"{stale_id}.md").exists()

    def test_pre_rename_jarvis_export_no_longer_approved_is_pruned(self, workspace, tmp_path):
        """Jarvis -> Friday (2026-11-20): files exported before the rename
        (`source: jarvis_export`, `jarvis_id`) are still export-owned."""
        stale_id = uuid.uuid4()
        text = (
            "---\njarvis_id: "
            + str(stale_id)
            + "\nworkspace: "
            + str(workspace.id)
            + "\nsource: jarvis_export\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, f"{stale_id}.md", text)
        _, _, removed = export_in_run(vault_root_real, workspace)
        assert removed == 1
        assert not (self._memory_dir(workspace, tmp_path) / f"{stale_id}.md").exists()

    def test_pre_rename_tag_with_the_new_id_key_is_skipped_not_deleted(self, workspace, tmp_path):
        """Each source tag is paired with its own id key; a mixed file is not
        something this job wrote, so it is left alone."""
        text = (
            "---\nfriday_id: "
            + str(uuid.uuid4())
            + "\nworkspace: "
            + str(workspace.id)
            + "\nsource: jarvis_export\n---\nbody\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, "mixed.md", text)
        export_in_run(vault_root_real, workspace)
        assert (self._memory_dir(workspace, tmp_path) / "mixed.md").exists()

    def test_pre_rename_export_of_an_approved_memory_is_rewritten(self, workspace, tmp_path):
        memory = make_memory(workspace)
        legacy = (
            "---\njarvis_id: "
            + str(memory.id)
            + "\nworkspace: "
            + str(workspace.id)
            + "\nsource: jarvis_export\n---\nold body\n"
        )
        vault_root_real = self._plant(workspace, tmp_path, f"{memory.id}.md", legacy)
        export_in_run(vault_root_real, workspace)
        rewritten = (self._memory_dir(workspace, tmp_path) / f"{memory.id}.md").read_text()
        assert "source: friday_export" in rewritten
        assert f"friday_id: {memory.id}" in rewritten

    def test_non_utf8_file_is_skipped_not_fatal(self, workspace, tmp_path):
        """Codex review finding: `.read()` on a non-UTF-8 file raises
        `UnicodeDecodeError` (a `ValueError` subclass, not `OSError`) -- the
        prune loop must catch that too, or the whole export fails/retries
        instead of just skipping the one unrecognized file."""
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)  # creates the dir chain
        bad_file = self._memory_dir(workspace, tmp_path) / "not-utf8.md"
        bad_file.write_bytes(b"\xff\xfe\x00\xff not valid utf-8")

        written, updated, removed = export_in_run(vault_root_real, workspace)

        assert removed == 0
        assert bad_file.exists()


class TestSanitizedErrors:
    def test_permission_error_message_never_leaks_the_real_path(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)  # tier 2 must pass first
        make_memory(workspace)
        run = make_run(workspace, owner)
        claimed = export.claim_vault_export_run(str(run.id))
        secret_path = "/Users/definitely-secret-username/vault"
        with mock.patch(
            "tools.export.export_workspace_memories",
            side_effect=PermissionError(f"Permission denied: {secret_path}"),
        ):
            export.process_claimed_vault_export_run(*claimed)
        run.refresh_from_db()
        assert run.error_type == VaultExportRun.ErrorType.PERMISSION_DENIED
        assert secret_path not in run.error_message
        assert (
            run.error_message == export._ERROR_MESSAGES[VaultExportRun.ErrorType.PERMISSION_DENIED]
        )
        for entry in AuditLog.objects.all():
            assert secret_path not in str(entry.metadata)

    def test_path_escape_during_run_maps_to_path_validation_failed(
        self, workspace, owner, tmp_path, settings
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)  # tier 2 must pass first
        make_memory(workspace)
        run = make_run(workspace, owner)
        claimed = export.claim_vault_export_run(str(run.id))
        with mock.patch(
            "tools.export.export_workspace_memories",
            side_effect=VaultPathEscapeError("/some/real/vault/path escaped"),
        ) as mocked:
            export.process_claimed_vault_export_run(*claimed)
        mocked.assert_called_once()
        run.refresh_from_db()
        assert run.error_type == VaultExportRun.ErrorType.PATH_VALIDATION_FAILED
        assert "/some/real/vault/path" not in run.error_message

    def test_nonexistent_vault_root_is_a_runtime_failure_not_a_request_time_400(
        self, workspace, owner, tmp_path, settings
    ):
        """Tier 2 (existence) can only be checked where the vault is
        mounted -- at execution time, never as a request-time 400."""
        make_memory(workspace)
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path / "does-not-exist")
        run = make_run(workspace, owner)
        claimed = export.claim_vault_export_run(str(run.id))
        export.process_claimed_vault_export_run(*claimed)
        run.refresh_from_db()
        assert run.status == VaultExportRun.Status.PENDING  # retryable, not yet terminal
        assert run.error_type == VaultExportRun.ErrorType.PATH_VALIDATION_FAILED


class TestRetryClassification:
    def test_repeated_failures_eventually_terminalize(self, workspace, owner, tmp_path, settings):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path / "does-not-exist")
        run = make_run(workspace, owner)
        for _ in range(export.MAX_EXPORT_RETRIES):
            claimed = export.claim_vault_export_run(str(run.id))
            export.process_claimed_vault_export_run(*claimed)
            run.refresh_from_db()
        assert run.status == VaultExportRun.Status.FAILED
        assert run.retry_count == export.MAX_EXPORT_RETRIES
        assert "vault_export.failed" in AuditLog.objects.values_list("action", flat=True)


class TestAtomicWrite:
    def test_a_failure_mid_write_never_leaves_a_partial_final_file(self, workspace, tmp_path):
        make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        with mock.patch("os.rename", side_effect=OSError("simulated failure during rename")):
            with pytest.raises(OSError, match="simulated failure"):
                export_in_run(vault_root_real, workspace)
        memory_dir = tmp_path / "03_Workspaces" / str(workspace.id) / "Memory"
        # No `.md` file was ever created at the final path -- only a
        # temp file (if anything) could have been left, never a partial
        # `<id>.md`.
        final_files = [p for p in memory_dir.glob("*.md")] if memory_dir.exists() else []
        assert final_files == []


class TestSymlinkSwapRace:
    def test_write_path_operation_detects_a_swapped_memory_directory(self, workspace, tmp_path):
        """Simulates the Memory/ directory itself being replaced with a
        symlink to somewhere outside the vault between validation and the
        write -- the per-write realpath re-check must catch it."""
        make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        # Prime the directory chain first.
        export_in_run(vault_root_real, workspace)

        memory_dir = tmp_path / "03_Workspaces" / str(workspace.id) / "Memory"
        outside = tmp_path.parent / f"outside-{uuid.uuid4()}"
        outside.mkdir()
        real_target = memory_dir.parent / "Memory.real"
        memory_dir.rename(real_target)
        memory_dir.symlink_to(outside)

        with pytest.raises(VaultPathEscapeError):
            export_in_run(vault_root_real, workspace)

    def test_read_path_prune_scan_detects_a_swapped_memory_directory(self, workspace, tmp_path):
        make_memory(workspace)
        vault_root_real = export.validate_vault_root_exists(str(tmp_path))
        export_in_run(vault_root_real, workspace)

        memory_dir = tmp_path / "03_Workspaces" / str(workspace.id) / "Memory"
        outside = tmp_path.parent / f"outside-{uuid.uuid4()}"
        outside.mkdir()
        real_target = memory_dir.parent / "Memory.real"
        memory_dir.rename(real_target)
        memory_dir.symlink_to(outside)

        with pytest.raises(VaultPathEscapeError):
            export_in_run(vault_root_real, workspace)


class TestValidateRequestWiring:
    def test_tier1_rejects_an_unconfigured_vault_path(self, settings):
        settings.OBSIDIAN_VAULT_PATH = ""
        with pytest.raises(export.ToolValidationError):
            export.validate_vault_export_request(workspace=None, payload={})

    def test_tier1_accepts_a_safe_absolute_path(self, settings, tmp_path):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        export.validate_vault_export_request(workspace=None, payload={})  # must not raise

    def test_create_request_400s_on_an_unsafe_vault_root(self, workspace, owner, settings):
        settings.OBSIDIAN_VAULT_PATH = "relative/not/absolute"
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="vault_export",
                origin=ApprovalRequest.Origin.HUMAN,
                channel=CHANNEL_WEB,
            )

    def test_create_request_succeeds_when_vault_root_is_safe(
        self, workspace, owner, settings, tmp_path
    ):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
        request = create_request(
            workspace=workspace,
            actor=owner,
            tool_name="vault_export",
            origin=ApprovalRequest.Origin.HUMAN,
            channel=CHANNEL_WEB,
        )
        assert request.status == ApprovalRequest.Status.PENDING


@pytest.mark.django_db(transaction=True)
def test_durable_before_dispatch_survives_a_broker_publish_failure(settings, tmp_path):
    """Mirrors memory's/knowledge's durable-before-dispatch test: a broker
    publish failure AFTER the approval's transaction commits must never
    propagate, and the VaultExportRun row must already exist, durably, for
    the recovery scan to reclaim."""
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.OBSIDIAN_VAULT_PATH = str(tmp_path)  # let tier 1 genuinely pass
    owner = register_user(email="durable-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    admin = register_user(email="durable-admin@test.dev", password=PASSWORD, name="Admin")
    from workspaces.models import WorkspaceMember

    WorkspaceMember.objects.create(workspace=workspace, user=admin, role=WorkspaceMember.Role.ADMIN)

    request = create_request(
        workspace=workspace,
        actor=owner,
        tool_name="vault_export",
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )

    with mock.patch(
        "tools.tasks.run_vault_export.delay",
        side_effect=ConnectionRefusedError("no redis"),
    ):
        approve_request(approval_request=request, actor=admin, channel=CHANNEL_WEB)

    run = VaultExportRun.objects.get(approval_request=request)
    assert run.status == VaultExportRun.Status.PENDING


@pytest.mark.django_db(transaction=True)
def test_two_workers_racing_to_claim_one_pending_run_exactly_one_wins():
    owner = register_user(email="claimrace-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    run = make_run(workspace, owner)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key):
        try:
            barrier.wait(timeout=5)
            results[key] = export.claim_vault_export_run(str(run.id))
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    winners = [v for v in results.values() if v is not None]
    losers = [v for v in results.values() if v is None]
    assert len(winners) == 1
    assert len(losers) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_exports_for_the_same_workspace_are_serialized(tmp_path, settings):
    """Codex review finding: two overlapping vault_export runs for the same
    workspace must never run their write+prune pass concurrently -- without
    serialization, an older run's approved-id snapshot could be used to
    prune a file a newer, since-completed run just legitimately wrote.

    One thread holds the workspace-row lock directly (simulating an
    in-progress export); a second thread's REAL `export_workspace_memories`
    call races to start. The second call's own `select_for_update` must
    block until the first releases -- proven by asserting it never
    completes before the release event fires.
    """
    settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
    owner = register_user(email="serialize-owner@test.dev", password=PASSWORD, name="Owner")
    workspace = Workspace.objects.get(owner=owner)
    vault_root_real = export.validate_vault_root_exists(str(tmp_path))
    run, token = claimed_run(workspace, owner)

    lock_held = threading.Event()
    release_lock = threading.Event()
    errors = []
    racer_result = {}

    def holder():
        try:
            with transaction.atomic():
                Workspace.objects.select_for_update().get(pk=workspace.pk)
                lock_held.set()
                release_lock.wait(timeout=5)
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    def racer():
        try:
            assert lock_held.wait(timeout=5)
            with export.claimed_run_scope(run.id, token):
                export.export_workspace_memories(vault_root_real, workspace)
            racer_result["completed_after_release"] = release_lock.is_set()
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t_holder = threading.Thread(target=holder)
    t_racer = threading.Thread(target=racer)
    t_holder.start()
    t_racer.start()
    assert lock_held.wait(timeout=5)
    # Give the racer a moment to reach (and block on) its own
    # select_for_update before the holder releases.
    time.sleep(0.2)
    release_lock.set()
    t_holder.join(timeout=10)
    t_racer.join(timeout=10)

    assert not errors, errors
    assert racer_result.get("completed_after_release") is True
