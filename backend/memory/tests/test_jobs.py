import threading
import uuid
from itertools import pairwise
from unittest import mock

import pytest
from django.db import connection

from accounts.services import register_user
from assistant.models import Conversation, Message

# Private, but this is exactly the production unit whose locking behavior the
# concurrency test below verifies — going through the public SSE-streaming
# API from two threads would be awkward for no added assurance (Codex
# round-1 finding: a test that reimplements lock->insert->allocate itself
# would stay green even if the production select_for_update were removed).
from assistant.services import _persist_assistant_message
from memory.jobs import create_extraction_job
from memory.models import MemoryExtractionJob
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


def make_message(conversation, content="hi"):
    return Message.objects.create(
        workspace=conversation.workspace,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        status=Message.Status.COMPLETE,
    )


class TestAllocation:
    def test_first_job_has_no_lower_bound(self, conversation):
        message = make_message(conversation)
        job = create_extraction_job(conversation=conversation, through_message=message)
        assert job.after_message is None
        assert job.through_message == message
        assert job.status == MemoryExtractionJob.Status.PENDING
        assert job.workspace == conversation.workspace

    def test_cursor_advances_and_next_job_is_adjacent(self, conversation):
        first_message = make_message(conversation, content="first")
        job_one = create_extraction_job(conversation=conversation, through_message=first_message)
        conversation.refresh_from_db()
        assert conversation.last_extracted_message_id == first_message.id

        second_message = make_message(conversation, content="second")
        job_two = create_extraction_job(conversation=conversation, through_message=second_message)
        assert job_two.after_message_id == job_one.through_message_id
        conversation.refresh_from_db()
        assert conversation.last_extracted_message_id == second_message.id

    def test_ranges_never_overlap_across_three_jobs(self, conversation):
        jobs = []
        for i in range(3):
            message = make_message(conversation, content=f"reply {i}")
            jobs.append(create_extraction_job(conversation=conversation, through_message=message))
        for earlier, later in pairwise(jobs):
            assert later.after_message_id == earlier.through_message_id

    def test_unique_constraint_on_conversation_and_through_message(self, conversation):
        from django.db import IntegrityError

        message = make_message(conversation)
        create_extraction_job(conversation=conversation, through_message=message)
        with pytest.raises(IntegrityError):
            create_extraction_job(conversation=conversation, through_message=message)

    def test_different_conversations_do_not_share_a_cursor(self, workspace, alice, conversation):
        other_conversation = Conversation.objects.create(workspace=workspace, created_by=alice)
        message_a = make_message(conversation)
        job_a = create_extraction_job(conversation=conversation, through_message=message_a)
        message_b = make_message(other_conversation)
        job_b = create_extraction_job(conversation=other_conversation, through_message=message_b)
        assert job_a.after_message is None
        assert job_b.after_message is None  # independent cursor, not affected by job_a


@pytest.mark.django_db(transaction=True)
def test_concurrent_persists_allocate_adjacent_non_overlapping_ranges():
    """Genuine two-connection concurrency test (Codex round-3 requirement,
    carried forward from the brief): two threads race to persist a COMPLETE
    assistant reply on the same conversation, through the actual production
    function (`assistant.services._persist_assistant_message`) — not a
    hand-rolled reimplementation of its locking, so this test would fail if
    that function's `select_for_update` were ever removed. Locking
    Conversation FIRST (inside each call's own transaction) must serialize
    the two persists in commit order, so whichever job committed second has
    `after_message` equal to the first job's `through_message` — never
    overlapping, never both null.

    Real dispatch is patched out (Codex round-1-of-branch-4 finding): since
    MVP 2 §4, `create_extraction_job` schedules a real `on_commit` dispatch,
    which genuinely fires under `transaction=True` — this test is about
    allocation/locking, not the worker pipeline (covered in
    memory/tests/test_tasks.py), so the auto-dispatched task must not run
    here (it would otherwise process each job inline and mutate its status/
    retry_count as an unrelated side effect of a test that never seeded the
    extraction prompt).
    """
    alice = register_user(email="racer@test.dev", password=PASSWORD, name="Racer")
    workspace = Workspace.objects.get(owner=alice)
    conversation = Conversation.objects.create(workspace=workspace, created_by=alice)

    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key, content):
        try:
            barrier.wait(timeout=5)
            message = _persist_assistant_message(
                conversation,
                message_id=uuid.uuid4(),
                content=content,
                status=Message.Status.COMPLETE,
                usage=None,
                memory_ids=[],
                chunk_ids=[],
            )
            results[key] = message
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("a", "reply one"))
    t2 = threading.Thread(target=worker, args=("b", "reply two"))
    # Patched once, around both threads (not inside each worker): mock.patch
    # mutates the module-global attribute process-wide, so two threads each
    # independently entering/exiting their own `with mock.patch(...)` block
    # on the same target could race — one thread's __exit__ restoring the
    # original could clobber the other's still-active patch.
    with mock.patch("memory.jobs.dispatch_extraction_job"):
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"a", "b"}
    jobs = list(
        MemoryExtractionJob.objects.filter(conversation=conversation).order_by("created_at")
    )
    assert len(jobs) == 2
    first, second = jobs
    assert first.after_message_id is None
    assert second.after_message_id == first.through_message_id
    assert first.through_message_id != second.through_message_id
    assert {first.through_message_id, second.through_message_id} == {
        results["a"].id,
        results["b"].id,
    }


class TestDeletionSafety:
    """Codex round-1 High finding: SET_NULL/CASCADE on message references let
    a deleted message silently reset the cursor to null, so the next job
    would re-cover a range an earlier surviving job already claimed. Both
    the cursor and job message references are on_delete=RESTRICT instead:
    a standalone message delete fails loudly, but deleting the whole
    Conversation (which cascades to its messages and jobs together) still
    works — RESTRICT, unlike PROTECT, only blocks when the FK holder is NOT
    part of the same cascading deletion (Codex round-2 finding: PROTECT
    over-blocked the legitimate Conversation-delete cascade)."""

    def test_deleting_the_cursor_message_is_restricted(self, conversation):
        from django.db.models import RestrictedError

        message = make_message(conversation)
        create_extraction_job(conversation=conversation, through_message=message)
        with pytest.raises(RestrictedError):
            message.delete()
        conversation.refresh_from_db()
        assert conversation.last_extracted_message_id == message.id  # unchanged, not NULL

    def test_deleting_an_older_through_message_is_restricted(self, conversation):
        from django.db.models import RestrictedError

        older = make_message(conversation, content="older")
        create_extraction_job(conversation=conversation, through_message=older)
        newer = make_message(conversation, content="newer")
        create_extraction_job(conversation=conversation, through_message=newer)

        with pytest.raises(RestrictedError):
            older.delete()  # not the current cursor, but still a job's range end

    def test_deleting_the_whole_conversation_cascades_cleanly(self, conversation):
        """The RESTRICT relations must not block the legitimate, aggregate
        deletion of a Conversation — only standalone message deletes."""
        message = make_message(conversation)
        job = create_extraction_job(conversation=conversation, through_message=message)

        conversation.delete()

        assert not Message.objects.filter(pk=message.pk).exists()
        assert not MemoryExtractionJob.objects.filter(pk=job.pk).exists()
