"""`manage.py preflight` control checks (Phase 10 closeout): `node_grant_config`,
`node_protocol_v2`, `node_conformance_v2`, `node_escape_suite`,
`node_output_scrubber`, `trust_check`, `node_action_actor` and
`harness_drivers` -- each passes, fails and warns with fakes. Every outside
contact is a seam (`http_get` for the gateway's /healthz,
`node_conformance_drift` / `node_conformance_case_counts` for the case files,
`escape_suite_report` for the escape run, `approve_request_source` for the
source test, `beat_schedules` for the beat): no test reaches a gateway, a
worker, a Mac, `srt` or a model."""

import datetime
import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.management import call_command

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from accounts.services import register_user
from ai_core.models import AIBudget, ModelUsage, PromptTemplate, PromptVersion
from harness import drivers
from nodes import bus
from nodes.protocol.messages import catalogue_sha256
from nodes.reference.node import default_catalogue
from nodes.services import pair_node, record_capability_snapshot
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from tools import node_action_ports, registry

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
GATEWAY_URL = "http://node-gateway:8002/healthz"
EVIDENCE_PENDING = (
    "32/32 contained with the fake executor; Mac evidence (ADR-014, M3) pending owner evaluation"
)


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def paired(owner):
    made = []

    def make(name="MacBook", *, protocol_version=2, catalogue=None, present=True):
        raw_code, _ = devices.issue_pairing_code(
            owner=owner,
            kind=DevicePairingCode.Kind.STRONG,
            issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
            device_kind=TrustedDevice.Kind.NODE,
        )
        node = pair_node(raw_code=raw_code, node_name=name, protocol_versions=[1]).node
        made.append(node)
        if catalogue is not False:
            catalogue = default_catalogue() if catalogue is None else catalogue
            record_capability_snapshot(
                node=node,
                catalogue=catalogue,
                catalogue_sha256=catalogue_sha256(catalogue),
                protocol_version=protocol_version,
            )
        if present:
            bus.mark_present(node.pk)
        return node

    yield make
    for node in made:
        bus.clear_presence(node.pk)


@pytest.fixture
def seams(monkeypatch, settings):
    """A healthy control stack: the gateway reports its scrubber, the case
    files are current, the escape suite contains every case, the real trust
    check and evidence source are registered (as `AssistantConfig.ready()`
    does at startup), its prompt is seeded and the `control` budget is
    capped."""
    settings.PREFLIGHT_NODE_GATEWAY_URL = GATEWAY_URL
    settings.NODE_ESCAPE_EVIDENCE_RECORDED_ON = ""
    state = {
        "healthz": (
            200,
            {
                "status": "ok",
                "redis": True,
                "connected_nodes": 0,
                "signing_key_id": "0123456789abcdef",
                "output_scrubber": True,
            },
        ),
        "drift": [],
        "counts": {1: 49, 2: 91},
        "escape": (True, 32, 32),
        "calls": [],
    }

    def http_get(url, *, timeout):
        state["calls"].append(("http", url))
        value = state["healthz"]
        if isinstance(value, Exception):
            raise value
        status, body = value
        return status, body if isinstance(body, str) else json.dumps(body)

    def escape():
        state["calls"].append(("escape",))
        return state["escape"]

    monkeypatch.setattr(preflight, "http_get", http_get)
    monkeypatch.setattr(preflight, "node_conformance_drift", lambda: list(state["drift"]))
    monkeypatch.setattr(preflight, "node_conformance_case_counts", lambda: dict(state["counts"]))
    monkeypatch.setattr(preflight, "escape_suite_report", escape)
    ports = node_action_ports.snapshot_ports()
    apps.get_app_config("assistant").ready()
    template, _ = PromptTemplate.objects.get_or_create(
        name="friday_control_trust_check", defaults={"purpose": "control.trust_check"}
    )
    version, _ = PromptVersion.objects.get_or_create(
        template=template, version=1, defaults={"content": "Compare."}
    )
    template.active_version = version
    template.save(update_fields=["active_version"])
    AIBudget.objects.update_or_create(
        name="control",
        defaults={
            "purposes": ["control.trust_check"],
            "monthly_limit_usd": Decimal("5"),
            "is_active": True,
        },
    )
    yield state
    node_action_ports.restore_ports(ports)


def _control():
    return {r.name: r for r in preflight.check_control()}


def _healthz(state, **changes):
    status, body = state["healthz"]
    state["healthz"] = (status, {**body, **changes})


