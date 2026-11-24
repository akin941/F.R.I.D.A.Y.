"""`manage.py preflight` reach checks (Phase 12 closeout, brief §8):
`check_proactive` (`proactive_beat`, `proactive_schedules`,
`proactive_last_run`, `proactive_digest`, `proactive_probes`), `check_n8n`
(`n8n_allowlist`, `n8n_binding`, `n8n_relay`, `n8n_pins`) and
`check_skills` (`skills_ai`, `skills_beat`, `skills_parser`) -- each
passes, warns and fails with fakes. The beat is the real Celery app's
schedule (or the seam `beat_schedules` with an entry removed); the n8n live
check goes through the seam `pin_report` (or the real MCP client against
the fake n8n trigger behind the in-process relay). No test reaches a model,
an n8n instance or the network; no check makes a model call."""

from datetime import time, timedelta
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from accounts.services import register_user
from ai_core.models import AIBudget, ModelUsage, PromptTemplate
from connectors.mcp_client import ConnectorError
from connectors.models import ConnectorBinding
from connectors.tests import fakes
from core import probes
from core.channels import CHANNEL_WEB
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from proactive.models import ProactivePause, ProactiveRun, ProactiveSchedule
from skills import services as skill_services
from skills.models import InstructionSkill, InstructionSkillVersion
from skills.tests.helpers import PASSWORD, make_firm, make_owner, personal_of, skill_text

pytestmark = pytest.mark.django_db

SEED_MIGRATIONS = (
    ("ai_core.migrations.0009_proactive_digest_prompt_and_budget", "create_prompt_and_budget"),
    ("ai_core.migrations.0010_skills_match_prompt_and_budget", "create_prompt_and_budget"),
    ("ai_core.migrations.0012_skills_curate_prompt_and_budget", "create_prompt_and_budget"),
    (
        "ai_core.migrations.0013_skills_playbook_distill_prompt_and_budget",
        "create_prompt_and_budgets",
    ),
)
REFERENCE_TOOLS = "wf_Ab12Cd34Ef56Gh78,wf_Hb34Kd56Mf78Np90"


@pytest.fixture(autouse=True)
def seeded(db):
    """What the ai_core data migrations create (a transactional test
    elsewhere may have flushed them from a reused test database)."""
    for module, function in SEED_MIGRATIONS:
        getattr(import_module(module), function)(django_apps, None)


@pytest.fixture(autouse=True)
def no_relay(monkeypatch, settings):
    settings.N8N_RELAY_ALLOWED_TOOLS = REFERENCE_TOOLS
    monkeypatch.delenv("N8N_MCP_URL", raising=False)

    def unreachable(manifest, *, timeout):  # pragma: no cover - a test that needs it replaces it
        raise AssertionError("the n8n live check ran without an enabled binding")

    monkeypatch.setattr(preflight, "pin_report", unreachable)


@pytest.fixture
def n8n(monkeypatch):
    """The fake n8n trigger behind the in-process relay, wired to the
    production MCP client (`connectors.tests.fakes`)."""
    return fakes.install_n8n(monkeypatch, fakes.N8nServerState())


@pytest.fixture
def owner(db):
    return make_owner()


@pytest.fixture
def workspace(owner):
    return personal_of(owner)


def _rows(name, results):
    return [r for r in results if r.name == name]


def _one(name, results):
    (row,) = _rows(name, results)
    return row


def _schedule(workspace, owner, *, kind="morning_digest", next_run_at=None, **fields):
    defaults = (
        {"interval_minutes": 15}
        if kind == ProactiveSchedule.Kind.HEARTBEAT
        else {"local_time": time(7, 45), "weekdays": 0b0011111}
    )
    defaults.update(fields)
    schedule = ProactiveSchedule(
        workspace=workspace,
        kind=kind,
        enabled=True,
        created_by=owner,
        next_run_at=next_run_at or timezone.now() + timedelta(hours=1),
        **defaults,
    )
    schedule.save()
    return schedule


def _run(schedule, *, outcome, started_at=None, **fields):
    return ProactiveRun.objects.create(
        workspace=schedule.workspace,
        schedule=schedule,
        kind=schedule.kind,
        outcome=outcome,
        started_at=started_at or timezone.now(),
        **fields,
    )


