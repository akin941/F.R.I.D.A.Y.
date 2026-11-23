"""`manage.py preflight` Phase 8B checks (closeout): `graph_integrity`,
`retrieval_indexes`, `graphify`, `compressor`, `transcripts`,
`eval_baseline`. Each passes, fails and warns with fakes; all are
read-only (no model call, no `ModelUsage`, nothing sent to a node, no
container started). No test reaches Docker, the compressor sidecar or a
node: the `docker_image_labels`, `http_get` and `transcript_node_port`
seams are replaced."""

import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.db import connection

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_WEB
from harness.models import GraphifyRun, HarnessRepo
from memory.ingestion.services import ensure_default_sources
from memory.ingestion.transcript_audit import PULL_AUDIT_ACTION
from memory.models import IngestionSource, MemoryEntity, MemoryEvalRun, MemoryFact
from memory.tests.graph_helpers import T0, T1, T2, entity, fact
from memory.tests.ingestion_helpers import add_rule
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from tools.tests.transcript_nodes import ReferenceNodePort
from tools.transcript_ingest import UnavailableNodePort, pull_transcripts
from workspaces.models import Workspace
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
PIN = "0.9.103"
LABEL = preflight.GRAPHIFY_VERSION_LABEL
CLAUDE = "claude.transcripts.owner_turns"
CODEX = "codex.transcripts.owner_turns"
NOW = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
Kind = IngestionSource.Kind


def _graph():
    return {r.name: r for r in preflight.check_graph()}


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
def firm(owner):
    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


# --- graph_integrity -------------------------------------------------------------


def test_integrity_passes_on_an_empty_graph():
    result = _graph()["graph_integrity"]
    assert (result.status, result.detail) == (PASS, "0 fact(s), 0 entit(ies) consistent")


def test_integrity_passes_on_a_superseded_chain(personal):
    api, aws, hetzner = (entity(personal, n) for n in ("api", "aws", "hetzner"))
    old = fact(personal, api, obj=aws, valid_from=T0, valid_to=T1)
    new = fact(personal, api, obj=hetzner, valid_from=T1)
    old.superseded_by = new
    old.save()
    # a trashed row is checked too, and is consistent here
    MemoryFact.all_objects.filter(pk=new.pk).update(deleted_at=T2)
    result = _graph()["graph_integrity"]
    assert (result.status, result.detail) == (PASS, "2 fact(s), 3 entit(ies) consistent")


def test_integrity_fails_on_a_fact_crossing_workspaces(personal, firm):
    api, hetzner = entity(personal, "api"), entity(personal, "hetzner")
    row = fact(personal, api, obj=hetzner)
    # Only a raw write can do it (`save()` and the fact trigger refuse):
    # move the object entity to another workspace.
    MemoryEntity.all_objects.filter(pk=hetzner.pk).update(workspace=firm)
    result = _graph()["graph_integrity"]
    assert result.status == FAIL
    assert result.detail == "1 fact(s) crossing workspaces"
    assert row.statement not in result.detail
    MemoryEntity.all_objects.filter(pk=hetzner.pk).update(workspace=personal)
    MemoryEntity.all_objects.filter(pk=api.pk).update(workspace=firm)
    assert _graph()["graph_integrity"].detail == "1 fact(s) crossing workspaces"


def test_integrity_fails_on_a_superseded_fact_left_open(personal):
    api, aws, hetzner = (entity(personal, n) for n in ("api", "aws", "hetzner"))
    old = fact(personal, api, obj=aws, valid_from=T0)
    new = fact(personal, api, obj=hetzner, valid_from=T1)
    MemoryFact.all_objects.filter(pk=old.pk).update(superseded_by=new)
    result = _graph()["graph_integrity"]
    assert result.status == FAIL
    assert result.detail == "1 superseded approved fact(s) with no valid_to"


def test_integrity_fails_on_an_inverted_window(personal):
    # The check constraint is the first guard; drop it inside the test
    # transaction (rolled back) to prove preflight is the second. Before any
    # write: the deferred fact trigger blocks DDL once it has pending events.
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE memory_memoryfact DROP CONSTRAINT IF EXISTS memory_fact_valid_window"
        )
    api = entity(personal, "api")
    row = fact(personal, api, valid_from=T1, valid_to=T2)
    MemoryFact.all_objects.filter(pk=row.pk).update(valid_to=T0)
    result = _graph()["graph_integrity"]
    assert (result.status, result.detail) == (FAIL, "1 fact(s) with valid_to before valid_from")


# --- retrieval_indexes -------------------------------------------------------------


