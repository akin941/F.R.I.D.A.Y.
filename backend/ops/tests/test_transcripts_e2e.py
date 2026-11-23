"""Phase 8B closeout end to end (brief "10. chore/p8b-closeout", E2E):

reference node with fixture transcripts -> pull -> episodes -> night
(eager) -> fact proposal -> approve -> hybrid retrieval answers a temporal
question in a fake chat turn with the fenced fact -> a vault run mirrors the
entity note -> forget the transcript source -> the fact and the note are gone
on the next run -> restore.

Everything goes through the real surfaces where one exists: the owner
enables the sources and writes the (shared hooks) path rules over the web
API, the pull is `tools.transcript_ingest.pull_transcripts` against the
Python reference node double (`tools/tests/transcript_nodes.py`, the D13
readers over the synthetic JSONL fixtures), the night is the real Celery
task with on-commit dispatch executed, the approval and the forget/restore
are web clicks, the chat turn is the real SSE view with the FakeAdapter
behind the gateway, and the vault writes run inside a claimed, approved
`vault_export` run in `tmp_path`.

It lives in `ops` because it spans `tools`, `memory`, `assistant` and the
vault export (ops is the leaf allowed to import all of them). No real LLM,
Mac, node, Claude Code, Codex, Obsidian or network. The live Mac node in
place of the double is the Mac session's job (M1, "server-side contract
tests it must pass")."""

import json
import re
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from accounts.services import register_user
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelUsage
from assistant.models import Conversation
from core.channels import CHANNEL_WEB
from memory.ingestion.tasks import run_learning_night_task
from memory.models import Episode, IngestionSource, MemoryEntity, MemoryFact
from memory.tests.learning_helpers import capture_llm, payload_text, seed_prompts
from ops import preflight
from tools import export
from tools.tests.transcript_nodes import ReferenceNodePort
from tools.tests.vault_helpers import export_in_run
from tools.transcript_ingest import AUDIT_ACTION, pull_transcripts, set_node_port
from workspaces.models import Workspace
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
OWNER_EMAIL = "owner@test.dev"
SOURCES = "/api/v1/memory/ingestion/sources"
LEARNING = "/api/v1/memory/learning"
NOW = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
PERSONAL_PREFIX = "/Users/owner/code"
FIRM_PREFIX = "/Users/owner/work/acme"
QUOTE = "move the billing service to hetzner"
QUESTION = "Billing service nereye deploy ediliyor, ne zamandan beri?"
Kind = IngestionSource.Kind
Status = MemoryFact.Status

_OWNER_ITEM = re.compile(r'<owner_item episode_id="([^"]+)"[^>]*>\n(.*?)\n</owner_item>', re.DOTALL)


@pytest.fixture(autouse=True)
def stack(settings, tmp_path):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    settings.MEMORY_RETRIEVAL_MODE = "legacy"
    settings.OBSIDIAN_VAULT_PATH = str(tmp_path / "vault")
    (tmp_path / "vault").mkdir()
    # `fake-local` (personal-data-allowed: transcripts are personal-class)
    # and `fake-embedding-local`, both behind AI_FAKE_PROVIDER.
    call_command("seed_ai_defaults", stdout=StringIO())
    seed_prompts()