# --- registration -------------------------------------------------------------


def test_the_reach_checks_are_registered_after_the_live_voice_checks():
    names = [name for name, _ in preflight.CHECKS]
    assert names[names.index("live_voice") + 1 :][:3] == ["proactive", "n8n", "skills"]
    assert ("proactive", preflight.check_proactive) in preflight.CHECKS
    assert ("n8n", preflight.check_n8n) in preflight.CHECKS
    assert ("skills", preflight.check_skills) in preflight.CHECKS


def test_a_fresh_stack_passes_with_only_the_documented_warnings(owner):
    results = preflight.run_preflight(
        model_calls=False,
        checks=(
            ("proactive", preflight.check_proactive),
            ("n8n", preflight.check_n8n),
            ("skills", preflight.check_skills),
        ),
    )
    assert {r.name: r.status for r in results} == {
        "proactive_beat": PASS,
        "proactive_schedules": WARN,  # owner step: enable morning_digest on /today
        "proactive_last_run": WARN,  # no schedule enabled
        "proactive_digest": PASS,
        "proactive_probes": PASS,
        "n8n_allowlist": PASS,
        "n8n_binding": WARN,  # n8n not configured (owner step 3)
        "n8n_relay": WARN,
        "n8n_pins": WARN,
        "skills_ai": PASS,
        "skills_beat": PASS,
        "skills_parser": PASS,
    }
    assert not ModelUsage.objects.exists()
    text = " ".join(r.detail for r in results)
    assert "owner step" in text


# --- proactive_beat -----------------------------------------------------------


def test_beat_passes_on_the_real_schedule():
    row = _one("proactive_beat", preflight.check_proactive())
    assert row.status == PASS


@pytest.mark.parametrize("task", ["proactive.tasks.heartbeat", "proactive.tasks.prune"])
def test_beat_fails_without_an_entry(monkeypatch, task):
    schedules, registered = preflight.beat_schedules()
    worker = {k: v for k, v in schedules["worker"].items() if v["task"] != task}
    monkeypatch.setattr(
        preflight, "beat_schedules", lambda: ({**schedules, "worker": worker}, registered)
    )
    row = _one("proactive_beat", preflight.check_proactive())
    assert row.status == FAIL
    assert f"{task} not on the worker beat" in row.detail


def test_beat_fails_when_the_heartbeat_is_too_slow(monkeypatch):
    schedules, registered = preflight.beat_schedules()
    worker = {
        key: (
            {**entry, "schedule": 300.0} if entry["task"] == "proactive.tasks.heartbeat" else entry
        )
        for key, entry in schedules["worker"].items()
    }
    monkeypatch.setattr(
        preflight, "beat_schedules", lambda: ({**schedules, "worker": worker}, registered)
    )
    row = _one("proactive_beat", preflight.check_proactive())
    assert row.status == FAIL
    assert "runs less often than every 60s" in row.detail


# --- proactive_schedules ------------------------------------------------------


def test_schedules_pass_with_a_future_next_run(owner, workspace):
    _schedule(workspace, owner)
    _schedule(workspace, owner, kind="heartbeat")
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.status == PASS
    assert row.detail == "2 enabled: heartbeat@personal, morning_digest@personal"


def test_a_disabled_schedule_is_not_counted(owner, workspace):
    schedule = _schedule(workspace, owner)
    ProactiveSchedule.objects.filter(pk=schedule.pk).update(enabled=False)
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.status == WARN and "no schedule enabled" in row.detail


def test_a_stale_next_run_fails(owner, workspace):
    _schedule(workspace, owner, next_run_at=timezone.now() - timedelta(minutes=30))
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.status == FAIL
    assert "overdue (is the heartbeat running?): morning_digest@personal" in row.detail


def test_a_just_due_next_run_is_not_stale(owner, workspace):
    _schedule(workspace, owner, next_run_at=timezone.now() - timedelta(minutes=2))
    assert _one("proactive_schedules", preflight.check_proactive()).status == PASS