def _embedder(name="fake-embedding-local", dimension=32):
    return ModelProvider.objects.create(
        name=name,
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        dimension=dimension,
    )


def _build_indexes():
    call_command("build_vector_indexes", stdout=StringIO())


def test_indexes_pass_with_every_hnsw_and_gin_index(settings):
    _embedder()
    _build_indexes()
    result = _graph()["retrieval_indexes"]
    assert result.status == PASS, result.detail
    assert result.detail == "mode legacy; 3/3 HNSW entr(ies) over 1 provider(s); 3/3 GIN"


def test_indexes_warn_without_an_embedding_provider():
    ModelProvider.objects.filter(capability=ModelProvider.Capability.EMBEDDING).delete()
    result = _graph()["retrieval_indexes"]
    assert result.status == WARN
    assert "no active embedding provider" in result.detail


def test_indexes_fail_when_a_provider_has_no_index():
    _embedder()  # added after the migrate-time build
    result = _graph()["retrieval_indexes"]
    assert result.status == FAIL
    assert "missing or stale: " in result.detail and "run build_vector_indexes" in result.detail


def test_indexes_warn_for_a_provider_above_the_hnsw_limit():
    _embedder(name="wide", dimension=3072)
    result = _graph()["retrieval_indexes"]
    assert result.status == WARN and "no HNSW for wide (exact scan)" in result.detail


def test_indexes_fail_when_a_gin_index_is_missing(monkeypatch):
    _embedder()
    _build_indexes()
    monkeypatch.setattr(
        preflight,
        "existing_index_names",
        lambda names: set(names) - {"memory_memoryfact_search_gin"},
    )
    result = _graph()["retrieval_indexes"]
    assert result.status == FAIL
    assert "GIN missing: memory_memoryfact_search_gin (run migrate)" in result.detail


def test_the_real_gin_lookup_finds_the_migrated_indexes():
    names = preflight.RETRIEVAL_GIN_INDEXES
    assert preflight.existing_index_names(names) == set(names)


def _eval_run(owner_workspace, **fields):
    started = fields.pop("started_at", NOW)
    defaults = {
        "run_id": uuid.uuid4(),
        "set": MemoryEvalRun.Set.PRIVATE,
        "kind": MemoryEvalRun.Kind.COMPARE,
        "retrieval_mode": "hybrid",
        "arm": MemoryEvalRun.Arm.B,
        "started_at": started,
        "finished_at": started + timedelta(minutes=3),
        "verdict": MemoryEvalRun.Verdict.PASS,
    }
    defaults.update(fields)
    return MemoryEvalRun.objects.create(workspace=owner_workspace, **defaults)


def test_hybrid_mode_warns_until_a_private_compare_passes(settings, personal):
    _embedder()
    _build_indexes()
    settings.MEMORY_RETRIEVAL_MODE = "hybrid"
    result = _graph()["retrieval_indexes"]
    assert result.status == WARN
    assert result.detail.startswith("mode hybrid;")
    assert "hybrid without a recorded passing private compare (D8)" in result.detail
    # a public or failing compare does not count
    _eval_run(personal, set=MemoryEvalRun.Set.PUBLIC)
    _eval_run(personal, verdict=MemoryEvalRun.Verdict.FAIL)
    assert _graph()["retrieval_indexes"].status == WARN
    _eval_run(personal)
    assert _graph()["retrieval_indexes"].status == PASS


# --- graphify ----------------------------------------------------------------------


def _labels(value):
    return lambda image, *, timeout: value


def _repo(workspace, name="friday", *, graphify=True):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name=name,
        git_url=f"git@github.com:owner/{name}.git",
        enabled=True,
        graphify_enabled=graphify,
    )


def _graphify_run(repo, status, **extra):
    return GraphifyRun.objects.create(
        workspace=repo.workspace, repo=repo, requested_sha="a" * 40, status=status, **extra
    )


def test_the_pin_is_read_from_the_lock_file():
    assert preflight.graphify_pinned_version() == PIN


def test_graphify_warns_with_no_enabled_repo_and_no_docker_here():
    result = _graph()["graphify"]
    assert result.status == WARN
    assert "no repo has graphify_enabled (owner step)" in result.detail
    assert "image not checked here (FileNotFoundError; run on the runner host)" in result.detail


