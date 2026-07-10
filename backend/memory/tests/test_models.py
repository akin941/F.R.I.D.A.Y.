import pytest
from django.db import IntegrityError

from accounts.services import register_user
from memory.models import Memory, compute_fingerprint
from workspaces.models import Workspace, WorkspaceMember

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


def make_memory(workspace, actor, content="Drinks coffee black.", **overrides):
    fields = {
        "workspace": workspace,
        "memory_type": Memory.Type.PREFERENCE,
        "status": Memory.Status.APPROVED,
        "source": Memory.Source.USER_CREATED,
        "title": "Coffee",
        "content": content,
        "fingerprint": compute_fingerprint(content),
        "created_by": actor,
    }
    fields.update(overrides)
    return Memory.objects.create(**fields)


class TestFingerprint:
    def test_normalizes_case_and_whitespace(self):
        assert compute_fingerprint("Drinks  Coffee\nBLACK.") == compute_fingerprint(
            "drinks coffee black."
        )

    def test_distinct_content_differs(self):
        assert compute_fingerprint("a") != compute_fingerprint("b")

    def test_unique_per_workspace_including_rejected(self, workspace, owner):
        make_memory(workspace, owner, status=Memory.Status.REJECTED)
        with pytest.raises(IntegrityError):
            make_memory(workspace, owner)

    def test_same_fingerprint_allowed_across_workspaces(self, workspace, owner, outsider):
        other = Workspace.objects.get(owner=outsider)
        make_memory(workspace, owner)
        make_memory(other, outsider)  # no error

    def test_slot_frees_on_hard_delete(self, workspace, owner):
        row = make_memory(workspace, owner, status=Memory.Status.REJECTED)
        row.delete()
        make_memory(workspace, owner)  # no error


class TestScoping:
    def test_for_actor_hides_other_workspaces(self, workspace, owner, outsider):
        mine = make_memory(workspace, owner)
        other_ws = Workspace.objects.get(owner=outsider)
        make_memory(other_ws, outsider, content="Other secret.")
        visible = Memory.objects.for_actor(owner)
        assert list(visible) == [mine]

    def test_for_actor_unauthenticated_sees_nothing(self, workspace, owner):
        make_memory(workspace, owner)
        assert Memory.objects.for_actor(None).count() == 0

    def test_member_sees_workspace_memories(self, workspace, owner, outsider):
        WorkspaceMember.objects.create(
            workspace=workspace, user=outsider, role=WorkspaceMember.Role.VIEWER
        )
        row = make_memory(workspace, owner)
        assert list(Memory.objects.for_actor(outsider, workspace=workspace)) == [row]


class TestCloudEligible:
    def test_excludes_private_and_non_approved(self, workspace, owner):
        eligible = make_memory(workspace, owner, content="normal approved")
        make_memory(
            workspace,
            owner,
            content="private approved",
            sensitivity=Memory.Sensitivity.PRIVATE,
        )
        make_memory(workspace, owner, content="pending", status=Memory.Status.PENDING_APPROVAL)
        make_memory(workspace, owner, content="archived", status=Memory.Status.ARCHIVED)
        make_memory(workspace, owner, content="rejected", status=Memory.Status.REJECTED)
        assert list(Memory.objects.for_actor(owner).cloud_eligible()) == [eligible]


class TestProvenanceInvariant:
    def test_source_conversation_must_share_workspace(self, workspace, owner, outsider):
        from assistant.models import Conversation

        other_ws = Workspace.objects.get(owner=outsider)
        foreign_conversation = Conversation.objects.create(workspace=other_ws, created_by=outsider)
        with pytest.raises(ValueError, match="must belong to the memory's workspace"):
            make_memory(workspace, owner, source_conversation=foreign_conversation)

    def test_same_workspace_provenance_is_allowed(self, workspace, owner):
        from assistant.models import Conversation

        conversation = Conversation.objects.create(workspace=workspace, created_by=owner)
        row = make_memory(workspace, owner, source_conversation=conversation)
        assert row.source_conversation == conversation