def _gate_confirm_counts(node):
    from nodes.selectors import latest_snapshot

    entries = [e for e in latest_snapshot(node).entries if e["invocable"]]
    return (
        sum(1 for e in entries if e["class"] == "gate"),
        sum(1 for e in entries if e["class"] == "confirm"),
    )


# --- the whole section -------------------------------------------------------


def test_a_healthy_control_stack_is_green_with_the_mac_rows_pending(seams, paired):
    node = paired()
    gate, confirm = _gate_confirm_counts(node)
    assert gate and confirm
    results = preflight.check_control()
    assert [(r.name, r.status) for r in results] == [
        ("node_grant_config", PASS),
        ("node_protocol_v2:MacBook", PASS),
        ("node_conformance_v2", PASS),
        ("node_escape_suite", WARN),
        ("node_output_scrubber", PASS),
        ("trust_check", PASS),
        ("node_action_actor", PASS),
        ("harness_drivers:codex_cli", PASS),
        ("harness_drivers:claude_code", PASS),
    ]
    by_name = {r.name: r for r in results}
    assert by_name["node_protocol_v2:MacBook"].detail == (
        f"connected, protocol v2, {gate} gate / {confirm} confirm invocable"
    )
    assert by_name["node_escape_suite"].detail == EVIDENCE_PENDING
    assert by_name["node_grant_config"].detail == (
        "grant TTL 90s, dispatch delay 60s, node_action approval 600s, "
        "3 control sweeps on the worker beat"
    )
    # Read-only: no model call.
    assert not ModelUsage.objects.exists()


def test_the_control_section_is_registered_after_the_node_checks():
    names = [name for name, _ in preflight.CHECKS]
    assert names.index("control") == names.index("nodes") + 1


def test_one_crashing_control_check_is_its_own_fail_row(seams, monkeypatch):
    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(preflight, "_check_trust_check", boom)
    monkeypatch.setattr(preflight, "_check_harness_drivers", boom)
    results = _control()
    assert results["trust_check"].status == FAIL
    assert results["trust_check"].detail == "check crashed: RuntimeError"
    assert results["harness_drivers"].detail == "check crashed: RuntimeError"
    assert results["node_action_actor"].status == PASS


# --- node_grant_config -------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "value", "detail"),
    [
        ("NODE_GRANT_TTL_SECONDS", 121, "NODE_GRANT_TTL_SECONDS=121 is outside 70..120"),
        ("NODE_GRANT_TTL_SECONDS", 69, "NODE_GRANT_TTL_SECONDS=69 is outside 70..120"),
        ("NODE_GRANT_TTL_SECONDS", "90", "NODE_GRANT_TTL_SECONDS=None is outside 70..120"),
        (
            "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS",
            61,
            "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS=61 is outside 1..60",
        ),
        (
            "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS",
            0,
            "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS=0 is outside 1..60",
        ),
    ],
)
def test_grant_config_fails_outside_the_bounds(seams, settings, setting, value, detail):
    setattr(settings, setting, value)
    result = _control()["node_grant_config"]
    assert (result.status, result.detail) == (FAIL, detail)


def test_grant_config_passes_at_the_bounds(seams, settings):
    for ttl in (70, 120):
        settings.NODE_GRANT_TTL_SECONDS = ttl
        settings.NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS = 1
        assert _control()["node_grant_config"].status == PASS


def test_grant_config_fails_without_the_node_action_tool(seams, monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_tool",
        lambda name: None if name == "node_action" else registry._REGISTRY.get(name),
    )
    result = _control()["node_grant_config"]
    assert result.status == FAIL
    assert "the node_action tool is not registered" in result.detail


def test_grant_config_fails_when_a_global_cap_cuts_the_approval_ttl(seams, settings):
    settings.APPROVAL_REQUEST_TTL_HOURS = 0
    result = _control()["node_grant_config"]
    assert result.status == FAIL
    assert "a node_action approval lives 0s, not 600s" in result.detail


def _beat(monkeypatch, *, worker=None, registered=None):
    schedules, real_registered = preflight.beat_schedules()
    worker = schedules["worker"] if worker is None else worker
    registered = real_registered if registered is None else registered
    monkeypatch.setattr(
        preflight, "beat_schedules", lambda: ({**schedules, "worker": worker}, registered)
    )
    return schedules["worker"], real_registered


