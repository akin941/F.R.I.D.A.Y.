import pytest

from accounts.services import register_user
from assistant.models import Conversation, Message
from memory.jobs import create_extraction_job, message_range
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def workspace(alice):
    return Workspace.objects.get(owner=alice)


@pytest.fixture
def conversation(workspace, alice):
    return Conversation.objects.create(workspace=workspace, created_by=alice)


def make_message(conversation, content, role=Message.Role.ASSISTANT):
    return Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=role,
        content=content,
        status=Message.Status.COMPLETE,
    )


class TestMessageRange:
    def test_first_job_includes_everything_up_to_through_message(self, conversation):
        make_message(conversation, "one")
        m2 = make_message(conversation, "two")
        make_message(conversation, "later, not in range")
        job = create_extraction_job(conversation=conversation, through_message=m2)
        contents = list(message_range(job).values_list("content", flat=True))
        assert contents == ["one", "two"]
        assert "later, not in range" not in contents

    def test_second_job_excludes_the_first_range(self, conversation):
        m1 = make_message(conversation, "one")
        m2 = make_message(conversation, "two")
        create_extraction_job(conversation=conversation, through_message=m2)
        m3 = make_message(conversation, "three")
        m4 = make_message(conversation, "four")
        job_two = create_extraction_job(conversation=conversation, through_message=m4)
        contents = list(message_range(job_two).values_list("content", flat=True))
        assert contents == ["three", "four"]
        assert "one" not in contents
        assert "two" not in contents
        assert m1 and m3  # unused-var guard

    def test_range_is_ordered_by_created_at_then_id(self, conversation):
        messages = [make_message(conversation, f"msg-{i}") for i in range(5)]
        job = create_extraction_job(conversation=conversation, through_message=messages[-1])
        contents = list(message_range(job).values_list("content", flat=True))
        assert contents == [f"msg-{i}" for i in range(5)]

    def test_id_tiebreak_partitions_a_frozen_tie_without_gap_or_overlap(self, conversation):
        """UUID4 ids are random, not monotonic, so an `id` tiebreak on a
        genuine `created_at` tie does not necessarily preserve true
        chronological order — that isn't the invariant it exists for. What
        must hold is the range-partition guarantee: two adjacent jobs never
        double-count or drop a tied-timestamp message between them."""
        import datetime

        m1 = make_message(conversation, "a")
        m2 = make_message(conversation, "b")
        frozen = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        Message.objects.filter(pk__in=[m1.pk, m2.pk]).update(created_at=frozen)
        m1.refresh_from_db()
        m2.refresh_from_db()
        lower, upper = sorted([m1, m2], key=lambda m: m.id)

        job_one = create_extraction_job(conversation=conversation, through_message=lower)
        job_two = create_extraction_job(conversation=conversation, through_message=upper)

        range_one = set(message_range(job_one).values_list("id", flat=True))
        range_two = set(message_range(job_two).values_list("id", flat=True))
        assert range_one == {lower.id}
        assert range_two == {upper.id}
        assert range_one.isdisjoint(range_two)  # no message double-counted

    def test_through_message_workspace_mismatch_is_rejected_at_job_creation(
        self, conversation, alice
    ):
        """create_extraction_job itself must refuse to build a job whose
        range-end belongs to a different workspace — silently accepting it
        would both leak a foreign FK reference and advance the conversation's
        own cursor to a message outside its own workspace, corrupting all
        later allocation for this conversation."""
        other_workspace = Workspace.objects.create(name="Other", owner=alice)
        foreign = Message.objects.create(
            workspace=other_workspace,  # mismatched on purpose
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="foreign",
            status=Message.Status.COMPLETE,
        )
        with pytest.raises(ValueError, match="must belong to the given conversation"):
            create_extraction_job(conversation=conversation, through_message=foreign)
        conversation.refresh_from_db()
        assert conversation.last_extracted_message_id is None  # cursor untouched

    def test_through_message_wrong_conversation_is_rejected_at_job_creation(
        self, conversation, workspace, alice
    ):
        other_conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        other_message = make_message(other_conversation, "elsewhere")
        with pytest.raises(ValueError, match="must belong to the given conversation"):
            create_extraction_job(conversation=conversation, through_message=other_message)

    def test_message_range_filter_defends_against_in_range_corruption(self, conversation, alice):
        """message_range's own workspace_id filter is defense-in-depth for a
        message BETWEEN the boundaries getting corrupted after the job
        already exists (e.g. a stray direct DB update) — not reachable
        through normal application code, mirroring
        assistant.context.build_context's identical guard."""
        make_message(conversation, "good")
        corrupted = make_message(conversation, "corrupted")
        through = make_message(conversation, "through")
        other_workspace = Workspace.objects.create(name="Other", owner=alice)
        Message.objects.filter(pk=corrupted.pk).update(workspace=other_workspace)

        job = create_extraction_job(conversation=conversation, through_message=through)
        contents = list(message_range(job).values_list("content", flat=True))
        assert contents == ["good", "through"]
        assert "corrupted" not in contents