def test_an_incomplete_schedule_fails(owner, workspace):
    schedule = _schedule(workspace, owner)
    ProactiveSchedule.objects.filter(pk=schedule.pk).update(next_run_at=None)
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.status == FAIL and "no next run (incomplete)" in row.detail


def test_a_paused_workspace_is_noted_not_failed(owner, workspace):
    _schedule(workspace, owner)
    ProactivePause.objects.create(workspace=workspace, paused_by=owner)
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.status == PASS and "(1 workspace(s) paused)" in row.detail


def test_a_firm_schedule_is_labelled_by_type_only(owner, workspace):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    firm = make_firm(owner, member)
    _schedule(firm, owner)
    row = _one("proactive_schedules", preflight.check_proactive())
    assert row.detail == "1 enabled: morning_digest@company"
    assert firm.name not in row.detail


# --- proactive_last_run -------------------------------------------------------


def test_last_run_passes_after_a_good_run(owner, workspace):
    schedule = _schedule(workspace, owner)
    _run(schedule, outcome=ProactiveRun.Outcome.FAILED, started_at=timezone.now() - timedelta(1))
    _run(schedule, outcome=ProactiveRun.Outcome.DIGEST)
    row = _one("proactive_last_run", preflight.check_proactive())
    assert (row.status, row.detail) == (PASS, "morning_digest@personal digest")


def test_last_run_fails_when_the_latest_failed(owner, workspace):
    schedule = _schedule(workspace, owner)
    _run(schedule, outcome=ProactiveRun.Outcome.DIGEST, started_at=timezone.now() - timedelta(1))
    _run(schedule, outcome=ProactiveRun.Outcome.FAILED)
    row = _one("proactive_last_run", preflight.check_proactive())
    assert (row.status, row.detail) == (FAIL, "last run failed: morning_digest@personal")


def test_a_run_stuck_running_warns(owner, workspace):
    schedule = _schedule(workspace, owner, kind="heartbeat")
    _run(
        schedule,
        outcome=ProactiveRun.Outcome.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )
    row = _one("proactive_last_run", preflight.check_proactive())
    assert row.status == WARN and "running for more than 15 min: heartbeat@personal" in row.detail


def test_a_schedule_with_no_run_yet_warns(owner, workspace):
    _schedule(workspace, owner)
    row = _one("proactive_last_run", preflight.check_proactive())
    assert (row.status, row.detail) == (WARN, "no run yet: morning_digest@personal")


# --- proactive_digest ---------------------------------------------------------


def test_digest_passes_when_seeded():
    row = _one("proactive_digest", preflight.check_proactive())
    assert row.status == PASS
    assert row.detail.startswith("proactive.digest (cheap); proactive: monthly 3 USD")


def test_digest_fails_without_prompt_tier_or_budget(settings):
    PromptTemplate.objects.filter(name="friday_proactive_digest").delete()
    AIBudget.objects.filter(purposes__contains=["proactive.digest"]).update(is_active=False)
    settings.AI_PURPOSE_TIERS = {}
    row = _one("proactive_digest", preflight.check_proactive())
    assert row.status == FAIL
    assert "proactive.digest has no tier in AI_PURPOSE_TIERS" in row.detail
    assert "no active budget covers proactive.digest" in row.detail
    assert "friday_proactive_digest is not seeded" in row.detail


def test_digest_warns_without_a_monthly_cap():
    AIBudget.objects.filter(purposes__contains=["proactive.digest"]).update(monthly_limit_usd=None)
    row = _one("proactive_digest", preflight.check_proactive())
    assert row.status == WARN and row.detail.startswith("no monthly cap on proactive.digest")


# --- proactive_probes ---------------------------------------------------------


def test_probes_pass_with_the_boot_registry():
    row = _one("proactive_probes", preflight.check_proactive())
    assert row.status == PASS
    assert row.detail.startswith(f"{len(probes.list_probes())} probes registered")


