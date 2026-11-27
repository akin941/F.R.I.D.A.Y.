"""The Phase 8B transcript e2e over the in-process gateway transport
(runbook A12): the `nodes`-backed port the app installs at start, the real
`NodeSocketApp` (in process, `nodes/tests/gateway_harness.py`: its own
event loop thread, the real test Redis bus), and the Python reference node
reading the 8B owner-turn fixtures from a home.

paired node -> `hello` (the snapshot advertises both owner-turn reads,
granted) -> one beat of the pull (`pull_transcripts`, default port) -> one
signed `invoke` per tool and workspace with exactly that workspace's
prefixes -> `ok` chain rows on the system channel with the owner as actor
-> owner-turn episodes in the mapped workspaces, scrubbed, owner text only
-> the night proposes one quoted fact -> preflight `transcripts` passes.

The rest of the 8B e2e (approve, retrieval, vault, forget and restore) does
not depend on the transport and stays in `test_transcripts_e2e.py`. The
fixture timestamps are moved to the wall clock (`transcript_gateway`): the
node verifies envelopes and bounds `since` on real time. No Mac, Claude
Code, Codex or model."""

import json
import re
from datetime import timedelta

import pytest
from django.db import connections
from django.utils import timezone

from accounts import devices
from accounts.models import DevicePairingCode, TrustedDevice
from ai_core.gateway import InferenceGateway
from audit.models import AuditLog
from memory.ingestion.services import ensure_default_sources
from memory.ingestion.tasks import run_learning_night_task
from memory.models import Episode, IngestionSource, MemoryFact, WorkspaceMappingRule
from memory.tests.learning_helpers import capture_llm, fake_provider, payload_text, seed_prompts
from nodes import bus
from nodes.audit_chain import verify_chain
from nodes.gateway import NodeSocketApp
from nodes.keys import signing_keyring
from nodes.models import NodeInvocation
from nodes.reference.executor import FakeExecutor
from nodes.reference.node import ReferenceNode, default_catalogue
from nodes.reference.readers import Readers
from nodes.services import pair_node
from nodes.tests.gateway_harness import V1, Harness, WallClock
from ops import preflight
from tools.tests.node_reads_helpers import _firm, _user
from tools.tests.transcript_gateway import owner_turn_home
from tools.transcript_ingest import AUDIT_ACTION, get_node_port, pull_transcripts
from tools.transcript_node_port import NodesTranscriptPort
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db(transaction=True)

CLAUDE = "claude.transcripts.owner_turns"
CODEX = "codex.transcripts.owner_turns"
Kind = IngestionSource.Kind
QUOTE = "move the billing service to hetzner"
NEVER = (
    "ASSISTANT-ONLY-PHRASE",
    "TOOL-RESULT-PHRASE",
    "TOOL-MIXED-PHRASE",
    "META-PHRASE",
    "MARKER-PHRASE",
    "SIDECHAIN-PHRASE",
    "COMPACT-PHRASE",
    "SUMMARY-PHRASE",
    "IMAGE-PHRASE",
    "UNKNOWN-PHRASE",
    "EVENT-MSG-PHRASE",
    "DEVELOPER-PHRASE",
    "SCRATCH-PHRASE",
    "hunter2",
)
_OWNER_ITEM = re.compile(r'<owner_item episode_id="([^"]+)"[^>]*>\n(.*?)\n</owner_item>', re.DOTALL)


@pytest.fixture
def harness():
    app = NodeSocketApp(tick_seconds=0.05)
    harness = Harness(app)
    yield harness
    harness.close()
    connections.close_all()


@pytest.fixture
def world():
    owner = _user("owner@test.dev", is_owner=True)
    personal = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    firm = _firm(owner, "Acme")
    ensure_default_sources(workspace=personal, created_by=owner)
    hooks = IngestionSource.objects.get(workspace=personal, kind=Kind.CLAUDE_CODE_HOOKS)
    for pattern, target in (("~/code", personal), ("~/work/acme", firm)):
        WorkspaceMappingRule.objects.create(
            workspace=personal,
            source=hooks,
            rule_type=WorkspaceMappingRule.RuleType.PATH_PREFIX,
            pattern=pattern,
            target_workspace=target,
            created_by=owner,
        )
    IngestionSource.objects.filter(workspace=personal).filter(
        kind__in=[Kind.CLAUDE_CODE_HOOKS, Kind.CLAUDE_CODE_TRANSCRIPTS, Kind.CODEX_TRANSCRIPTS]
    ).update(enabled=True, enabled_at=timezone.now() - timedelta(days=30))
    raw_code, _ = devices.issue_pairing_code(
        owner=owner,
        kind=DevicePairingCode.Kind.STRONG,
        issued_via=DevicePairingCode.IssuedVia.MANAGEMENT_COMMAND,
        device_kind=TrustedDevice.Kind.NODE,
    )
    paired = pair_node(raw_code=raw_code, node_name="MacBook", protocol_versions=[1])
    state = {"owner": owner, "personal": personal, "firm": firm, "paired": paired}
    yield state
    for clear in (bus.clear_presence, bus.clear_inbox, bus.clear_inflight):
        clear(paired.node.pk)


