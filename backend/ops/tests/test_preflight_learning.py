"""`manage.py preflight` learning checks (Phase 8A closeout): sources
configured, beat tasks scheduled, the scrubber self-test on the corpus, the
learning budget, the last extraction run. All read-only: no model call, no
`ModelUsage`, no outside contact."""

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import AIBudget, ModelUsage
from memory.ingestion.services import ensure_default_sources
from memory.models import EpisodeExtractionJob, IngestionSource, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
Kind = IngestionSource.Kind


def _learning():
    return {r.name: r for r in preflight.check_learning()}


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def sources(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    return {s.kind: s for s in IngestionSource.objects.filter(workspace=personal)}


def _enable(source):
    source.enabled = True
    source.save()


# --- learning_sources ---------------------------------------------------------


def test_sources_warn_without_an_owner():
    assert _learning()["learning_sources"].status == WARN


def test_sources_fail_when_not_seeded(owner):
    result = _learning()["learning_sources"]
    assert result.status == FAIL
    assert "run seed_ingestion_sources" in result.detail


def test_sources_warn_when_all_disabled(sources):
    result = _learning()["learning_sources"]
    assert (result.status, result.detail) == (WARN, "all four sources are disabled")


def test_sources_warn_on_an_unmapped_or_paused_source(settings, sources, personal):
    settings.MEMORY_OWNER_GIT_EMAILS = ["owner@test.dev"]
    _enable(sources[Kind.GIT])
    _enable(sources[Kind.CLAUDE_CODE_HOOKS])
    add_rule(sources[Kind.GIT], WorkspaceMappingRule.RuleType.REPO, "r1", personal)
    hooks = sources[Kind.CLAUDE_CODE_HOOKS]
    hooks.paused_at = timezone.now()
    hooks.save()
    result = _learning()["learning_sources"]
    assert result.status == WARN
    assert result.detail.startswith("enabled: claude_code_hooks, git")
    assert "claude_code_hooks paused" in result.detail
    assert "claude_code_hooks has no mapping rule" in result.detail
    assert "git" not in result.detail.split(";", 1)[1].replace("claude_code_hooks", "")


def test_sources_warn_when_git_has_no_owner_emails(settings, sources, personal):
    settings.MEMORY_OWNER_GIT_EMAILS = []
    _enable(sources[Kind.GIT])
    add_rule(sources[Kind.GIT], WorkspaceMappingRule.RuleType.REPO, "r1", personal)
    result = _learning()["learning_sources"]
    assert result.status == WARN
    assert "MEMORY_OWNER_GIT_EMAILS is empty" in result.detail


def test_sources_pass_when_enabled_and_mapped(settings, sources, personal):
    settings.MEMORY_OWNER_GIT_EMAILS = ["owner@test.dev"]
    _enable(sources[Kind.GIT])
    _enable(sources[Kind.CLAUDE_CODE_HOOKS])
    add_rule(sources[Kind.GIT], WorkspaceMappingRule.RuleType.REPO, "r1", personal)
    add_rule(
        sources[Kind.CLAUDE_CODE_HOOKS],
        WorkspaceMappingRule.RuleType.PATH_PREFIX,
        "/Users/o/code",
        personal,
    )
    result = _learning()["learning_sources"]
    assert (result.status, result.detail) == (PASS, "enabled: claude_code_hooks, git")


def test_transcript_sources_are_mapped_by_the_hooks_rules(settings, sources, personal):
    """Phase 8B branch 8 (D13): a transcript source owns no rules; it is
    mapped exactly when the hooks source has an enabled path rule."""
    _enable(sources[Kind.CLAUDE_CODE_TRANSCRIPTS])
    result = _learning()["learning_sources"]
    assert "claude_code_transcripts has no mapping rule" in result.detail
    add_rule(
        sources[Kind.CLAUDE_CODE_HOOKS],
        WorkspaceMappingRule.RuleType.PATH_PREFIX,
        "/Users/o/code",
        personal,
    )
    result = _learning()["learning_sources"]
    assert (result.status, result.detail) == (PASS, "enabled: claude_code_transcripts")


def test_the_scrubber_self_test_includes_the_transcript_corpus(settings, tmp_path):
    novel = "zz-transcript-only-novel-format"
    _write_corpus(
        tmp_path,
        secrets=[{"id": "s1", "text": "sk-ant-api03-abcdefghijklmnopqrstuvwx", "needles": []}],
    )
    (tmp_path / "transcripts.jsonl").write_text(
        json.dumps({"id": "tx-1", "class": "secret", "text": novel, "needles": [novel]}) + "\n",
        encoding="utf-8",
    )
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path)
    result = _learning()["learning_scrubber"]
    assert result.status == FAIL and "tx-1" in result.detail and novel not in result.detail