def test_a_slow_or_failing_probe_on_the_last_run_warns(owner, workspace):
    schedule = _schedule(workspace, owner, kind="heartbeat")
    _run(
        schedule,
        outcome=ProactiveRun.Outcome.NOTICES,
        slow_probes=["tasks.due_today"],
        probe_failures={"memory.calendar_today": "timeout", "nodes.link": "denied"},
    )
    row = _one("proactive_probes", preflight.check_proactive())
    assert row.status == WARN
    assert "slow on the last run: tasks.due_today" in row.detail
    assert "failed on the last run: memory.calendar_today=timeout" in row.detail
    assert "nodes.link" not in row.detail  # a denial is the guard working


def test_no_probe_registered_fails(monkeypatch):
    monkeypatch.setattr(probes, "_REGISTRY", {})
    row = _one("proactive_probes", preflight.check_proactive())
    assert (row.status, row.detail) == (FAIL, "no probe registered")


def test_one_crashing_proactive_check_is_its_own_fail_row(monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(preflight, "beat_schedules", broken)
    results = preflight.check_proactive()
    assert _one("proactive_beat", results).status == FAIL
    assert _one("proactive_beat", results).detail == "check crashed: RuntimeError"
    assert len(results) == 5


def test_a_database_failure_fails_the_schedule_rows(monkeypatch):
    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(preflight, "_enabled_schedules", broken)
    results = preflight.check_proactive()
    statuses = {r.name: r.status for r in results}
    assert statuses["proactive_schedules"] == statuses["proactive_last_run"] == FAIL
    assert statuses["proactive_probes"] == FAIL
    assert statuses["proactive_digest"] == PASS


# --- n8n ----------------------------------------------------------------------


def _enable_n8n(workspace):
    return ConnectorBinding.objects.create(workspace=workspace, connector="n8n", enabled=True)


def test_allowlist_fails_when_the_relay_ceiling_lists_another_workflow(settings):
    settings.N8N_RELAY_ALLOWED_TOOLS = REFERENCE_TOOLS + ",wf_Zz99Yy88Xx77Ww66"
    row = _one("n8n_allowlist", preflight.check_n8n())
    assert row.status == FAIL
    assert "not in the manifest: wf_Zz99Yy88Xx77Ww66" in row.detail


def test_allowlist_fails_when_the_relay_ceiling_misses_one(settings):
    settings.N8N_RELAY_ALLOWED_TOOLS = "wf_Ab12Cd34Ef56Gh78"
    row = _one("n8n_allowlist", preflight.check_n8n())
    assert row.status == FAIL and "missing: wf_Hb34Kd56Mf78Np90" in row.detail


def test_allowlist_fails_when_empty(settings):
    settings.N8N_RELAY_ALLOWED_TOOLS = ""
    row = _one("n8n_allowlist", preflight.check_n8n())
    assert row.status == FAIL and "N8N_RELAY_ALLOWED_TOOLS is empty" in row.detail


def test_allowlist_ignores_order_and_spaces(settings):
    settings.N8N_RELAY_ALLOWED_TOOLS = " wf_Hb34Kd56Mf78Np90 , wf_Ab12Cd34Ef56Gh78 "
    assert _one("n8n_allowlist", preflight.check_n8n()).status == PASS


def test_the_committed_defaults_equal_the_manifest():
    """The compose defaults of the sidecar's `BRIDGE_ALLOWED_TOOLS`, of the
    web/worker `N8N_RELAY_ALLOWED_TOOLS` and the settings default are the
    allowlist `manage.py n8n_relay_env` prints."""
    from pathlib import Path

    from connectors.manifests.n8n import relay_allowed_tools

    expected = relay_allowed_tools()
    assert expected == REFERENCE_TOOLS
    compose = (
        Path(preflight.settings.BASE_DIR).resolve().parent / "deploy" / "docker" / "compose.yml"
    ).read_text(encoding="utf-8")
    line = f'"${{N8N_RELAY_ALLOWED_TOOLS:-{expected}}}"'
    assert f"BRIDGE_ALLOWED_TOOLS: {line}" in compose
    assert f"N8N_RELAY_ALLOWED_TOOLS: {line}" in compose
    settings_source = (
        Path(preflight.settings.BASE_DIR) / "config" / "settings" / "base.py"
    ).read_text(encoding="utf-8")
    assert f'"N8N_RELAY_ALLOWED_TOOLS", default="{expected}"' in settings_source


def test_an_enabled_binding_without_the_relay_url_fails(workspace):
    _enable_n8n(workspace)
    results = preflight.check_n8n()
    assert _one("n8n_binding", results).status == PASS
    relay = _one("n8n_relay", results)
    assert relay.status == FAIL and "N8N_MCP_URL is not set" in relay.detail
    assert _one("n8n_pins", results).status == WARN


def test_an_enabled_binding_in_a_firm_workspace_fails(owner):
    member = register_user(email="member@test.dev", password=PASSWORD, name="Member")
    firm = make_firm(owner, member)
    _enable_n8n(firm)
    row = _one("n8n_binding", preflight.check_n8n())
    assert row.status == FAIL and "outside a personal workspace" in row.detail


def test_an_unreachable_relay_fails_with_its_code_only(workspace, monkeypatch):
    _enable_n8n(workspace)
    monkeypatch.setenv("N8N_MCP_URL", "http://n8n-relay:8080/mcp")

    def down(manifest, *, timeout):
        raise ConnectorError("transport_error") from OSError("http://secret-host/mcp refused")

    monkeypatch.setattr(preflight, "pin_report", down)
    results = preflight.check_n8n()
    relay = _one("n8n_relay", results)
    assert (relay.status, relay.detail) == (
        FAIL,
        "tools/list through the relay failed: transport_error",
    )
    assert "secret-host" not in " ".join(r.detail for r in results)
    assert _one("n8n_pins", results).status == WARN


def test_a_crashing_relay_is_reported_by_class(workspace, monkeypatch):
    _enable_n8n(workspace)
    monkeypatch.setenv("N8N_MCP_URL", "http://n8n-relay:8080/mcp")

    def crash(manifest, *, timeout):
        raise OSError("Bearer abc")

    monkeypatch.setattr(preflight, "pin_report", crash)
    relay = _one("n8n_relay", preflight.check_n8n())
    assert relay.detail == "tools/list through the relay failed: OSError"


def test_a_pin_mismatch_fails(workspace, monkeypatch):
    _enable_n8n(workspace)
    monkeypatch.setenv("N8N_MCP_URL", "http://n8n-relay:8080/mcp")
    monkeypatch.setattr(
        preflight,
        "pin_report",
        lambda manifest, *, timeout: {"n8n_server_status": "ok", "n8n_home_backup": "mismatch"},
    )
    results = preflight.check_n8n()
    assert _one("n8n_relay", results).status == PASS
    pins = _one("n8n_pins", results)
    assert (pins.status, pins.detail) == (FAIL, "pins differ: n8n_home_backup=mismatch")


def test_the_live_check_passes_against_the_fake_trigger_behind_the_relay(
    workspace, monkeypatch, n8n
):
    """The real `pin_report` (`live_pin_report`) and MCP client, through the
    in-process relay to the fake n8n trigger -- nothing stored on the
    binding, nothing audited."""
    from connectors.services import live_pin_report

    binding = _enable_n8n(workspace)
    monkeypatch.setattr(preflight, "pin_report", live_pin_report)
    results = preflight.check_n8n()
    assert {r.name: r.status for r in results} == {
        "n8n_allowlist": PASS,
        "n8n_binding": PASS,
        "n8n_relay": PASS,
        "n8n_pins": PASS,
    }
    assert _one("n8n_pins", results).detail == "2 tool pin(s) match"
    binding.refresh_from_db()
    assert binding.last_health == {}
    assert n8n.calls == []


def test_an_unregistered_manifest_fails(monkeypatch):
    monkeypatch.setattr("connectors.manifest.get_manifest", lambda name: None)
    results = preflight.check_n8n()
    assert [(r.name, r.status) for r in results] == [("n8n_allowlist", FAIL)]


# --- skills -------------------------------------------------------------------


def test_skills_ai_passes_when_seeded():
    row = _one("skills_ai", preflight.check_skills())
    assert row.status == PASS
    assert row.detail.startswith(
        "skills.match (cheap), skills.curate (mid), skills.playbook_distill (mid); "
    )


def test_skills_ai_fails_on_a_missing_prompt():
    PromptTemplate.objects.filter(name="friday_skills_curate").delete()
    row = _one("skills_ai", preflight.check_skills())
    assert (row.status, row.detail) == (
        FAIL,
        "friday_skills_curate is not seeded (run seed_ai_defaults)",
    )


def test_skills_ai_fails_on_a_missing_budget():
    for budget in AIBudget.objects.all():
        if "skills.match" in (budget.purposes or []):
            budget.is_active = False
            budget.save(update_fields=["is_active"])
    row = _one("skills_ai", preflight.check_skills())
    assert row.status == FAIL and "no active budget covers skills.match" in row.detail


@pytest.mark.parametrize("task", ["skills.tasks.curate", "skills.tasks.distill_playbooks"])
def test_skills_beat_fails_without_an_entry(monkeypatch, task):
    schedules, registered = preflight.beat_schedules()
    worker = {k: v for k, v in schedules["worker"].items() if v["task"] != task}
    monkeypatch.setattr(
        preflight, "beat_schedules", lambda: ({**schedules, "worker": worker}, registered)
    )
    row = _one("skills_beat", preflight.check_skills())
    assert row.status == FAIL and f"{task} not on the worker beat" in row.detail


def _active(workspace, owner, text=None):
    parsed = skill_services.parse_text(text or skill_text())
    version = skill_services.propose_from_owner(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, parsed=parsed, origin="upload"
    )
    skill_services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256=version.content_sha256
    )
    version.skill.refresh_from_db()
    return version


