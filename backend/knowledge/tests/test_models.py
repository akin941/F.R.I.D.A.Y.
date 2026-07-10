import pytest
from django.db import IntegrityError

from accounts.services import register_user
from ai_core.models import ModelProvider
from knowledge.models import KnowledgeChunk, KnowledgeDocument, compute_content_hash
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def make_document(workspace, actor, content="Some document text.", **overrides):
    fields = {
        "workspace": workspace,
        "source_type": KnowledgeDocument.SourceType.PASTED_TEXT,
        "title": "Doc",
        "status": KnowledgeDocument.Status.PENDING,
        "sensitivity": KnowledgeDocument.Sensitivity.NORMAL,
        "raw_text": content,
        "content_hash": compute_content_hash(content),
        "created_by": actor,
    }
    fields.update(overrides)
    return KnowledgeDocument.objects.create(**fields)


def make_chunk(document, ordinal=0, **overrides):
    fields = {
        "workspace": document.workspace,
        "document": document,
        "ordinal": ordinal,
        "content": f"chunk {ordinal}",
    }
    fields.update(overrides)
    return KnowledgeChunk.objects.create(**fields)


class TestContentHash:
    def test_normalizes_case_and_whitespace(self):
        assert compute_content_hash("Some  Text\nHERE.") == compute_content_hash("some text here.")

    def test_distinct_content_differs(self):
        assert compute_content_hash("a") != compute_content_hash("b")

    def test_unique_per_workspace(self, workspace, owner):
        make_document(workspace, owner)
        with pytest.raises(IntegrityError):
            make_document(workspace, owner)

    def test_same_hash_allowed_across_workspaces(self, workspace, owner, outsider):
        other = Workspace.objects.get(owner=outsider)
        make_document(workspace, owner)
        make_document(other, outsider)  # no error

    def test_null_content_hash_not_constrained(self, workspace, owner):
        """Partial unique index: any number of pending `url` documents
        (content_hash still null) may coexist without colliding."""
        make_document(workspace, owner, content_hash=None, raw_text=None)
        make_document(workspace, owner, content_hash=None, raw_text=None)  # no error

    def test_slot_frees_on_delete(self, workspace, owner):
        row = make_document(workspace, owner)
        row.delete()
        make_document(workspace, owner)  # no error


class TestScoping:
    def test_for_actor_hides_other_workspaces(self, workspace, owner, outsider):
        mine = make_document(workspace, owner)
        other_ws = Workspace.objects.get(owner=outsider)
        make_document(other_ws, outsider, content="Other secret.")
        visible = KnowledgeDocument.objects.for_actor(owner)
        assert list(visible) == [mine]

    def test_for_actor_unauthenticated_sees_nothing(self, workspace, owner):
        make_document(workspace, owner)
        assert KnowledgeDocument.objects.for_actor(None).count() == 0

    def test_chunk_for_actor_hides_other_workspaces(self, workspace, owner, outsider):
        document = make_document(workspace, owner)
        mine = make_chunk(document)
        other_ws = Workspace.objects.get(owner=outsider)
        other_doc = make_document(other_ws, outsider, content="Other secret.")
        make_chunk(other_doc)
        visible = KnowledgeChunk.objects.for_actor(owner)
        assert list(visible) == [mine]


class TestCloudEligible:
    def _fake_embedding_provider(self):
        return ModelProvider.objects.create(
            name="fake-embedding-local",
            provider_type=ModelProvider.ProviderType.FAKE,
            capability=ModelProvider.Capability.EMBEDDING,
            model_id="fake-embedding-1",
            dimension=8,
        )

    def test_excludes_unembedded_chunks(self, workspace, owner):
        document = make_document(workspace, owner, status=KnowledgeDocument.Status.READY)
        make_chunk(document)  # embedding still null
        assert KnowledgeChunk.objects.cloud_eligible().count() == 0

    def test_excludes_non_ready_documents(self, workspace, owner):
        document = make_document(workspace, owner, status=KnowledgeDocument.Status.PROCESSING)
        provider = self._fake_embedding_provider()
        make_chunk(document, embedding=[0.1] * 8, embedding_provider=provider)
        assert KnowledgeChunk.objects.cloud_eligible().count() == 0

    def test_excludes_private_documents_even_if_embedded(self, workspace, owner):
        """Defense in depth: even if a private chunk somehow had a non-null
        embedding, cloud_eligible() still excludes it."""
        document = make_document(
            workspace,
            owner,
            status=KnowledgeDocument.Status.READY,
            sensitivity=KnowledgeDocument.Sensitivity.PRIVATE,
        )
        provider = self._fake_embedding_provider()
        make_chunk(document, embedding=[0.1] * 8, embedding_provider=provider)
        assert KnowledgeChunk.objects.cloud_eligible().count() == 0

    def test_includes_ready_normal_embedded_chunks(self, workspace, owner):
        document = make_document(workspace, owner, status=KnowledgeDocument.Status.READY)
        provider = self._fake_embedding_provider()
        chunk = make_chunk(document, embedding=[0.1] * 8, embedding_provider=provider)
        assert list(KnowledgeChunk.objects.cloud_eligible()) == [chunk]


class TestChunkOrdinalConstraint:
    def test_unique_per_document(self, workspace, owner):
        document = make_document(workspace, owner)
        make_chunk(document, ordinal=0)
        with pytest.raises(IntegrityError):
            make_chunk(document, ordinal=0)

    def test_same_ordinal_allowed_across_documents(self, workspace, owner):
        doc_a = make_document(workspace, owner, content="doc a")
        doc_b = make_document(workspace, owner, content="doc b")
        make_chunk(doc_a, ordinal=0)
        make_chunk(doc_b, ordinal=0)  # no error