def test_grant_config_fails_without_the_approval_sweep(seams, monkeypatch):
    schedules, _ = preflight.beat_schedules()
    worker = {
        key: entry
        for key, entry in schedules["worker"].items()
        if entry["task"] != "approvals.tasks.sweep_expired_approval_requests"
    }
    _beat(monkeypatch, worker=worker)
    result = _control()["node_grant_config"]
    assert result.status == FAIL
    assert result.detail == "approvals.tasks.sweep_expired_approval_requests not on the worker beat"


def test_grant_config_fails_when_a_sweep_runs_too_rarely(seams, monkeypatch):
    schedules, _ = preflight.beat_schedules()
    worker = {
        key: (
            {**entry, "schedule": 300.0}
            if entry["task"] == "nodes.tasks.sweep_node_actions_task"
            else entry
        )
        for key, entry in schedules["worker"].items()
    }
    _beat(monkeypatch, worker=worker)
    result = _control()["node_grant_config"]
    assert result.status == FAIL
    assert result.detail == "nodes.tasks.sweep_node_actions_task runs less often than every 60s"


def test_grant_config_fails_when_a_sweep_task_is_not_registered(seams, monkeypatch):
    _, registered = preflight.beat_schedules()
    _beat(monkeypatch, registered=registered - {"nodes.tasks.clear_node_action_tails_task"})
    result = _control()["node_grant_config"]
    assert result.status == FAIL
    assert result.detail == "nodes.tasks.clear_node_action_tails_task not registered"


def test_grant_config_fails_without_any_beat(seams, monkeypatch):
    monkeypatch.setattr(preflight, "beat_schedules", lambda: ({}, set()))
    result = _control()["node_grant_config"]
    assert result.detail == "no worker beat schedule published by the Celery app"


def test_schedule_seconds_reads_floats_and_celery_schedules():
    from celery.schedules import crontab, schedule

    assert preflight._schedule_seconds(60.0) == 60.0
    assert preflight._schedule_seconds(schedule(datetime.timedelta(seconds=30))) == 30.0
    assert preflight._schedule_seconds(crontab(minute="*")) is None
    assert preflight._schedule_seconds(True) is None


# --- node_protocol_v2 ----------------------------------------------------------


def test_protocol_v2_warns_without_a_paired_node(seams):
    result = _control()["node_protocol_v2"]
    assert (result.status, result.detail) == (
        WARN,
        "no node paired (pending owner evaluation (Mac session))",
    )


def test_protocol_v2_warns_when_no_node_speaks_v2(seams, paired):
    paired("Old Mac", protocol_version=1)
    results = _control()
    row = results["node_protocol_v2:Old Mac"]
    assert row.status == WARN
    assert row.detail == (
        "connected, protocol v1, 0 gate / 0 confirm invocable (actions need a v2 session)"
    )
    aggregate = results["node_protocol_v2"]
    assert (aggregate.status, aggregate.detail) == (
        WARN,
        "no node speaks protocol v2 (pending owner evaluation (Mac session))",
    )


def test_protocol_v2_lists_every_active_node(seams, paired):
    paired("MacBook")
    paired("Old Mac", protocol_version=1)
    paired("Fresh Mac", catalogue=False, present=False)
    results = _control()
    assert results["node_protocol_v2:MacBook"].status == PASS
    assert results["node_protocol_v2:Old Mac"].status == WARN
    fresh = results["node_protocol_v2:Fresh Mac"]
    assert (fresh.status, fresh.detail) == (WARN, "disconnected, no catalogue yet")
    # One node speaks v2: no aggregate warning.
    assert "node_protocol_v2" not in results


def test_protocol_v2_warns_when_a_v2_node_offers_no_action(seams, paired):
    reads_only = [e for e in default_catalogue() if e["class"] == "read"]
    paired(catalogue=reads_only)
    row = _control()["node_protocol_v2:MacBook"]
    assert row.status == WARN
    assert row.detail.endswith("0 gate / 0 confirm invocable (no action invocable)")


def test_protocol_v2_skips_a_revoked_node(seams, paired, owner):
    node = paired()
    devices.revoke_device(device=node.device, actor=owner)
    result = _control()["node_protocol_v2"]
    assert result.status == WARN and result.detail.startswith("no node paired")


# --- node_conformance_v2 -------------------------------------------------------


def test_conformance_v2_passes_with_current_files(seams):
    result = _control()["node_conformance_v2"]
    assert (result.status, result.detail) == (
        PASS,
        "49 v1 + 91 v2 cases and vectors/sandbox.json current",
    )