def test_graphify_passes_when_every_enabled_repo_last_succeeded(monkeypatch, personal):
    monkeypatch.setattr(preflight, "docker_image_labels", _labels({LABEL: PIN}))
    repo = _repo(personal)
    _repo(personal, "off", graphify=False)
    _graphify_run(repo, GraphifyRun.Status.FAILED, error_type="old")
    _graphify_run(repo, GraphifyRun.Status.SUCCEEDED)
    result = _graph()["graphify"]
    assert result.status == PASS, result.detail
    assert result.detail == "1 enabled repo(s): 1 succeeded; image label matches the pin"


def test_graphify_fails_on_a_failed_last_run_naming_only_the_type(monkeypatch, personal):
    monkeypatch.setattr(preflight, "docker_image_labels", _labels({LABEL: PIN}))
    repo = _repo(personal)
    _graphify_run(repo, GraphifyRun.Status.SUCCEEDED)
    _graphify_run(
        repo, GraphifyRun.Status.FAILED, error_type="schema_invalid", error_message="/repo/x"
    )
    result = _graph()["graphify"]
    assert result.status == FAIL
    assert "friday: last run failed (schema_invalid)" in result.detail
    assert "/repo/x" not in result.detail


def test_graphify_fails_when_an_enabled_repo_has_no_image(monkeypatch, personal):
    monkeypatch.setattr(preflight, "docker_image_labels", _labels(None))
    result = _graph()["graphify"]
    assert result.status == WARN and "not built" in result.detail  # nothing needs it yet
    _graphify_run(_repo(personal), GraphifyRun.Status.SUCCEEDED)
    result = _graph()["graphify"]
    assert result.status == FAIL
    assert "image friday-harness-graphify:0.9.103 not built" in result.detail


@pytest.mark.parametrize("labels", [{}, {LABEL: "0.9.102"}])
def test_graphify_fails_on_a_mislabelled_image(monkeypatch, labels):
    monkeypatch.setattr(preflight, "docker_image_labels", _labels(labels))
    result = _graph()["graphify"]
    assert result.status == FAIL
    assert f"pin {PIN}" in result.detail and LABEL in result.detail


def test_graphify_warns_when_a_repo_never_ran_or_is_in_flight(monkeypatch, personal):
    monkeypatch.setattr(preflight, "docker_image_labels", _labels({LABEL: PIN}))
    _repo(personal)
    result = _graph()["graphify"]
    assert result.status == WARN
    assert result.detail == "1 enabled repo(s): 1 never ran; an enabled repo has no run yet"
    _graphify_run(_repo(personal, "other"), GraphifyRun.Status.RUNNING)
    result = _graph()["graphify"]
    assert result.status == WARN and "1 in flight" in result.detail


def test_the_docker_seam_reads_only_the_labels(monkeypatch, real_docker_image_labels):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        if argv[-1] == "absent":
            return SimpleNamespace(returncode=1, stdout=b"")
        if argv[-1] == "garbled":
            return SimpleNamespace(returncode=0, stdout=b"not json")
        return SimpleNamespace(returncode=0, stdout=b'{"org.friday.graphify.version":"0.9.103"}\n')

    monkeypatch.setattr(subprocess, "run", fake_run)
    seam = real_docker_image_labels
    assert seam("friday-harness-graphify:0.9.103", timeout=5) == {LABEL: PIN}
    assert seam("absent", timeout=5) is None
    assert seam("garbled", timeout=5) == {}
    assert seen[0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{json .Config.Labels}}",
        "friday-harness-graphify:0.9.103",
    ]


# --- compressor --------------------------------------------------------------------

HEALTHY = '{"status": "ok", "telemetry": "off", "offline": true}'


def _sidecar(monkeypatch, status=200, text=HEALTHY, seen=None):
    def fake(url, *, timeout):
        if seen is not None:
            seen.append(url)
        if isinstance(status, Exception):
            raise status
        return status, text

    monkeypatch.setattr(preflight, "http_get", fake)


@pytest.fixture
def compression(settings):
    settings.AI_COMPRESSION_ENABLED = True
    settings.AI_COMPRESSION_PURPOSES = frozenset({"chat"})
    settings.AI_COMPRESSION_URL = "http://compressor:8080/compress"
    return settings


def test_compressor_is_ok_when_off(settings, monkeypatch):
    settings.AI_COMPRESSION_ENABLED = False
    _sidecar(monkeypatch, status=ConnectionError("never called"))
    result = _graph()["compressor"]
    assert (result.status, result.detail) == (PASS, "off (AI_COMPRESSION_ENABLED is false)")


def test_compressor_passes_with_a_healthy_sidecar_and_a_recorded_compare(
    compression, monkeypatch, personal
):
    seen = []
    _sidecar(monkeypatch, seen=seen)
    _eval_run(personal, retrieval_mode="legacy", compression_purpose="chat")
    result = _graph()["compressor"]
    assert (result.status, result.detail) == (PASS, "sidecar healthy; purposes: chat")
    assert seen == ["http://compressor:8080/healthz"]