@pytest.fixture
def owner():
    user = register_user(email=OWNER_EMAIL, password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def calls(monkeypatch):
    captured = []
    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        captured.append(
            {
                "purpose": purpose,
                "messages": json.dumps(messages, ensure_ascii=False),
                "personal_data": kwargs.get("personal_data"),
            }
        )
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    return captured


def _extract(messages):
    """The fake episode extractor: one `deployed_to` fact from the owner
    turn that says it, quoted verbatim (the code-checked provenance)."""
    candidates = []
    for episode_id, text in _OWNER_ITEM.findall(payload_text(messages)):
        if "billing service to hetzner" in text:
            candidates.append(
                {
                    "type": "semantic",
                    "title": "Billing runs on hetzner",
                    "content": "The billing service runs on hetzner.",
                    "quote": QUOTE,
                    "episode_id": episode_id,
                    "target": "fact",
                    "fact": {
                        "subject": {"kind": "service", "name": "billing"},
                        "predicate": "deployed_to",
                        "object": {"kind": "service", "name": "hetzner"},
                        "valid_from": "2026-11-20",
                    },
                }
            )
    return json.dumps({"version": 2, "candidates": candidates})


def _sse(response):
    assert response.status_code == 200, response.content
    events = []
    for chunk in b"".join(response.streaming_content).decode().split("\n\n"):
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))
    return events


def _setup_sources(web, personal, firm):
    """The runbook's steps over the web API: seed, enable the hooks source
    and both transcript sources, map one path prefix per workspace on the
    hooks source (the transcript sources share its rules, D13)."""
    call_command("seed_ingestion_sources", stdout=StringIO())
    rows = web.get(f"{SOURCES}?workspace={personal.id}").json()
    by_kind = {row["kind"]: row["id"] for row in rows}
    for kind in (Kind.CLAUDE_CODE_HOOKS, Kind.CLAUDE_CODE_TRANSCRIPTS, Kind.CODEX_TRANSCRIPTS):
        response = web.patch(f"{SOURCES}/{by_kind[kind]}", {"enabled": True}, format="json")
        assert response.status_code == 200, response.content
    for prefix, target in ((PERSONAL_PREFIX, personal), (FIRM_PREFIX, firm)):
        response = web.post(
            f"{SOURCES}/{by_kind[Kind.CLAUDE_CODE_HOOKS]}/rules",
            {"rule_type": "path_prefix", "pattern": prefix, "target_workspace": str(target.id)},
            format="json",
        )
        assert response.status_code == 201, response.content
    # A transcript source's own rule form is refused: the rules are shared.
    response = web.post(
        f"{SOURCES}/{by_kind[Kind.CLAUDE_CODE_TRANSCRIPTS]}/rules",
        {"rule_type": "path_prefix", "pattern": "/tmp", "target_workspace": str(personal.id)},
        format="json",
    )
    assert response.status_code == 400
    # Enabled long ago, so the first pull starts at the 7-day floor and
    # reaches every fixture turn.
    IngestionSource.objects.filter(
        kind__in=[Kind.CLAUDE_CODE_TRANSCRIPTS, Kind.CODEX_TRANSCRIPTS]
    ).update(enabled_at=NOW - timedelta(days=30))
    return by_kind


def _chat(web, owner, workspace, calls):
    conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
    calls.clear()
    events = _sse(
        web.post(
            f"/api/v1/ai/conversations/{conversation.id}/messages",
            {"content": QUESTION},
            format="json",
        )
    )
    assert events[-1]["type"] == "done"
    chat = [c for c in calls if c["purpose"] == "chat"][-1]
    system = json.loads(chat["messages"])[0]["content"]
    usage = ModelUsage.objects.filter(purpose="chat").latest("created_at")
    return system, usage.context_report


def _entity_note(root, workspace, entity_id):
    return root / "03_Workspaces" / str(workspace.id) / "Entities" / f"{entity_id}.md"


def _vault_run(workspace):
    from django.conf import settings

    root = export.validate_vault_root_exists(settings.OBSIDIAN_VAULT_PATH)
    stats = export.ExportStats()
    export_in_run(root, workspace, stats)
    return Path(settings.OBSIDIAN_VAULT_PATH), stats