@pytest.mark.parametrize(
    "drift",
    [["cases/v2/v2_gate_happy.json"], ["cases/v1/v1_read.json"], ["vectors/sandbox.json"]],
)
def test_conformance_v2_fails_on_stale_case_or_sandbox_files(seams, drift):
    seams["drift"] = drift
    result = _control()["node_conformance_v2"]
    assert (result.status, result.detail) == (
        FAIL,
        f"stale: {drift[0]} (run build_node_conformance)",
    )


def test_conformance_v2_leaves_the_other_vectors_to_node_conformance(seams):
    seams["drift"] = ["vectors/scrub.json", "reference/_scrubber_vendored.py"]
    assert _control()["node_conformance_v2"].status == PASS


def test_conformance_v2_shortens_a_long_drift_list(seams):
    seams["drift"] = [f"cases/v2/case_{i}.json" for i in range(7)]
    result = _control()["node_conformance_v2"]
    assert result.status == FAIL
    assert result.detail == (
        "stale: cases/v2/case_0.json, cases/v2/case_1.json, cases/v2/case_2.json, "
        "cases/v2/case_3.json, cases/v2/case_4.json (+2 more) (run build_node_conformance)"
    )


def test_conformance_v2_fails_without_v2_cases(seams):
    seams["counts"] = {1: 49, 2: 0}
    result = _control()["node_conformance_v2"]
    assert (result.status, result.detail) == (FAIL, "no v2 conformance cases defined")


def test_the_case_count_seam_reads_the_suite():
    counts = preflight.node_conformance_case_counts()
    assert set(counts) == {1, 2} and counts[1] > 0 and counts[2] > 0


# --- node_escape_suite -------------------------------------------------------


def test_escape_suite_warns_while_the_mac_evidence_is_pending(seams):
    result = _control()["node_escape_suite"]
    assert (result.status, result.detail) == (WARN, EVIDENCE_PENDING)
    assert ("escape",) in seams["calls"]


def test_escape_suite_passes_once_the_evidence_is_recorded(seams, settings):
    settings.NODE_ESCAPE_EVIDENCE_RECORDED_ON = "2026-12-01"
    result = _control()["node_escape_suite"]
    assert (result.status, result.detail) == (
        PASS,
        "32/32 contained with the fake executor; Mac evidence (ADR-014, M3) recorded 2026-12-01",
    )


def test_escape_suite_fails_on_a_malformed_evidence_date(seams, settings):
    settings.NODE_ESCAPE_EVIDENCE_RECORDED_ON = "soon"
    result = _control()["node_escape_suite"]
    assert result.status == FAIL
    assert result.detail.endswith("NODE_ESCAPE_EVIDENCE_RECORDED_ON is not an ISO date")


def test_escape_suite_fails_when_a_case_escapes(seams, settings):
    settings.NODE_ESCAPE_EVIDENCE_RECORDED_ON = "2026-12-01"
    seams["escape"] = (False, 31, 32)
    result = _control()["node_escape_suite"]
    assert (result.status, result.detail) == (
        FAIL,
        "31/32 contained with the fake executor (run node_escape_suite)",
    )


def test_the_escape_seam_runs_the_fake_suite():
    """The real seam: `node_escape_suite --executor fake` in process."""
    passed, contained, total = preflight.escape_suite_report()
    assert passed and contained == total >= 32


# --- node_output_scrubber ----------------------------------------------------


def test_output_scrubber_passes_when_the_gateway_reports_it(seams):
    result = _control()["node_output_scrubber"]
    assert (result.status, result.detail) == (
        PASS,
        "the node-gateway has its output scrubber registered",
    )


def test_output_scrubber_fails_when_the_gateway_has_none(seams):
    _healthz(seams, output_scrubber=False)
    result = _control()["node_output_scrubber"]
    assert result.status == FAIL
    assert "fail-closed marker" in result.detail


def test_output_scrubber_fails_when_the_gateway_does_not_say(seams):
    status, body = seams["healthz"]
    seams["healthz"] = (status, {k: v for k, v in body.items() if k != "output_scrubber"})
    result = _control()["node_output_scrubber"]
    assert (result.status, result.detail) == (
        FAIL,
        "the node-gateway's /healthz does not report its scrubber",
    )