def test_a_disabled_rule_does_not_count_as_mapped(settings, sources, personal):
    settings.MEMORY_OWNER_GIT_EMAILS = ["owner@test.dev"]
    _enable(sources[Kind.GIT])
    add_rule(sources[Kind.GIT], WorkspaceMappingRule.RuleType.REPO, "r1", personal, enabled=False)
    assert "git has no mapping rule" in _learning()["learning_sources"].detail


# --- learning_beat ------------------------------------------------------------


def test_beat_passes_on_the_real_schedule():
    result = _learning()["learning_beat"]
    assert result.status == PASS, result.detail
    assert "7 worker-beat and 2 runner-beat" in result.detail


def test_beat_fails_without_the_graphify_recovery_scan(monkeypatch):
    """Phase 8B closeout: the reflection, the transcript pull and the
    Graphify lease-recovery scan are all required."""
    schedules, registered = preflight.beat_schedules()
    runner = {
        name: entry
        for name, entry in schedules["runner"].items()
        if entry["task"] != "harness.tasks.recover_stale_graphify_runs"
    }
    worker = {
        name: entry
        for name, entry in schedules["worker"].items()
        if entry["task"] != "memory.ingestion.tasks.run_weekly_reflection_task"
    }
    trimmed = {"worker": worker, "runner": runner}
    monkeypatch.setattr(preflight, "beat_schedules", lambda: (trimmed, registered))
    result = _learning()["learning_beat"]
    assert result.status == FAIL
    assert "recover_stale_graphify_runs not on the runner beat" in result.detail
    assert "run_weekly_reflection_task not on the worker beat" in result.detail


def test_beat_fails_when_a_task_is_on_the_wrong_beat(monkeypatch):
    schedules, registered = preflight.beat_schedules()
    swapped = {"worker": schedules["runner"], "runner": schedules["worker"]}
    monkeypatch.setattr(preflight, "beat_schedules", lambda: (swapped, registered))
    result = _learning()["learning_beat"]
    assert result.status == FAIL
    assert "harness.tasks.ingest_repo_commits not on the runner beat" in result.detail


def test_beat_fails_when_a_task_is_not_registered(monkeypatch):
    schedules, registered = preflight.beat_schedules()
    registered = registered - {"memory.ingestion.tasks.run_learning_night_task"}
    monkeypatch.setattr(preflight, "beat_schedules", lambda: (schedules, registered))
    result = _learning()["learning_beat"]
    assert result.status == FAIL
    assert "run_learning_night_task not registered" in result.detail


def test_beat_fails_without_a_published_schedule(monkeypatch):
    monkeypatch.setattr(preflight, "beat_schedules", lambda: ({}, set()))
    assert _learning()["learning_beat"].status == FAIL


# --- learning_scrubber --------------------------------------------------------


def test_scrubber_self_test_passes_on_the_committed_corpus():
    result = _learning()["learning_scrubber"]
    assert result.status == PASS, result.detail
    assert result.detail.startswith("secrets ")
    secrets_part = result.detail.split(",")[0]
    hit, total = secrets_part.split()[1].split("/")
    assert hit == total and int(total) > 0


# The transcript corpus is required since the Phase 8B closeout; by default
# a test corpus carries one transcript secret the fake scrubbers below hide.
TRANSCRIPT_SECRET = {"id": "tx", "class": "secret", "text": "pasted S3CR3T", "needles": ["S3CR3T"]}


def _write_corpus(directory, *, secrets, pii=(), benign=(), transcripts=(TRANSCRIPT_SECRET,)):
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("secrets", secrets),
        ("pii", pii),
        ("benign", benign),
        ("transcripts", transcripts),
    ):
        if rows is None:
            continue
        (directory / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )


def test_scrubber_fails_on_a_missed_secret_and_names_only_the_id(settings, tmp_path, monkeypatch):
    novel = "zz-novel-format-that-no-rule-knows"
    _write_corpus(
        tmp_path,
        secrets=[{"id": "novel-secret", "text": f"key {novel} here", "needles": [novel]}],
    )
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path)
    monkeypatch.setattr(
        "memory.ingestion.scrubber.scrub_text",
        lambda text, **kw: type("R", (), {"text": text, "hits": {}})(),
    )
    result = _learning()["learning_scrubber"]
    assert result.status == FAIL
    assert "missed: novel-secret" in result.detail
    assert novel not in result.detail


def test_scrubber_warns_on_pii_miss_or_changed_benign(settings, tmp_path, monkeypatch):
    _write_corpus(
        tmp_path,
        secrets=[{"id": "s", "text": "token S3CR3T", "needles": ["S3CR3T"]}],
        pii=[{"id": "p", "text": "call PII-ITEM", "needles": ["PII-ITEM"]}],
        benign=[{"id": "b", "text": "plain words"}],
    )
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path)

    def fake_scrub(text, **kw):
        return type("R", (), {"text": text.replace("S3CR3T", "[SECRET]"), "hits": {}})()

    monkeypatch.setattr("memory.ingestion.scrubber.scrub_text", fake_scrub)
    result = _learning()["learning_scrubber"]
    assert result.status == WARN
    assert result.detail.startswith("secrets 2/2, PII 0/1, benign 1/1 unchanged")
    assert "incl. 1 transcript fragments" in result.detail
    assert "check: p" in result.detail


def test_scrubber_warns_when_the_corpus_is_absent(settings, tmp_path):
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path / "missing")
    assert _learning()["learning_scrubber"].status == WARN


def test_scrubber_fails_without_the_transcript_corpus(settings, tmp_path):
    """Phase 8B closeout: `transcripts.jsonl` is part of the gate; a corpus
    without it, or without one transcript secret, fails."""
    _write_corpus(
        tmp_path,
        secrets=[{"id": "s", "text": "sk-ant-api03-abcdefghijklmnopqrstuvwx", "needles": []}],
        transcripts=None,
    )
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path)
    result = _learning()["learning_scrubber"]
    assert result.status == FAIL and "transcripts.jsonl is missing" in result.detail
    (tmp_path / "transcripts.jsonl").write_text(
        json.dumps({"id": "tx-pii", "class": "pii", "text": "call 555", "needles": []}) + "\n",
        encoding="utf-8",
    )
    result = _learning()["learning_scrubber"]
    assert result.status == FAIL and "has no secret item" in result.detail


def test_scrubber_fails_on_an_unreadable_or_empty_corpus(settings, tmp_path):
    _write_corpus(tmp_path, secrets=[], transcripts=[])
    settings.PREFLIGHT_SCRUBBER_CORPUS_DIR = str(tmp_path)
    assert _learning()["learning_scrubber"].status == FAIL
    (tmp_path / "secrets.jsonl").write_text("{not json\n", encoding="utf-8")
    result = _learning()["learning_scrubber"]
    assert result.status == FAIL and "corpus unreadable" in result.detail


# --- learning_budget ----------------------------------------------------------


def test_budget_fails_before_seed_and_passes_after():
    assert _learning()["learning_budget"].status == FAIL
    call_command("seed_ai_defaults", stdout=StringIO())
    result = _learning()["learning_budget"]
    assert result.status == PASS, result.detail
    assert "learning: monthly 20 USD" in result.detail
    assert "learning_extraction_daily: daily 1 USD" in result.detail


def test_budget_warns_without_a_monthly_cap(settings):
    purposes = list(settings.AI_LEARNING_PURPOSES) + list(settings.AI_LEARNING_EVAL_PURPOSES)
    AIBudget.objects.create(name="daily-only", purposes=purposes)
    AIBudget.objects.filter(name="daily-only").update(daily_limit_usd=1)
    result = _learning()["learning_budget"]
    assert result.status == WARN and "no monthly cap" in result.detail