@pytest.mark.parametrize(
    ("verdict", "data_set"),
    [
        (None, None),
        (MemoryEvalRun.Verdict.INCONCLUSIVE, MemoryEvalRun.Set.PRIVATE),
        (MemoryEvalRun.Verdict.FAIL, MemoryEvalRun.Set.PRIVATE),
        (MemoryEvalRun.Verdict.PASS, MemoryEvalRun.Set.PUBLIC),
    ],
)
def test_compressor_fails_without_a_recorded_passing_private_compare(
    compression, monkeypatch, personal, verdict, data_set
):
    _sidecar(monkeypatch)
    if verdict is not None:
        _eval_run(
            personal,
            retrieval_mode="legacy",
            compression_purpose="chat",
            verdict=verdict,
            set=data_set,
        )
    result = _graph()["compressor"]
    assert result.status == FAIL
    assert "no recorded passing private compare for chat" in result.detail


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (ConnectionError("down"), "", "sidecar unreachable: ConnectionError"),
        (500, "{}", "sidecar /healthz HTTP 500"),
        (200, "not json", "unexpected sidecar /healthz body"),
        (
            200,
            '{"status": "ok", "telemetry": "on", "offline": true}',
            "telemetry on or not offline",
        ),
        (
            200,
            '{"status": "ok", "telemetry": "off", "offline": false}',
            "telemetry on or not offline",
        ),
    ],
)
def test_compressor_fails_on_an_unhealthy_sidecar(
    compression, monkeypatch, personal, status, text, expected
):
    _eval_run(personal, retrieval_mode="legacy", compression_purpose="chat")
    _sidecar(monkeypatch, status=status, text=text)
    result = _graph()["compressor"]
    assert result.status == FAIL and expected in result.detail


def test_compressor_fails_on_a_url_it_cannot_derive_health_from(compression, monkeypatch):
    compression.AI_COMPRESSION_URL = "compressor:8080"
    _sidecar(monkeypatch, status=ConnectionError("never called"))
    result = _graph()["compressor"]
    assert (result.status, result.detail) == (FAIL, "AI_COMPRESSION_URL is not an http(s) URL")


def test_compressor_warns_when_nothing_would_be_compressed(compression, monkeypatch):
    _sidecar(monkeypatch)
    compression.AI_COMPRESSION_PURPOSES = frozenset()
    result = _graph()["compressor"]
    assert result.status == WARN and "no purpose enabled" in result.detail
    compression.AI_COMPRESSION_PURPOSES = frozenset({"memory.episode_extraction"})
    result = _graph()["compressor"]
    assert result.status == WARN and "memory.episode_extraction always bypassed" in result.detail


# --- transcripts -------------------------------------------------------------------


