"""Phase 8B branch 7 (brief D12): the `00_Inbox/` import-once inside an
approved `vault_export` run -- mapping, episodes, the move to
`_imported/<date>/`, skips and caps, provider class, scrubbing, the
move-failure dedupe and the "done when" run. A real temporary vault under
`tmp_path`; FakeAdapter only, no real model."""

import os
from unittest import mock

import pytest
from django.utils import timezone

from accounts.services import register_user
from core.channels import CHANNEL_WEB
from memory.ingestion import extraction
from memory.ingestion.services import ensure_default_sources
from memory.models import (
    Episode,
    EpisodeExtractionJob,
    IngestionSource,
    Memory,
    WorkspaceMappingRule,
)
from memory.tests.graph_helpers import entity, fact
from memory.tests.ingestion_helpers import add_rule
from memory.tests.learning_helpers import capture_llm, fake_provider, payload_text, seed_prompts
from tools import export, vault_inbox
from tools.models import VaultExportRun
from tools.tests.vault_helpers import claimed_run, run_full
from tools.vault_frontmatter import split_frontmatter
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
VAULT = WorkspaceMappingRule.RuleType.VAULT_PREFIX
SECRET = "AKIA" + "IOSFODNN7EXAMPLE"


@pytest.fixture
def owner(db):
    user = register_user(email="inbox-owner@test.dev", password=PASSWORD, name="Owner")
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
def source(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    return IngestionSource.objects.get(workspace=personal, kind=IngestionSource.Kind.OBSIDIAN_INBOX)


@pytest.fixture
def enabled(source):
    source.enabled = True
    source.save()
    return source


@pytest.fixture
def vault(tmp_path, settings):
    settings.OBSIDIAN_VAULT_PATH = str(tmp_path)
    return export.validate_vault_root_exists(str(tmp_path))


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "00_Inbox"
    folder.mkdir()
    return folder


def note(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    return path


def imported_dir(inbox):
    return inbox / "_imported" / timezone.localdate().isoformat()


# --- reading only when enabled ----------------------------------------------------


def test_disabled_source_reads_nothing(source, personal, vault, inbox):
    add_rule(source, VAULT, "00_Inbox", personal)
    path = note(inbox, "idea.md", "An idea worth keeping")
    with mock.patch("tools.vault_inbox.read_validated_bytes") as reader:
        stats = run_full(vault, personal)
    reader.assert_not_called()
    assert path.exists() and not Episode.all_objects.exists()
    assert stats.inbox.imported == 0


def test_paused_source_reads_nothing(enabled, personal, vault, inbox):
    enabled.paused_at = timezone.now()
    enabled.save()
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "idea.md", "An idea worth keeping")
    with mock.patch("tools.vault_inbox.list_validated") as lister:
        run_full(vault, personal)
    lister.assert_not_called()
    assert not Episode.all_objects.exists()


def test_firm_workspace_run_never_reads_the_inbox(enabled, personal, firm, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", firm)
    path = note(inbox, "idea.md", "An idea worth keeping")
    run_full(vault, firm)
    assert path.exists() and not Episode.all_objects.exists()


def test_missing_inbox_folder_is_not_created(enabled, personal, vault, tmp_path):
    run_full(vault, personal)
    assert not (tmp_path / "00_Inbox").exists()


# --- import, mapping, move ------------------------------------------------------------


def test_mapped_note_becomes_an_episode_and_is_moved(enabled, personal, firm, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    add_rule(enabled, VAULT, "00_Inbox/work", firm)
    top = note(inbox, "idea.md", "---\ntags:\n  - x\n---\nPersonal idea: learn the cello")
    work = note(inbox / "work", "meeting.md", "Firm decision: deploy the api on Hetzner")
    stats = run_full(vault, personal)

    assert stats.inbox.imported == 2
    assert not top.exists() and not work.exists()
    assert (imported_dir(inbox) / "idea.md").exists()
    assert (imported_dir(inbox) / "meeting.md").exists()
    personal_ep = Episode.objects.get(workspace=personal)
    firm_ep = Episode.objects.get(workspace=firm)
    assert personal_ep.owner_text == "Personal idea: learn the cello"
    assert personal_ep.kind == "obsidian.inbox_note"
    assert personal_ep.sensitivity == Episode.Sensitivity.NORMAL
    assert personal_ep.external_id.startswith("sha256:")
    assert personal_ep.source_id == enabled.id
    assert firm_ep.owner_text == "Firm decision: deploy the api on Hetzner"
    assert firm_ep.metadata == {"vault_folder": "00_Inbox/work"}


def test_unmapped_note_is_counted_and_left_in_place(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox/work", personal)
    path = note(inbox / "home", "todo.md", "Fix the fence")
    stats = run_full(vault, personal)
    assert path.exists() and not Episode.all_objects.exists()
    assert stats.inbox.unmapped == 1
    assert not (inbox / "_imported").exists()


def test_friday_export_note_is_skipped(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    path = note(inbox, "echo.md", "---\nsource: friday_export\n---\nAn exported memory")
    stats = run_full(vault, personal)
    assert path.exists() and not Episode.all_objects.exists()
    assert stats.inbox.skipped == 1


def test_imported_folder_is_never_rescanned(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    old = note(inbox / "_imported" / "2026-01-01", "old.md", "Already imported once")
    note(inbox / "_imported", "stray.md", "Also under _imported")
    run_full(vault, personal)
    assert old.exists() and not Episode.all_objects.exists()


def test_only_one_subfolder_level_is_scanned(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    deep = note(inbox / "a" / "b", "deep.md", "Too deep to import")
    other = note(inbox, "notes.txt", "Not markdown")
    run_full(vault, personal)
    assert deep.exists() and other.exists() and not Episode.all_objects.exists()


def test_symlinked_note_and_folder_are_never_followed(enabled, personal, vault, inbox, tmp_path):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    outside = tmp_path.parent / f"outside-{os.getpid()}-{id(inbox)}"
    outside.mkdir()
    target = note(outside, "target.md", "Text outside the vault")
    os.symlink(target, inbox / "link.md")
    os.symlink(outside, inbox / "linkdir")
    stats = run_full(vault, personal)
    assert not Episode.all_objects.exists()
    assert target.exists() and (inbox / "link.md").is_symlink()
    assert stats.inbox.skipped == 2


def test_rerun_is_idempotent(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "idea.md", "An idea worth keeping")
    run_full(vault, personal)
    # The owner drops the very same note in again: deduped, moved again.
    again = note(inbox, "idea.md", "An idea worth keeping")
    stats = run_full(vault, personal)
    assert Episode.all_objects.count() == 1
    assert stats.inbox.duplicate == 1 and stats.inbox.imported == 0
    assert not again.exists()
    assert (imported_dir(inbox) / "idea.1.md").exists()


def test_oversized_note_is_skipped_and_counted(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    path = note(inbox, "big.md", "x" * (70 * 1024))
    stats = run_full(vault, personal)
    assert path.exists() and not Episode.all_objects.exists()
    assert stats.inbox.skipped == 1


def test_long_body_is_truncated_and_counted(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "long.md", "word " * 2000)
    stats = run_full(vault, personal)
    assert len(Episode.objects.get().owner_text) == Episode.MAX_TEXT_CHARS
    assert stats.inbox.truncated == 1


def test_per_run_cap_defers_the_rest(enabled, personal, vault, inbox, monkeypatch):
    monkeypatch.setattr(vault_inbox, "MAX_FILES_PER_RUN", 2)
    add_rule(enabled, VAULT, "00_Inbox", personal)
    for index in range(3):
        note(inbox, f"n{index}.md", f"Note number {index} with text")
    stats = run_full(vault, personal)
    assert stats.inbox.imported == 2 and stats.inbox.deferred == 1
    assert (inbox / "n2.md").exists()


@pytest.mark.parametrize("value", ["private", "'private'", "Private", "secret"])
def test_private_frontmatter_gives_a_private_episode(enabled, personal, vault, inbox, value):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "diary.md", f"---\nsensitivity: {value}\n---\nMy private thought")
    run_full(vault, personal)
    assert Episode.objects.get().sensitivity == Episode.Sensitivity.PRIVATE


def test_unclosed_frontmatter_is_skipped(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    path = note(inbox, "broken.md", "---\nsensitivity: private\nno closing line")
    stats = run_full(vault, personal)
    assert path.exists() and not Episode.all_objects.exists()
    assert stats.inbox.skipped == 1


def test_secret_in_a_note_is_scrubbed_before_storage(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "keys.md", f"Rotate the key {SECRET} tomorrow")
    run_full(vault, personal)
    stored = Episode.objects.get()
    assert SECRET not in stored.owner_text
    assert SECRET not in str(stored.metadata) and SECRET not in stored.external_id


def test_move_failure_after_commit_gives_no_duplicate(enabled, personal, vault, inbox):
    add_rule(enabled, VAULT, "00_Inbox", personal)
    path = note(inbox, "idea.md", "An idea worth keeping")
    with mock.patch("tools.vault_inbox.rename_validated", side_effect=OSError("busy")):
        stats = run_full(vault, personal)
    assert stats.inbox.imported == 1 and stats.inbox.move_failed == 1
    assert path.exists() and Episode.all_objects.count() == 1
    stats = run_full(vault, personal)
    assert Episode.all_objects.count() == 1
    assert stats.inbox.duplicate == 1 and not path.exists()


# --- provider class (D12) ------------------------------------------------------------


@pytest.fixture
def ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    seed_prompts()


def _plan_and_run():
    with mock.patch("memory.ingestion.extraction.dispatch_episode_extraction_job"):
        job_ids = extraction.plan_nightly_extraction()
    with mock.patch("memory.ingestion.consolidation.dispatch_consolidation"):
        for job_id in job_ids:
            extraction.run_job(job_id)
    return job_ids


def test_inbox_source_is_not_personal_class(enabled):
    assert enabled.is_personal_class is False


def test_normal_note_is_extracted_without_personal_data(
    ai, enabled, personal, vault, inbox, monkeypatch
):
    fake_provider(personal_data_allowed=False, name="fake-cloud")
    capture_llm(monkeypatch)
    add_rule(enabled, VAULT, "00_Inbox", personal)
    note(inbox, "idea.md", "Refactor the scrubber so it caps every input at 64 KB first")
    note(inbox, "diary.md", "---\nsensitivity: private\n---\nMy private plan for the weekend trip")
    run_full(vault, personal)
    seen = []
    from ai_core.gateway import InferenceGateway

    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        if purpose == "memory.episode_extraction":
            seen.append((kwargs.get("personal_data"), payload_text(messages)))
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    job_ids = _plan_and_run()

    cloud = [text for flag, text in seen if not flag]
    assert len(cloud) == 1 and "caps every input" in cloud[0]
    assert all("weekend trip" not in text for text in cloud)
    private_job = EpisodeExtractionJob.objects.get(pk__in=job_ids, personal_data=True)
    # Only a cloud provider is configured: the private note fails closed.
    assert private_job.error_type == "no_provider" and private_job.candidates == []
    assert Episode.objects.get(sensitivity="private").extracted_at is None


# --- the "done when" run ----------------------------------------------------------------


def test_done_when_an_approved_run_in_a_fixture_vault(owner, enabled, personal, vault, tmp_path):
    memory = Memory.objects.create(
        workspace=personal,
        memory_type=Memory.Type.SEMANTIC,
        status=Memory.Status.APPROVED,
        source=Memory.Source.USER_CREATED,
        title="Deploys",
        content="We deploy on Fridays.",
        fingerprint="done-when",
    )
    api = entity(personal, "Api")
    fact(personal, api, text="hetzner")
    run, token = claimed_run(personal, owner)
    export.process_claimed_vault_export_run(run.id, token)
    mem_file = tmp_path / "03_Workspaces" / str(personal.id) / "Memory" / f"{memory.id}.md"
    mem_file.write_text(mem_file.read_text() + "\nEdited in Obsidian\n")

    inbox = tmp_path / "00_Inbox"
    add_rule(enabled, VAULT, "00_Inbox/work", personal)
    mapped = note(inbox / "work", "plan.md", "Ship the mirror this week")
    unmapped = note(inbox / "home", "chores.md", "Paint the fence")
    run, token = claimed_run(personal, owner)
    export.process_claimed_vault_export_run(run.id, token)

    run.refresh_from_db()
    assert run.status == VaultExportRun.Status.SUCCEEDED
    assert split_frontmatter(mem_file.read_text())[0]["friday_format"] == "2"
    ent_file = tmp_path / "03_Workspaces" / str(personal.id) / "Entities" / f"{api.id}.md"
    assert ent_file.exists() and run.entity_notes_count == 1
    conflict_dir = tmp_path / "_conflicts" / timezone.localdate().isoformat() / str(personal.id)
    assert "Edited in Obsidian" in (conflict_dir / f"{memory.id}.md").read_text()
    assert run.conflicts_saved == 1
    assert not mapped.exists() and (inbox / "_imported").exists()
    assert unmapped.exists()
    assert (run.inbox_imported_count, run.inbox_unmapped_count) == (1, 1)
    assert Episode.objects.get().owner_text == "Ship the mirror this week"
