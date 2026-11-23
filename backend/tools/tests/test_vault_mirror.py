"""Phase 8B branch 7 (brief D12): vault format v2, conflict copies, entity
notes, the approved-run gate and the approval's dry pass. A real temporary
vault under `tmp_path`, as the existing export tests use; no Obsidian app."""

import os
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from django.utils import timezone

from accounts.services import register_user
from approvals.models import ApprovalRequest
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from memory.graph.services import as_instant
from memory.ingestion.services import ensure_default_sources
from memory.models import Episode, IngestionSource, Memory, MemoryEntity, MemoryFact
from memory.tests.graph_helpers import T0, T1, entity, fact, system_fact
from tools import export, vault_entities
from tools.models import VaultExportRun
from tools.tests.vault_helpers import claimed_run, export_in_run
from tools.vault_frontmatter import body_sha256, is_owner_edited, split_frontmatter
from tools.vault_paths import VaultPathEscapeError
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
EDIT = "THE OWNER TYPED THIS IN OBSIDIAN"


@pytest.fixture
def owner(db):
    user = register_user(email="mirror-owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def vault(tmp_path, settings):
    settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
    return export.validate_vault_root_exists(str(tmp_path))


def make_memory(workspace, **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.SEMANTIC,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.USER_CREATED,
        "title": "A memory",
        "content": "Original content.",
        "fingerprint": uuid.uuid4().hex,
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


def mem_path(root, workspace, memory, tree="03_Workspaces"):
    return root / tree / str(workspace.id) / "Memory" / f"{memory.id}.md"


def ent_path(root, workspace, ent, tree="03_Workspaces"):
    return root / tree / str(workspace.id) / "Entities" / f"{ent.id}.md"


def today():
    return timezone.localdate().isoformat()


def open_tree_text(root):
    """Every file outside `_private/`, concatenated."""
    texts = []
    for path in root.rglob("*"):
        if path.is_file() and "_private" not in path.relative_to(root).parts:
            texts.append(path.read_text(errors="replace"))
    return "\n".join(texts)


def edit_body(path, marker=EDIT):
    text = path.read_text()
    path.write_text(text + f"\n{marker}\n")


# --- format v2 -------------------------------------------------------------------


class TestFormatV2:
    def test_run_writes_v2_files(self, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        text = mem_path(tmp_path, personal, memory).read_text()
        fields, body = split_frontmatter(text)
        assert fields["friday_format"] == "2"
        assert fields["content_sha256"] == body_sha256(body)
        assert not is_owner_edited(text)

    def test_v1_file_is_rewritten_without_a_conflict(self, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        path = mem_path(tmp_path, personal, memory)
        v1 = (
            f"---\nfriday_id: {memory.id}\ntype: semantic\nworkspace: {personal.id}\n"
            "project: \nsensitivity: normal\nsource: friday_export\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n---\n\n# Old\n\nOld v1 body\n"
        )
        path.write_text(v1)
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert stats.conflicts_saved == 0
        assert not (tmp_path / "_conflicts").exists()
        assert split_frontmatter(path.read_text())[0]["friday_format"] == "2"


# --- conflicts ---------------------------------------------------------------------


class TestConflicts:
    def test_edited_body_is_copied_then_overwritten(self, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        path = mem_path(tmp_path, personal, memory)
        edit_body(path)
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)

        copy = tmp_path / "_conflicts" / today() / str(personal.id) / f"{memory.id}.md"
        assert copy.exists() and EDIT in copy.read_text()
        assert EDIT not in path.read_text()
        assert not is_owner_edited(path.read_text())
        assert stats.conflicts_saved == 1 and stats.conflicts_private == 0

    def test_unedited_file_gives_no_copy(self, personal, vault, tmp_path):
        make_memory(personal)
        export_in_run(vault, personal)
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert stats.conflicts_saved == 0
        assert not (tmp_path / "_conflicts").exists()

    def test_a_second_copy_the_same_day_never_replaces_the_first(self, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        path = mem_path(tmp_path, personal, memory)
        edit_body(path, "FIRST EDIT")
        export_in_run(vault, personal)
        edit_body(path, "SECOND EDIT")
        export_in_run(vault, personal)
        folder = tmp_path / "_conflicts" / today() / str(personal.id)
        texts = sorted(p.read_text() for p in folder.iterdir())
        assert len(texts) == 2
        assert any("FIRST EDIT" in t for t in texts) and any("SECOND EDIT" in t for t in texts)

    def test_edited_file_about_to_be_pruned_is_copied_first(self, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        path = mem_path(tmp_path, personal, memory)
        edit_body(path)
        memory.status = Memory.Status.ARCHIVED
        memory.save(update_fields=["status"])
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert not path.exists()
        copy = tmp_path / "_conflicts" / today() / str(personal.id) / f"{memory.id}.md"
        assert EDIT in copy.read_text()
        assert stats.conflicts_saved == 1 and stats.removed == 1

    @pytest.mark.parametrize("edit_frontmatter", [False, True])
    def test_edited_private_note_keeps_its_copy_inside_private(
        self, personal, vault, tmp_path, edit_frontmatter
    ):
        memory = make_memory(personal, sensitivity=Memory.Sensitivity.PRIVATE)
        export_in_run(vault, personal)
        path = mem_path(tmp_path, personal, memory, tree="_private")
        edit_body(path)
        if edit_frontmatter:
            # An owner edit cannot move a note out of `_private/`.
            path.write_text(path.read_text().replace("sensitivity: private", "sensitivity: normal"))
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)

        copy = tmp_path / "_private" / "_conflicts" / today() / str(personal.id) / f"{memory.id}.md"
        assert copy.exists() and EDIT in copy.read_text()
        assert not (tmp_path / "_conflicts").exists()
        assert EDIT not in open_tree_text(tmp_path)
        assert stats.conflicts_saved == 1 and stats.conflicts_private == 1

    @pytest.mark.parametrize("private", [False, True])
    def test_symlinked_conflicts_folder_is_refused(self, personal, vault, tmp_path, private):
        sensitivity = Memory.Sensitivity.PRIVATE if private else Memory.Sensitivity.NORMAL
        memory = make_memory(personal, sensitivity=sensitivity)
        export_in_run(vault, personal)
        tree = "_private" if private else "03_Workspaces"
        path = mem_path(tmp_path, personal, memory, tree=tree)
        edit_body(path)
        outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
        outside.mkdir()
        link_parent = tmp_path / "_private" if private else tmp_path
        os.symlink(outside, link_parent / "_conflicts")

        with pytest.raises(VaultPathEscapeError):
            export_in_run(vault, personal)
        assert list(outside.iterdir()) == []
        # Refused BEFORE the overwrite: the owner's edit is still there.
        assert EDIT in path.read_text()

    def test_conflict_run_records_count_and_work_log_notice(self, owner, personal, vault, tmp_path):
        memory = make_memory(personal)
        export_in_run(vault, personal)
        edit_body(mem_path(tmp_path, personal, memory))
        run, token = claimed_run(personal, owner)
        export.process_claimed_vault_export_run(run.id, token)

        run.refresh_from_db()
        assert run.status == VaultExportRun.Status.SUCCEEDED
        assert run.conflicts_saved == 1
        notice = Memory.objects.get(
            workspace=personal,
            memory_type=Memory.Type.WORK_LOG,
            title__contains="conflicts_saved",
        )
        assert "Obsidian'da düzenlenmiş 1 not" in notice.content
        assert f"`_conflicts/{today()}/`" in notice.content
        assert "web arayüzünde" in notice.content
        assert EDIT not in notice.content
        audit = AuditLog.objects.get(action="vault_export", resource_id=str(run.id))
        assert audit.metadata["conflicts_saved"] == 1
        fresh = ApprovalRequest.objects.get(pk=run.approval_request_id)
        described = export.describe_vault_export_execution(fresh)
        assert described["conflicts_saved"] == 1
        # Branch 9: the run detail names the folder (names only).
        assert run.conflicts_private == 0 and run.conflicts_day.isoformat() == today()
        assert described["conflicts_private"] == 0
        assert described["conflicts_folders"] == [f"_conflicts/{today()}/"]

    def test_private_conflicts_name_the_private_folder(self, owner, personal, vault, tmp_path):
        shared = make_memory(personal)
        private = make_memory(personal, sensitivity=Memory.Sensitivity.PRIVATE)
        export_in_run(vault, personal)
        edit_body(mem_path(tmp_path, personal, shared))
        edit_body(mem_path(tmp_path, personal, private, tree="_private"))
        run, token = claimed_run(personal, owner)
        export.process_claimed_vault_export_run(run.id, token)
        run.refresh_from_db()
        assert (run.conflicts_saved, run.conflicts_private) == (2, 1)
        assert export.conflict_folders(run) == [
            f"_conflicts/{today()}/",
            f"_private/_conflicts/{today()}/",
        ]

    def test_a_run_without_conflicts_names_no_folder(self, owner, personal, vault, tmp_path):
        make_memory(personal)
        run, token = claimed_run(personal, owner)
        export.process_claimed_vault_export_run(run.id, token)
        run.refresh_from_db()
        assert run.conflicts_saved == 0 and run.conflicts_day is None
        assert export.conflict_folders(run) == []

    def test_edited_text_never_reaches_postgres(self, owner, personal, vault, tmp_path):
        memory = make_memory(personal)
        ent = entity(personal, "Edited Service")
        fact(personal, ent, text="hetzner")
        export_in_run(vault, personal)
        edit_body(mem_path(tmp_path, personal, memory))
        edit_body(ent_path(tmp_path, personal, ent))
        run, token = claimed_run(personal, owner)
        export.process_claimed_vault_export_run(run.id, token)

        assert not Memory.objects.filter(content__contains=EDIT).exists()
        assert not Memory.objects.filter(title__contains=EDIT).exists()
        assert not Episode.all_objects.filter(owner_text__contains=EDIT).exists()
        assert not MemoryFact.all_objects.filter(statement__contains=EDIT).exists()
        assert not MemoryEntity.all_objects.filter(name__contains=EDIT).exists()
        assert not any(EDIT in str(a.metadata) for a in AuditLog.objects.all())


# --- entity notes --------------------------------------------------------------------

LINK = re.compile(r"\[\[([0-9a-f-]{36})\|")


class TestEntityNotes:
    def test_current_and_superseded_facts_are_rendered(self, personal, vault, tmp_path):
        api = entity(personal, "Api")
        aws = entity(personal, "Aws")
        hetzner = entity(personal, "Hetzner")
        # Aws keeps a live normal fact, so it is not a private entity (an
        # entity whose only facts are history would be private, D4).
        fact(personal, aws, MemoryFact.Predicate.HAS_STATUS, text="still in use")
        old = fact(personal, api, obj=aws, valid_from=T0, valid_to=T1)
        new = fact(personal, api, obj=hetzner, valid_from=T1)
        old.superseded_by = new
        old.save()
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)

        text = ent_path(tmp_path, personal, api).read_text()
        fields, body = split_frontmatter(text)
        assert fields["type"] == "entity" and fields["friday_format"] == "2"
        assert fields["source"] == "friday_export" and fields["workspace"] == str(personal.id)
        current, history = body.split("## History")
        assert new.statement in current and f"[[{hetzner.id}|Hetzner]]" in current
        assert old.statement in history and "2026-01-01 → 2026-03-01" in history
        assert stats.entity_notes == 3

    @pytest.mark.parametrize("hour", [12, 22])
    def test_a_window_is_the_local_calendar_day(self, personal, frozen_now, hour):
        """A date-valued `valid_from` is stored as local midnight (21:00 UTC
        the day before); read back UTC-aware it must still render as its
        own local day, whatever the hour of the export."""
        frozen_now(datetime(2026, 11, 20, hour, 30, tzinfo=UTC))
        api = entity(personal, "Api")
        fact(personal, api, text="hetzner", valid_from=as_instant(date(2026, 11, 20)))
        open_files, _ = vault_entities.plan_entity_notes(personal)
        assert "2026-11-20 → now" in open_files[f"{api.id}.md"]

    def test_wiki_links_resolve_to_notes_of_the_same_workspace(
        self, personal, firm, vault, tmp_path
    ):
        for workspace in (personal, firm):
            a = entity(workspace, f"A {workspace.id}")
            b = entity(workspace, f"B {workspace.id}")
            fact(workspace, a, MemoryFact.Predicate.USES, b)
            export_in_run(vault, workspace)
        for workspace in (personal, firm):
            folder = tmp_path / "03_Workspaces" / str(workspace.id) / "Entities"
            notes = {p.stem for p in folder.iterdir()}
            ids = set(MemoryEntity.objects.filter(workspace=workspace).values_list("id", flat=True))
            for note in folder.iterdir():
                for linked in LINK.findall(note.read_text()):
                    assert uuid.UUID(linked) in ids
                    assert linked in notes

    def test_private_entity_lives_only_under_private(self, personal, vault, tmp_path):
        secret = entity(personal, "Zebra Clinic")
        fact(
            personal,
            secret,
            MemoryFact.Predicate.HAS_STATUS,
            text="appointment monday",
            sensitivity=MemoryFact.Sensitivity.PRIVATE,
        )
        export_in_run(vault, personal)
        assert ent_path(tmp_path, personal, secret, tree="_private").exists()
        assert not ent_path(tmp_path, personal, secret).exists()
        outside = open_tree_text(tmp_path)
        assert "Zebra Clinic" not in outside and "appointment monday" not in outside
        assert str(secret.id) not in outside

    def test_mixed_entity_gets_an_open_note_and_a_private_companion(
        self, personal, vault, tmp_path
    ):
        api = entity(personal, "Api")
        fact(personal, api, text="hetzner")
        fact(
            personal,
            api,
            MemoryFact.Predicate.HAS_STATUS,
            text="owner burnout notes",
            sensitivity=MemoryFact.Sensitivity.PRIVATE,
        )
        export_in_run(vault, personal)
        open_text = ent_path(tmp_path, personal, api).read_text()
        private_text = ent_path(tmp_path, personal, api, tree="_private").read_text()
        assert "hetzner" in open_text and "owner burnout notes" not in open_text
        assert "owner burnout notes" in private_text and "hetzner" not in private_text
        assert "sensitivity: private" in private_text
        assert "owner burnout notes" not in open_tree_text(tmp_path)

    def test_open_note_never_links_or_names_a_private_entity(self, personal, vault, tmp_path):
        api = entity(personal, "Api")
        ghost = entity(personal, "Ghostvendor")
        fact(personal, api, text="hetzner")
        # History only: a superseded normal fact does not make Ghostvendor
        # non-private, so it stays a private entity.
        fact(personal, api, MemoryFact.Predicate.USES, ghost, valid_from=T0, valid_to=T1)
        fact(
            personal,
            ghost,
            MemoryFact.Predicate.HAS_STATUS,
            text="secret deal",
            sensitivity=MemoryFact.Sensitivity.PRIVATE,
        )
        export_in_run(vault, personal)
        open_text = ent_path(tmp_path, personal, api).read_text()
        assert str(ghost.id) not in open_text and "Ghostvendor" not in open_text
        outside = open_tree_text(tmp_path)
        assert "Ghostvendor" not in outside and str(ghost.id) not in outside
        companion = ent_path(tmp_path, personal, api, tree="_private").read_text()
        assert f"[[{ghost.id}|Ghostvendor]]" in companion

    def test_forgetting_the_last_normal_fact_moves_the_note_to_private(
        self, personal, vault, tmp_path
    ):
        clinic = entity(personal, "Clinic")
        normal = fact(personal, clinic, text="downtown")
        fact(
            personal,
            clinic,
            MemoryFact.Predicate.HAS_STATUS,
            text="therapy",
            sensitivity=MemoryFact.Sensitivity.PRIVATE,
        )
        export_in_run(vault, personal)
        assert ent_path(tmp_path, personal, clinic).exists()
        normal.deleted_at = timezone.now()
        normal.save(update_fields=["deleted_at"])
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert not ent_path(tmp_path, personal, clinic).exists()
        private_text = ent_path(tmp_path, personal, clinic, tree="_private").read_text()
        assert "therapy" in private_text and "downtown" not in private_text
        assert "Clinic" not in open_tree_text(tmp_path)
        assert stats.removed == 1

    def test_trashed_entity_note_is_pruned(self, personal, vault, tmp_path):
        api = entity(personal, "Api")
        api_fact = fact(personal, api, text="hetzner")
        export_in_run(vault, personal)
        now = timezone.now()
        MemoryFact.all_objects.filter(pk=api_fact.pk).update(deleted_at=now)
        MemoryEntity.all_objects.filter(pk=api.pk).update(deleted_at=now)
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert not ent_path(tmp_path, personal, api).exists()
        assert stats.removed == 1

    def test_pending_and_rejected_facts_are_not_mirrored(self, personal, vault, tmp_path):
        api = entity(personal, "Api")
        fact(personal, api, text="pending place", status=MemoryFact.Status.PENDING_APPROVAL)
        fact(personal, api, text="rejected place", status=MemoryFact.Status.REJECTED)
        export_in_run(vault, personal)
        assert not ent_path(tmp_path, personal, api).exists()

    def test_code_entities_are_not_mirrored(self, personal, vault, tmp_path):
        module = entity(personal, "pkg.mod", kind="code_module", origin="graphify")
        other = entity(personal, "pkg.other", kind="code_module", origin="graphify")
        system_fact(personal, module, other)
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert not ent_path(tmp_path, personal, module).exists()
        assert not ent_path(tmp_path, personal, other).exists()
        assert "pkg.mod" not in open_tree_text(tmp_path)
        assert stats.entity_notes == 0

    def test_human_note_in_entities_is_never_pruned(self, personal, vault, tmp_path):
        export_in_run(vault, personal)
        note = tmp_path / "03_Workspaces" / str(personal.id) / "Entities" / "mine.md"
        note.write_text("my own entity notes")
        stats = export.ExportStats()
        export_in_run(vault, personal, stats)
        assert note.read_text() == "my own entity notes" and stats.removed == 0


# --- the approved-run gate -------------------------------------------------------------


class TestApprovedRunGate:
    def test_direct_export_without_a_claimed_run_is_refused(self, personal, vault, tmp_path):
        make_memory(personal)
        with pytest.raises(export.VaultRunNotClaimed):
            export.export_workspace_memories(vault, personal)
        assert list(tmp_path.iterdir()) == []

    def test_direct_inbox_import_without_a_claimed_run_is_refused(self, personal, vault):
        with pytest.raises(export.VaultRunNotClaimed):
            export.import_inbox(vault, personal)

    def test_a_scope_for_another_workspace_does_not_cover_this_one(
        self, personal, firm, vault, tmp_path
    ):
        run, token = claimed_run(firm)
        with export.claimed_run_scope(run.id, token):
            with pytest.raises(export.VaultRunNotClaimed):
                export.export_workspace_memories(vault, personal)

    def test_wrong_token_or_unapproved_request_is_refused(self, owner, personal, vault):
        run, token = claimed_run(personal, owner)
        with pytest.raises(export.VaultRunNotClaimed):
            with export.claimed_run_scope(run.id, uuid.uuid4()):
                pass
        ApprovalRequest.objects.filter(pk=run.approval_request_id).update(
            status=ApprovalRequest.Status.PENDING
        )
        with pytest.raises(export.VaultRunNotClaimed):
            with export.claimed_run_scope(run.id, token):
                pass

    def test_a_run_whose_approval_is_gone_writes_nothing(self, owner, personal, vault, tmp_path):
        make_memory(personal)
        run, token = claimed_run(personal, owner)
        ApprovalRequest.objects.filter(pk=run.approval_request_id).update(
            status=ApprovalRequest.Status.REJECTED
        )
        export.process_claimed_vault_export_run(run.id, token)
        assert list(tmp_path.iterdir()) == []
        run.refresh_from_db()
        assert run.status == VaultExportRun.Status.RUNNING  # left for the lease sweep


# --- the approval description (dry pass) -------------------------------------------------


def pending_request(workspace, owner):
    return ApprovalRequest.objects.create(
        workspace=workspace,
        tool_name="vault_export",
        status=ApprovalRequest.Status.PENDING,
        requested_by=owner,
    )


class TestApprovalDescription:
    def test_counts_and_dry_pass(self, owner, personal, vault, tmp_path):
        ensure_default_sources(workspace=personal, created_by=owner)
        IngestionSource.objects.filter(kind=IngestionSource.Kind.OBSIDIAN_INBOX).update(
            enabled=True
        )
        memory = make_memory(personal)
        make_memory(personal, sensitivity=Memory.Sensitivity.PRIVATE)
        api = entity(personal, "Api")
        fact(personal, api, text="hetzner")
        export_in_run(vault, personal)
        edit_body(mem_path(tmp_path, personal, memory))
        (tmp_path / "00_Inbox").mkdir()
        (tmp_path / "00_Inbox" / "idea.md").write_text("An idea worth keeping")
        before = sorted(str(p) for p in tmp_path.rglob("*"))

        summary = export.describe_vault_export_request(pending_request(personal, owner))
        assert summary["kind"] == "vault_export"
        assert (summary["memories"], summary["private_memories"]) == (1, 1)
        assert (summary["entity_notes"], summary["private_entity_notes"]) == (1, 0)
        assert summary["dry_pass"] == "ok"
        assert summary["conflicts_detected"] == 1
        assert summary["inbox_enabled"] is True
        assert (summary["inbox_waiting"], summary["inbox_unmapped"]) == (0, 1)
        assert "sensitivity: private" in summary["inbox_routing_note"]
        # Read-only: nothing created, moved or copied.
        assert sorted(str(p) for p in tmp_path.rglob("*")) == before
        assert not Episode.all_objects.exists()
        assert EDIT not in str(summary)

    def test_vault_unreachable_from_this_process(self, owner, personal, settings, tmp_path):
        settings.OBSIDIAN_VAULT_PATH = str(tmp_path / "not-mounted")
        make_memory(personal)
        summary = export.describe_vault_export_request(pending_request(personal, owner))
        assert summary["dry_pass"] == "vault_unavailable"
        assert summary["conflicts_detected"] is None and summary["inbox_waiting"] is None
        assert summary["memories"] == 1

    def test_decided_request_skips_the_dry_pass(self, owner, personal, vault):
        request = pending_request(personal, owner)
        request.status = ApprovalRequest.Status.APPROVED
        request.save()
        summary = export.describe_vault_export_request(request)
        assert summary["dry_pass"] == "skipped"

    def test_display_summary_over_the_api_for_the_owner_only(
        self, owner, personal, vault, web_client
    ):
        from workspaces.models import WorkspaceMember

        request = pending_request(personal, owner)
        body = web_client(owner).get(f"/api/v1/approvals/requests/{request.id}").json()
        assert body["display_summary"]["kind"] == "vault_export"
        member = register_user(email="mirror-member@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(
            workspace=personal, user=member, role=WorkspaceMember.Role.MEMBER
        )
        response = web_client(member).get(f"/api/v1/approvals/requests/{request.id}")
        assert response.status_code in (200, 403, 404)
        if response.status_code == 200:
            assert response.json()["display_summary"] is None