@pytest.fixture
def sources(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    return {s.kind: s for s in IngestionSource.objects.filter(workspace=personal)}


def _enable(source, *, at=None):
    source.enabled = True
    source.enabled_at = at or NOW - timedelta(days=30)
    source.save()


@pytest.fixture
def wired(sources, personal):
    hooks = sources[Kind.CLAUDE_CODE_HOOKS]
    _enable(hooks)
    add_rule(hooks, "path_prefix", "/Users/owner/code", personal)
    _enable(sources[Kind.CLAUDE_CODE_TRANSCRIPTS])
    return sources


def _port(monkeypatch, port):
    monkeypatch.setattr(preflight, "transcript_node_port", lambda: port)


def test_transcripts_warn_without_an_owner():
    assert _graph()["transcripts"].status == WARN


def test_transcripts_are_off_while_both_sources_are_disabled(sources, monkeypatch):
    _port(monkeypatch, UnavailableNodePort())
    result = _graph()["transcripts"]
    assert (result.status, result.detail) == (PASS, "off (both transcript sources disabled)")


def test_transcripts_warn_while_no_node_port_is_installed(wired):
    # the real default port: the nodes-backed port is a pending follow-up
    result = _graph()["transcripts"]
    assert result.status == WARN and "no node port installed" in result.detail


def test_transcripts_warn_when_the_node_is_offline_or_not_granted(wired, owner, monkeypatch):
    _port(monkeypatch, ReferenceNodePort(owner=owner, now=NOW, online=False))
    result = _graph()["transcripts"]
    assert result.status == WARN and "not reachable" in result.detail
    _port(monkeypatch, ReferenceNodePort(owner=owner, now=NOW, granted=False))
    result = _graph()["transcripts"]
    assert result.status == WARN
    assert f"{CLAUDE} not granted (Touch ID grant, Mac session)" in result.detail


def test_transcripts_warn_before_the_first_pull_then_pass(wired, owner, monkeypatch):
    node = ReferenceNodePort(owner=owner, now=NOW)
    _port(monkeypatch, node)
    result = _graph()["transcripts"]
    assert result.status == WARN and f"{CLAUDE}: no pull yet" in result.detail
    pull_transcripts(now=NOW, port=node)
    result = _graph()["transcripts"]
    assert (result.status, result.detail) == (PASS, f"{CLAUDE} ok")
    # a path, a session id or a turn never reaches the detail
    assert "/Users" not in result.detail


def _pull_row(owner, personal, capability, status, code=""):
    record_audit(
        action=PULL_AUDIT_ACTION,
        actor=owner,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=personal,
        metadata={"capability": capability, "status": status, "code": code, "system": True},
    )


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        ("failed", "timeout", FAIL),
        ("server_refused", "actor_not_owner", FAIL),
        ("node_refused", "not_granted", WARN),
    ],
)
def test_transcripts_report_the_last_pull_status(
    wired, owner, personal, monkeypatch, status, code, expected
):
    _port(monkeypatch, ReferenceNodePort(owner=owner, now=NOW))
    _pull_row(owner, personal, CLAUDE, "ok")
    _pull_row(owner, personal, CLAUDE, status, code)
    _pull_row(owner, personal, CODEX, "failed")  # the codex source is disabled
    result = _graph()["transcripts"]
    assert result.status == expected
    assert result.detail.startswith(f"{CLAUDE} {status} ({code})")
    assert CODEX not in result.detail


def test_a_paused_transcript_source_warns(wired, owner, personal, monkeypatch):
    _port(monkeypatch, ReferenceNodePort(owner=owner, now=NOW))
    _pull_row(owner, personal, CLAUDE, "ok")
    IngestionSource.objects.filter(kind=Kind.CLAUDE_CODE_TRANSCRIPTS).update(paused_at=NOW)
    result = _graph()["transcripts"]
    assert result.status == WARN and "claude_code_transcripts paused" in result.detail


# --- eval_baseline -----------------------------------------------------------------


def _baseline(workspace, data_set, **fields):
    return _eval_run(
        workspace,
        set=data_set,
        kind=MemoryEvalRun.Kind.BASELINE,
        retrieval_mode="legacy",
        arm="",
        verdict="",
        **fields,
    )


def test_eval_baseline_warns_pending_owner_evaluation(personal):
    result = _graph()["eval_baseline"]
    assert result.status == WARN
    assert result.detail == "no private baseline recorded (pending owner evaluation, M2 item 1)"
    _baseline(personal, MemoryEvalRun.Set.PUBLIC, recall_at_10=0.5714)
    result = _graph()["eval_baseline"]
    assert result.status == WARN and result.detail.endswith("; a public baseline exists")


def test_eval_baseline_passes_with_a_private_baseline_reporting_metrics_only(personal):
    _baseline(
        personal,
        MemoryEvalRun.Set.PRIVATE,
        total=100,
        recall_at_10=0.8123,
        leak_rate=0.0,
        temporal_correctness=None,
    )
    result = _graph()["eval_baseline"]
    assert result.status == PASS
    assert result.detail == (
        "private baseline 2026-11-21 (legacy): recall@10 0.8123, leak rate 0.0000, "
        "temporal n/a, 100 case(s)"
    )


# --- wiring ------------------------------------------------------------------------


def test_the_graph_checks_are_registered_in_order_and_make_no_model_call(owner, sources):
    names = [name for name, _ in preflight.CHECKS]
    assert names.index("graph") == names.index("learning") + 1
    results = preflight.run_preflight(checks=(("graph", preflight.check_graph),))
    assert [r.name for r in results] == [
        "graph_integrity",
        "retrieval_indexes",
        "graphify",
        "compressor",
        "transcripts",
        "eval_baseline",
    ]
    assert not ModelUsage.objects.exists()


def test_one_crashing_graph_check_does_not_hide_the_others(monkeypatch):
    def boom():
        raise RuntimeError("secret /path detail")

    monkeypatch.setattr(preflight, "_check_graphify", boom)
    results = {r.name: r for r in preflight.check_graph()}
    assert results["graphify"].status == FAIL
    assert results["graphify"].detail == "check crashed: RuntimeError"
    assert results["graph_integrity"].status == PASS