def test_output_scrubber_warns_without_a_gateway_url(seams, settings):
    settings.PREFLIGHT_NODE_GATEWAY_URL = ""
    result = _control()["node_output_scrubber"]
    assert (result.status, result.detail) == (
        WARN,
        "not checked: PREFLIGHT_NODE_GATEWAY_URL not set",
    )


def test_output_scrubber_fails_when_the_gateway_is_unreachable(seams):
    seams["healthz"] = ConnectionError("refused: http://user:pw@node-gateway")
    result = _control()["node_output_scrubber"]
    assert result.status == FAIL
    assert result.detail == "not checked: unreachable: ConnectionError"


# --- trust_check -------------------------------------------------------------


def test_trust_check_passes_with_the_real_check(seams):
    result = _control()["trust_check"]
    assert result.status == PASS
    assert result.detail == (
        "assistant.trust_check.check_node_action, tier cheap, control: monthly 5 USD"
    )


def test_trust_check_fails_while_the_branch3_stub_is_registered(seams):
    node_action_ports.register_trust_check(node_action_ports.pending_trust_check)
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (
        FAIL,
        "the branch-3 stub is the registered trust check (warn:trust_check_pending)",
    )


def test_trust_check_fails_when_nothing_is_registered(seams):
    node_action_ports.reset_ports()
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (
        FAIL,
        "no trust check registered (every node_action is blocked)",
    )


def test_trust_check_fails_without_the_evidence_source(seams, monkeypatch):
    monkeypatch.setattr(node_action_ports, "_evidence_source", None)
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (FAIL, "no evidence source registered")


def test_trust_check_fails_without_a_tier(seams, settings):
    settings.AI_PURPOSE_TIERS = {
        k: v for k, v in settings.AI_PURPOSE_TIERS.items() if k != "control.trust_check"
    }
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (
        FAIL,
        "control.trust_check has no tier in AI_PURPOSE_TIERS",
    )


def test_trust_check_fails_without_a_budget(seams):
    AIBudget.objects.filter(name="control").update(is_active=False)
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (
        FAIL,
        "no active budget covers control.trust_check (run migrate)",
    )


def test_trust_check_fails_without_the_prompt(seams):
    PromptTemplate.objects.filter(name="friday_control_trust_check").delete()
    result = _control()["trust_check"]
    assert (result.status, result.detail) == (
        FAIL,
        "friday_control_trust_check is not seeded (run seed_ai_defaults)",
    )


def test_trust_check_warns_on_an_uncapped_budget(seams):
    AIBudget.objects.filter(name="control").update(monthly_limit_usd=None)
    result = _control()["trust_check"]
    assert result.status == WARN
    assert result.detail.endswith("control: no caps (no monthly cap)")


# --- node_action_actor ---------------------------------------------------------


def test_actor_check_passes_on_the_real_tool_and_service(seams):
    result = _control()["node_action_actor"]
    assert (result.status, result.detail) == (
        PASS,
        "precheck_request and authorize_decision declared; approve_request calls the hook",
    )


@pytest.mark.parametrize("missing", ["precheck_request", "authorize_decision"])
def test_actor_check_fails_when_a_hook_is_missing(seams, monkeypatch, missing):
    real = registry.get_tool("node_action")
    fake = SimpleNamespace(
        precheck_request=real.precheck_request, authorize_decision=real.authorize_decision
    )
    setattr(fake, missing, None)
    monkeypatch.setattr(registry, "get_tool", lambda name: fake if name == "node_action" else None)
    result = _control()["node_action_actor"]
    assert (result.status, result.detail) == (FAIL, f"node_action declares no {missing}")


def test_actor_check_fails_without_the_tool(seams, monkeypatch):
    monkeypatch.setattr(registry, "get_tool", lambda name: None)
    result = _control()["node_action_actor"]
    assert (result.status, result.detail) == (FAIL, "the node_action tool is not registered")


NO_HOOK = """
def approve_request(*, approval_request, actor, channel):
    approval_request.status = "approved"
    approval_request.save()
"""
HOOK_TOO_LATE = """
def approve_request(*, approval_request, actor, channel):
    approval_request.status = "approved"
    authorize_decision(approval_request, actor)
"""
HOOK_IN_A_STRING = """
def approve_request(*, approval_request, actor, channel):
    note = "authorize_decision(approval_request, actor)"
    approval_request.status = "approved"
"""


