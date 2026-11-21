"""Phase 8B branch 4 (brief D8): the fact embedding job (the `Memory` lease
pattern). Only retrievable facts -- current, `approved`, `learned`,
non-`private` -- are embedded; a Graphify import makes zero embed calls and
zero `ModelUsage` rows, and its facts keep `embedding IS NULL`."""

from datetime import UTC, datetime

import pytest

from ai_core.exceptions import GatewayError
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage
from core.channels import CHANNEL_WEB
from memory.graph import ports, services
from memory.jobs import (
    MAX_EMBEDDING_RETRIES,
    claim_fact_for_embedding,
    finalize_fact_embedding_success,
)
from memory.models import MemoryEntity, MemoryFact
from memory.tasks import embed_fact, recover_stale_fact_embeddings
from memory.tests.graph_helpers import T0, T1, entity, fact, system_fact

pytestmark = pytest.mark.django_db

Status = MemoryFact.Status


@pytest.fixture(autouse=True)
def ai(settings):
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0


@pytest.fixture
def provider(db):
    return ModelProvider.objects.create(
        name="fake-embedding-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        priority=0,
    )


@pytest.fixture
def api(personal):
    return entity(personal, "api")


def embed_calls():
    return ModelUsage.objects.filter(purpose="memory.retrieval")


def test_an_approved_learned_current_fact_is_embedded(personal, api, provider):
    row = fact(personal, api)
    embed_fact(str(row.pk))
    row.refresh_from_db()
    assert row.embedding is not None and row.embedding_provider_id == provider.pk
    assert row.claim_token is None and row.retry_count == 0
    assert embed_calls().count() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": Status.PENDING_APPROVAL},
        {"status": Status.REJECTED},
        {"status": Status.ARCHIVED},
        {"sensitivity": MemoryFact.Sensitivity.PRIVATE},
        {"valid_to": T1},
    ],
    ids=["pending", "rejected", "archived", "private", "superseded"],
)
def test_a_fact_that_is_not_retrievable_is_never_embedded(personal, api, provider, overrides):
    row = fact(personal, api, **overrides)
    embed_fact(str(row.pk))
    row.refresh_from_db()
    assert row.embedding is None
    assert not embed_calls().exists()


def test_a_graphify_import_makes_zero_embed_calls(personal, provider):
    ports.import_code_graph(
        workspace=personal,
        repo_ref="11111111-1111-1111-1111-111111111111",
        commit_sha="a" * 40,
        committed_at=datetime(2026, 10, 1, tzinfo=UTC),
        graph={
            "entities": [
                {"key": "app/models.py", "kind": "code_module", "name": "app/models.py"},
                {"key": "app/views.py", "kind": "code_module", "name": "app/views.py"},
                {"key": "tool:django", "kind": "tool", "name": "django"},
            ],
            "facts": [
                {"subject": "app/views.py", "predicate": "imports", "object": "app/models.py"},
                {"subject": "app/views.py", "predicate": "imports", "object": "tool:django"},
            ],
        },
    )
    # Plus a factory batch of system facts.
    modules = [
        entity(personal, f"m{i}.py", MemoryEntity.Kind.CODE_MODULE, "graphify") for i in range(6)
    ]
    for a, b in zip(modules, modules[1:], strict=False):
        system_fact(personal, a, b)
    system_ids = list(
        MemoryFact.all_objects.filter(status=Status.SYSTEM).values_list("pk", flat=True)
    )
    assert len(system_ids) >= 7
    assert recover_stale_fact_embeddings() == 0
    for pk in system_ids:
        embed_fact(str(pk))
        assert claim_fact_for_embedding(pk) is None
    assert not ModelUsage.objects.exists()
    assert not MemoryFact.all_objects.filter(status=Status.SYSTEM, embedding__isnull=False).exists()


def test_the_scan_backfills_and_clears_vectors_of_unretrievable_facts(personal, api, provider):
    live = fact(personal, api)
    gone = fact(personal, api, text="elsewhere", valid_to=T1, valid_from=T0)
    MemoryFact.all_objects.filter(pk=gone.pk).update(embedding=[0.1] * 32)
    assert recover_stale_fact_embeddings() == 1  # eager: embedded inline
    live.refresh_from_db()
    gone.refresh_from_db()
    assert live.embedding is not None
    assert gone.embedding is None


def test_a_gateway_error_counts_a_retry_and_stops_at_the_ceiling(
    personal, api, provider, monkeypatch
):
    row = fact(personal, api)

    def boom(self, *args, **kwargs):
        raise GatewayError("down", retryable=True)

    monkeypatch.setattr(InferenceGateway, "embed", boom)
    for _ in range(MAX_EMBEDDING_RETRIES + 1):
        embed_fact(str(row.pk))
    row.refresh_from_db()
    assert row.embedding is None and row.retry_count == MAX_EMBEDDING_RETRIES


def test_a_fact_superseded_mid_call_gets_no_vector(personal, api, provider):
    row = fact(personal, api)
    claimed = claim_fact_for_embedding(row.pk)
    assert claimed is not None
    MemoryFact.all_objects.filter(pk=row.pk).update(valid_to=T1)
    assert not finalize_fact_embedding_success(
        row.pk, claimed[1], vector=[0.1] * 32, provider=provider
    )
    row.refresh_from_db()
    assert row.embedding is None


def test_approving_and_creating_a_fact_dispatch_its_embedding(
    owner, personal, api, provider, django_capture_on_commit_callbacks
):
    proposal = fact(personal, api, status=Status.PENDING_APPROVAL)
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_fact(fact=proposal, actor=owner, channel=CHANNEL_WEB)
    proposal.refresh_from_db()
    assert proposal.embedding is not None
    with django_capture_on_commit_callbacks(execute=True):
        created = services.create_fact(
            workspace=personal,
            actor=owner,
            channel=CHANNEL_WEB,
            subject_kind="service",
            subject_name="api",
            predicate="uses",
            object_text="redis",
            statement="api uses redis",
        )
    created.refresh_from_db()
    assert created.embedding is not None


def test_supersede_drops_the_old_vector(personal, api, provider):
    old = fact(personal, api, valid_from=T0)
    embed_fact(str(old.pk))
    new = fact(personal, api, text="elsewhere", valid_from=T1)
    services.supersede(old, new)
    old.refresh_from_db()
    assert old.embedding is None and old.embedding_provider_id is None
