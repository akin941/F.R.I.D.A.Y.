"""Phase 8B branch 4 (brief D8): `select_context_chunks(..., fused=True)` --
vector plus full-text lists fused with `core.ranking.rrf` inside
`knowledge`; a `GatewayError` on the embed degrades to full text only
(the legacy selector returns zero chunks then); eligibility and workspace
scoping are the legacy selector's."""

import pytest

from accounts.services import register_user
from ai_core.adapters.fake import embed_text
from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider
from audit.models import AuditLog
from knowledge.context import select_context_chunks
from knowledge.models import KnowledgeChunk, KnowledgeDocument
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture(autouse=True)
def ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def provider(db):
    return ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )


def make_chunk(workspace, title, content, provider, *, sensitivity="normal"):
    document = KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title=title,
        status=KnowledgeDocument.Status.READY,
        sensitivity=sensitivity,
        raw_text=content,
        content_hash=f"hash-{title}",
    )
    return KnowledgeChunk.objects.create(
        workspace=workspace,
        document=document,
        ordinal=0,
        content=content,
        embedding=embed_text(content),
        embedding_provider=provider,
    )


def _fused(owner, workspace, query):
    return select_context_chunks(actor=owner, workspace=workspace, query_text=query, fused=True)


def test_a_chunk_in_both_lists_ranks_first(owner, workspace, provider):
    both = make_chunk(workspace, "Runbook", "Deploy runbook for hetzner servers.", provider)
    make_chunk(workspace, "Coffee", "Filter coffee, no sugar.", provider)
    chunks = _fused(owner, workspace, "hetzner deploy runbook")
    assert chunks[0].pk == both.pk


def test_a_gateway_error_degrades_to_full_text_only(owner, workspace, provider, monkeypatch):
    row = make_chunk(workspace, "Runbook", "Deploy runbook for hetzner servers.", provider)
    make_chunk(workspace, "Coffee", "Filter coffee, no sugar.", provider)

    def boom(self, *args, **kwargs):
        raise GatewayError("down")

    monkeypatch.setattr(InferenceGateway, "embed", boom)
    assert [c.pk for c in _fused(owner, workspace, "hetzner")] == [row.pk]
    # The legacy selector keeps today's behaviour: zero chunks.
    assert select_context_chunks(actor=owner, workspace=workspace, query_text="hetzner") == []
    assert AuditLog.objects.filter(action="knowledge.context_accessed").count() == 2


def test_private_and_foreign_chunks_never_reach_the_fused_list(owner, workspace, provider):
    make_chunk(workspace, "Secret", "hetzner root password notes", provider, sensitivity="private")
    other = register_user(email="other@test.dev", password=PASSWORD, name="O")
    make_chunk(Workspace.objects.get(owner=other), "Foreign", "hetzner foreign notes", provider)
    assert _fused(owner, workspace, "hetzner") == []