@pytest.mark.parametrize("source", [NO_HOOK, HOOK_TOO_LATE, HOOK_IN_A_STRING])
def test_actor_check_fails_when_approve_request_skips_the_hook(seams, monkeypatch, source):
    monkeypatch.setattr(preflight, "approve_request_source", lambda: source)
    result = _control()["node_action_actor"]
    assert (result.status, result.detail) == (
        FAIL,
        "approve_request does not call authorize_decision before deciding",
    )


def test_the_source_seam_reads_the_real_service():
    source = preflight.approve_request_source()
    assert source.lstrip().startswith("def approve_request(")
    assert preflight._approve_calls_hook_before_status_change(source)


# --- harness_drivers ---------------------------------------------------------


def _driver_rows(monkeypatch, **changes):
    real = drivers.driver_status()
    rows = []
    for row in real:
        row = dict(row)
        row.update(changes.get(row["agent"], {}))
        rows.append(row)
    monkeypatch.setattr(drivers, "driver_status", lambda: rows)


def test_drivers_disabled_pass(seams):
    results = _control()
    for agent in ("codex_cli", "claude_code"):
        result = results[f"harness_drivers:{agent}"]
        assert (result.status, result.detail) == (PASS, "disabled (CLI path)")


def test_an_enabled_driver_without_d4_fails(seams, settings):
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = ""
    result = _control()["harness_drivers:codex_cli"]
    assert result.status == FAIL
    assert result.detail.startswith("app_server enabled; d4 not recorded")
    # The other agent stays on the CLI path.
    assert _control()["harness_drivers:claude_code"].status == PASS


def test_an_enabled_driver_on_a_provisional_snapshot_fails(seams, settings):
    """The shipped snapshots are provisional (owner step: pins); the system
    check only warns (`harness.W002`), the closeout preflight fails."""
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_CLAUDE = "agent_sdk"
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = "2026-11-30"
    result = _control()["harness_drivers:claude_code"]
    assert result.status == FAIL
    assert result.detail == (
        "agent_sdk enabled; the snapshot for pin 0.1.0 is provisional "
        "(run generate_driver_schemas.sh)"
    )


def test_an_enabled_driver_with_d4_and_a_real_snapshot_passes(seams, monkeypatch):
    _driver_rows(
        monkeypatch,
        codex_cli={
            "driver": "app_server",
            "enabled": True,
            "terms_checked_on": datetime.date(2026, 11, 30),
            "pin": "0.2.0",
            "provisional": False,
            "pin_errors": [],
        },
    )
    result = _control()["harness_drivers:codex_cli"]
    assert (result.status, result.detail) == (
        PASS,
        "app_server enabled, d4 2026-11-30, pin 0.2.0",
    )


def test_an_enabled_driver_with_disagreeing_pins_fails(seams, monkeypatch):
    _driver_rows(
        monkeypatch,
        codex_cli={
            "driver": "app_server",
            "enabled": True,
            "terms_checked_on": datetime.date(2026, 11, 30),
            "provisional": False,
            "pin_errors": ["HARNESS_CODEX_APP_SERVER_VERSION='0.2.0' but ..."],
        },
    )
    result = _control()["harness_drivers:codex_cli"]
    assert (result.status, result.detail) == (
        FAIL,
        "app_server enabled; 1 pin problem(s): pin, ARG and snapshot differ",
    )


def test_a_driver_selected_while_disabled_fails(seams, settings):
    settings.HARNESS_DRIVERS_ENABLED = False
    settings.HARNESS_DRIVER_CODEX = "app_server"
    result = _control()["harness_drivers:codex_cli"]
    assert (result.status, result.detail) == (
        FAIL,
        "app_server selected while HARNESS_DRIVERS_ENABLED is off",
    )


def test_an_unknown_driver_fails(seams, settings):
    settings.HARNESS_DRIVER_CLAUDE = "acp"
    result = _control()["harness_drivers:claude_code"]
    assert (result.status, result.detail) == (FAIL, "unknown driver 'acp'")


# --- the command ---------------------------------------------------------------


def test_the_command_prints_the_control_rows(seams, monkeypatch):
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda **kwargs: preflight.check_control(),
    )
    out = StringIO()
    call_command("preflight", "--no-model-calls", stdout=out)
    lines = out.getvalue().splitlines()
    assert f"WARN  node_escape_suite  {EVIDENCE_PENDING}" in lines
    assert (
        "PASS  node_output_scrubber  the node-gateway has its output scrubber registered" in lines
    )
    assert (
        "WARN  node_protocol_v2  no node paired (pending owner evaluation (Mac session))" in lines
    )