def test_budget_fails_when_a_phase_8b_purpose_is_not_configured(settings):
    """Phase 8B closeout: reconciliation, reflection and the eval answers
    must each be a configured budget purpose."""
    call_command("seed_ai_defaults", stdout=StringIO())
    settings.AI_LEARNING_EVAL_PURPOSES = ()
    result = _learning()["learning_budget"]
    assert result.status == FAIL and "memory.eval_answer" in result.detail
    settings.AI_LEARNING_EVAL_PURPOSES = ("memory.eval_answer",)
    settings.AI_LEARNING_PURPOSES = ("memory.episode_extraction", "memory.consolidation")
    result = _learning()["learning_budget"]
    assert result.status == FAIL
    assert "memory.fact_reconciliation, memory.reflection" in result.detail


def test_budget_fails_when_the_eval_budget_is_missing():
    call_command("seed_ai_defaults", stdout=StringIO())
    AIBudget.objects.filter(name="learning_eval").update(is_active=False)
    result = _learning()["learning_budget"]
    assert result.status == FAIL and "memory.eval_answer" in result.detail


def test_budget_ignores_inactive_budgets(settings):
    AIBudget.objects.create(
        name="off",
        purposes=list(settings.AI_LEARNING_PURPOSES),
        monthly_limit_usd=5,
        is_active=False,
    )
    assert _learning()["learning_budget"].status == FAIL


# --- learning_last_run --------------------------------------------------------


def _job(workspace, status, *, night=None, **extra):
    return EpisodeExtractionJob.objects.create(
        workspace=workspace, night=night or timezone.localdate(), status=status, **extra
    )


Status = EpisodeExtractionJob.Status


def test_last_run_warns_with_no_run():
    result = _learning()["learning_last_run"]
    assert (result.status, result.detail) == (WARN, "no extraction run yet")


def test_last_run_passes_when_the_latest_night_succeeded(personal):
    _job(personal, Status.FAILED, night=timezone.localdate() - timedelta(days=3))
    _job(personal, Status.SUCCEEDED, consolidated_at=timezone.now())
    result = _learning()["learning_last_run"]
    assert result.status == PASS
    assert result.detail == f"night {timezone.localdate().isoformat()}: 1 succeeded"


def test_last_run_fails_on_a_failed_job(personal):
    _job(personal, Status.SUCCEEDED, consolidated_at=timezone.now())
    _job(personal, Status.FAILED, error_type="schema_invalid", error_message="secret detail")
    result = _learning()["learning_last_run"]
    assert result.status == FAIL
    assert "schema_invalid" in result.detail
    assert "secret detail" not in result.detail


def test_last_run_warns_on_deferred_stuck_or_unconsolidated(personal):
    _job(personal, Status.DEFERRED)
    _job(personal, Status.RUNNING, lease_expires_at=timezone.now() - timedelta(minutes=5))
    _job(personal, Status.SUCCEEDED)
    result = _learning()["learning_last_run"]
    assert result.status == WARN
    for note in ("deferred by a budget", "1 with an expired lease", "1 awaiting consolidation"):
        assert note in result.detail


# --- wiring -------------------------------------------------------------------


def test_learning_checks_are_registered_and_make_no_model_call(owner, sources):
    assert ("learning", preflight.check_learning) in preflight.CHECKS
    results = preflight.run_preflight(checks=(("learning", preflight.check_learning),))
    names = [r.name for r in results]
    assert names == [
        "learning_sources",
        "learning_beat",
        "learning_scrubber",
        "learning_budget",
        "learning_last_run",
    ]
    assert not ModelUsage.objects.exists()


def test_command_prints_the_learning_lines(settings, monkeypatch, owner, sources):
    settings.AI_FAKE_PROVIDER = True
    settings.CELERY_TASK_ALWAYS_EAGER = True
    call_command("seed_ai_defaults", stdout=StringIO())
    call_command("build_vector_indexes", stdout=StringIO())
    out = StringIO()
    call_command("preflight", "--no-model-calls", stdout=out)
    lines = out.getvalue().splitlines()
    assert "WARN  learning_sources  all four sources are disabled" in lines
    assert any(line.startswith("PASS  learning_scrubber  secrets ") for line in lines)
    assert any(line.startswith("PASS  learning_beat  ") for line in lines)
    assert any(line.startswith("PASS  learning_budget  ") for line in lines)
    assert "WARN  learning_last_run  no extraction run yet" in lines