def _reference_node(paired, home):
    keyring = signing_keyring()
    readers = Readers(home=home, policy=None, grants={"claude": True, "codex": True})
    clock = WallClock()
    return ReferenceNode(
        node_id=str(paired.node.pk),
        pinned={keyring.key_id: keyring.active.public_key_bytes},
        policy=None,
        executor=FakeExecutor(clock),
        clock=clock,
        catalogue=default_catalogue(readers.availability()),
        readers=readers,
    )


def _extract(messages):
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


def test_the_pull_over_the_gateway_to_a_quoted_fact_proposal(
    world, harness, tmp_path, settings, monkeypatch, django_capture_on_commit_callbacks
):
    owner, personal, firm, paired = (world[k] for k in ("owner", "personal", "firm", "paired"))
    home = owner_turn_home(tmp_path, newest_at=timezone.now() - timedelta(minutes=10))

    # 1. The app installed the nodes-backed port; the node connects.
    port = get_node_port()
    assert isinstance(port, NodesTranscriptPort)
    assert port.owner_node(owner) is None  # paired, not connected yet
    reference = _reference_node(paired, home)
    link = harness.connect(secret=paired.node_secret, node=reference, subprotocols=(V1,))
    assert link.frames_of("hello.ack"), link.close_code
    assert link.wait_for(lambda: bus.is_present(paired.node.pk))
    node = port.owner_node(owner)
    assert node is not None and node.ref == str(paired.node.pk)
    assert port.capability_granted(node, CLAUDE) and port.capability_granted(node, CODEX)

    # 2. One beat: one signed invoke per tool and workspace, each with
    #    exactly that workspace's prefixes, answered over the socket.
    report = pull_transcripts()
    assert (report.invokes, report.refused, report.failed) == (4, 0, 0), report
    invokes = link.frames_of("invoke")
    sent = {(i["capability"], i["workspace_id"], tuple(i["args"]["cwd_prefixes"])) for i in invokes}
    assert sent == {
        (CLAUDE, str(personal.pk), ("~/code",)),
        (CLAUDE, str(firm.pk), ("~/work/acme",)),
        (CODEX, str(personal.pk), ("~/code",)),
        (CODEX, str(firm.pk), ("~/work/acme",)),
    }

    # 3. The chain: four `ok` rows on the system channel, the owner as actor,
    #    hashes only; one `nodes.invoke` and one pull audit row each.
    rows = list(NodeInvocation.objects.order_by("seq"))
    assert [(r.status, r.channel, r.actor_id) for r in rows] == [("ok", "system", owner.pk)] * 4
    assert {r.nonce for r in rows} == {i["nonce"] for i in invokes}
    assert verify_chain().ok
    assert AuditLog.objects.filter(action="nodes.invoke").count() == 4
    pulls = AuditLog.objects.filter(action=AUDIT_ACTION)
    assert sorted(p.metadata["status"] for p in pulls) == ["ok"] * 4
    stored = json.dumps(list(NodeInvocation.objects.values()), default=str)
    assert "hetzner" not in stored and "~/code" not in stored

    # 4. Owner-turn episodes in the mapped workspaces, scrubbed node-side.
    turns = Episode.objects.filter(kind__endswith=".owner_turn")
    billing = turns.get(owner_text__contains="billing service to hetzner")
    assert billing.workspace_id == personal.pk
    assert turns.filter(workspace=firm).exists()
    text = json.dumps(list(turns.values_list("owner_text", flat=True)), ensure_ascii=False)
    for phrase in NEVER:
        assert phrase not in text, phrase
    assert "[SECRET:" in text

    # 5. The night proposes one quoted fact from the owner's own words.
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    seed_prompts()
    fake_provider(personal_data_allowed=True)
    llm = capture_llm(monkeypatch)
    llm.overrides["memory.episode_extraction"] = _extract
    flags = []
    original = InferenceGateway.complete

    def spy(self, purpose, messages, **kwargs):
        if purpose == "memory.episode_extraction":
            flags.append(kwargs.get("personal_data"))
        return original(self, purpose, messages, **kwargs)

    monkeypatch.setattr(InferenceGateway, "complete", spy)
    with django_capture_on_commit_callbacks(execute=True):
        run_learning_night_task()
    assert flags and all(flag is True for flag in flags)
    fact = MemoryFact.objects.get(workspace=personal)
    assert fact.status == "pending_approval" and fact.provenance_quote == QUOTE
    assert not MemoryFact.objects.filter(workspace=firm).exists()

    # 6. Preflight `transcripts` reads the default port and passes.
    result = {r.name: r for r in preflight.check_graph()}["transcripts"]
    assert result.status == preflight.PASS, result
    assert f"{CLAUDE} ok" in result.detail and f"{CODEX} ok" in result.detail