def test_fixture_transcripts_to_an_approved_fact_answered_mirrored_forgotten_restored(
    owner,
    personal,
    firm,
    calls,
    web_client,
    settings,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    web = web_client(owner)
    by_kind = _setup_sources(web, personal, firm)
    llm = capture_llm(monkeypatch)
    llm.overrides["memory.episode_extraction"] = _extract

    # 1. The pull: one audited invoke per tool and workspace through the
    #    reference node, each with exactly that workspace's prefixes.
    node = ReferenceNodePort(owner=owner, now=NOW)
    report = pull_transcripts(now=NOW, port=node)
    assert report.invokes == 4 and report.refused == 0 and report.failed == 0
    assert report.stored > 0
    assert len(node.chain) == 4
    sent = {(frame["workspace"], tuple(frame["args"]["cwd_prefixes"])) for frame in node.received}
    assert sent == {(personal.pk, (PERSONAL_PREFIX,)), (firm.pk, (FIRM_PREFIX,))}
    from audit.models import AuditLog

    pulls = AuditLog.objects.filter(action=AUDIT_ACTION)
    assert pulls.count() == 4 and all(row.actor_id == owner.pk for row in pulls)

    # 2. Episodes: owner turns only, in the mapped workspaces.
    turns = Episode.objects.filter(kind__endswith=".owner_turn")
    assert turns.filter(workspace=personal).exists() and turns.filter(workspace=firm).exists()
    billing = turns.get(owner_text__contains="billing service to hetzner")
    assert billing.workspace_id == personal.id
    assert billing.source.kind == Kind.CLAUDE_CODE_TRANSCRIPTS

    # 3. The night (eager Celery, on-commit dispatch executed): the
    #    extraction runs personal-class (`personal_data=True`) and proposes
    #    one quoted fact, born pending.
    with django_capture_on_commit_callbacks(execute=True):
        run_learning_night_task()
    extraction = [c for c in calls if c["purpose"] == "memory.episode_extraction"]
    assert extraction and all(c["personal_data"] is True for c in extraction)
    proposal = MemoryFact.objects.get(workspace=personal)
    assert proposal.status == Status.PENDING_APPROVAL
    assert proposal.provenance_quote == QUOTE
    assert proposal.ingestion_source.kind == Kind.CLAUDE_CODE_TRANSCRIPTS
    assert not MemoryFact.objects.filter(workspace=firm).exists()

    # 4. The morning card shows it; the owner approves it (a web click).
    digest = web.get(f"{LEARNING}/digest").json()
    items = [i for g in digest["workspaces"] for i in g["items"]]
    item = next(i for i in items if i["kind"] == "fact" and i["id"] == str(proposal.id))
    with django_capture_on_commit_callbacks(execute=True):
        response = web.post(f"{LEARNING}/proposals/fact/{item['id']}/approve", {})
    assert response.status_code == 200, response.content
    fact = MemoryFact.objects.get(pk=proposal.pk)
    assert fact.status == Status.APPROVED and fact.valid_to is None

    # 5. Hybrid retrieval answers the temporal question in a fake chat turn
    #    from the fenced fact with its window; legacy never injects facts.
    system, context = _chat(web, owner, personal, calls)
    assert "<memory_fact>" not in system and context["retrieval_mode"] == "legacy"
    settings.MEMORY_RETRIEVAL_MODE = "hybrid"
    system, context = _chat(web, owner, personal, calls)
    assert "<memory_fact>" in system
    fenced = system.split("<memory_fact>", 1)[1].split("</memory_fact>", 1)[0]
    assert fact.statement in fenced and "2026-11-20–" in fenced
    assert context["retrieval_mode"] == "hybrid" and str(fact.pk) in context["fact_ids"]
    assert fact.statement not in json.dumps(context, ensure_ascii=False)
    # The firm workspace never sees the personal fact.
    firm_system, firm_context = _chat(web, owner, firm, calls)
    assert fact.statement not in firm_system and str(fact.pk) not in firm_context["fact_ids"]

    # 6. A vault run (inside an approved `vault_export` run) mirrors the
    #    entity note of the fact's subject, in the fact's workspace.
    root, stats = _vault_run(personal)
    note = _entity_note(root, personal, fact.subject_id)
    assert note.is_file() and stats.entity_notes >= 1
    assert fact.statement in note.read_text()
    assert not (root / "03_Workspaces" / str(firm.id) / "Entities").exists() or not any(
        fact.statement in p.read_text()
        for p in (root / "03_Workspaces" / str(firm.id) / "Entities").glob("*.md")
    )

    # 7. Forget the transcript source (a web click): the episodes, the fact
    #    and the entities left without a live fact leave; the next vault run
    #    removes the note and the next chat turn no longer sees the fact.
    source_id = by_kind[Kind.CLAUDE_CODE_TRANSCRIPTS]
    response = web.post(f"{SOURCES}/{source_id}/forget", {}, format="json")
    assert response.status_code == 200, response.content
    fact.refresh_from_db()
    assert fact.status == Status.ARCHIVED
    assert not Episode.objects.filter(pk=billing.pk).exists()  # in the trash
    assert not MemoryEntity.objects.filter(pk=fact.subject_id).exists()
    root, stats = _vault_run(personal)
    assert not note.exists() and stats.removed >= 1
    system, context = _chat(web, owner, personal, calls)
    assert fact.statement not in system and str(fact.pk) not in context["fact_ids"]
    # The pull is a no-op while the source is paused by the forget.
    before = AuditLog.objects.filter(action=AUDIT_ACTION).count()
    pull_transcripts(now=NOW + timedelta(minutes=15), port=node)
    claude_pulls = AuditLog.objects.filter(
        action=AUDIT_ACTION, metadata__capability="claude.transcripts.owner_turns"
    )
    assert claude_pulls.count() == 2
    assert AuditLog.objects.filter(action=AUDIT_ACTION).count() == before + 2  # codex only

    # 8. Restore (a web click): exactly those rows come back, and the next
    #    vault run writes the note again.
    response = web.post(f"{SOURCES}/{source_id}/restore", {}, format="json")
    assert response.status_code == 200, response.content
    fact.refresh_from_db()
    assert fact.status == Status.APPROVED and fact.forget_operation_id is None
    assert Episode.objects.filter(pk=billing.pk).exists()
    root, stats = _vault_run(personal)
    assert note.is_file() and fact.statement in note.read_text()
    system, context = _chat(web, owner, personal, calls)
    assert str(fact.pk) in context["fact_ids"]
    # The forget paused the source; the owner resumes it and the next beat
    # pulls again from the kept cursor (no turn is stored twice).
    response = web.patch(f"{SOURCES}/{source_id}", {"paused": False}, format="json")
    assert response.status_code == 200, response.content
    episodes_before = Episode.all_objects.count()
    report = pull_transcripts(now=NOW + timedelta(minutes=30), port=node)
    assert report.invokes == 4 and report.failed == 0
    assert Episode.all_objects.count() == episodes_before

    # Every model call of the run is one ModelUsage row (rule 3).
    assert ModelUsage.objects.filter(purpose="chat").count() == 5

    # 9. The closeout preflight checks see the pipeline. The default port
    #    has no node (the `nodes`-backed port is a pending follow-up), so
    #    `transcripts` warns; with the reference node installed it reads
    #    the last pull of each capability.
    learning = {r.name: r for r in preflight.check_learning()}
    assert learning["learning_scrubber"].status == preflight.PASS
    graph = {r.name: r for r in preflight.check_graph()}
    assert graph["graph_integrity"].status == preflight.PASS, graph["graph_integrity"]
    assert graph["transcripts"].status == preflight.WARN
    assert "no node port" in graph["transcripts"].detail
    assert graph["eval_baseline"].status == preflight.WARN
    previous = set_node_port(node)
    try:
        result = {r.name: r for r in preflight.check_graph()}["transcripts"]
    finally:
        set_node_port(previous)
    assert result.status == preflight.PASS, result
    assert "claude.transcripts.owner_turns ok" in result.detail