def test_parser_passes_on_active_and_stale_skills(owner, workspace):
    _active(workspace, owner)
    stale = _active(workspace, owner, skill_text(name="eski"))
    InstructionSkill.objects.filter(pk=stale.skill_id).update(status=InstructionSkill.Status.STALE)
    row = _one("skills_parser", preflight.check_skills())
    assert (row.status, row.detail) == (PASS, "2 usable skill(s) pass the current parser")


def test_a_parser_tightening_surfaces_an_old_active_version(owner, workspace, settings):
    _active(workspace, owner, skill_text(body="Uzun bir gövde. " * 20))
    _active(workspace, owner, skill_text(name="kisa", body="Kısa."))
    settings.SKILLS_BODY_MAX_CHARS = 100  # the parser got stricter after approval
    row = _one("skills_parser", preflight.check_skills())
    assert row.status == FAIL
    assert row.detail == (
        "1 of 2 usable skill(s) fail the current parser (archive or re-approve): "
        "pr-yaz=body_too_large"
    )
    assert "Uzun" not in row.detail


def test_proposed_archived_and_rejected_skills_are_not_checked(owner, workspace, settings):
    archived = _active(workspace, owner, skill_text(body="Uzun bir gövde. " * 20))
    InstructionSkill.objects.filter(pk=archived.skill_id).update(
        status=InstructionSkill.Status.ARCHIVED
    )
    skill_services.propose_from_owner(
        actor=owner,
        channel=CHANNEL_WEB,
        workspace=workspace,
        parsed=skill_services.parse_text(skill_text(name="yeni", body="Uzun bir gövde. " * 20)),
        origin="upload",
    )
    settings.SKILLS_BODY_MAX_CHARS = 100
    assert InstructionSkillVersion.objects.count() == 2
    row = _one("skills_parser", preflight.check_skills())
    assert (row.status, row.detail) == (PASS, "no active skill")


def test_no_reach_check_calls_a_model_or_writes(owner, workspace):
    _schedule(workspace, owner)
    _active(workspace, owner)
    _enable_n8n(workspace)
    counts = {
        "usage": ModelUsage.objects.count(),
        "runs": ProactiveRun.objects.count(),
        "versions": InstructionSkillVersion.objects.count(),
    }
    preflight.run_preflight(
        model_calls=True,
        checks=(
            ("proactive", preflight.check_proactive),
            ("n8n", preflight.check_n8n),
            ("skills", preflight.check_skills),
        ),
    )
    assert counts == {
        "usage": ModelUsage.objects.count(),
        "runs": ProactiveRun.objects.count(),
        "versions": InstructionSkillVersion.objects.count(),
    }
