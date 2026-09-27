"""`assistant.services.fork_conversation` (Phase 4 branch 6): a copied
server-side prefix, never client text, in the source's workspace."""

import uuid

import pytest

from accounts.services import register_user
from assistant.models import Conversation, Message
from assistant.services import fork_conversation
from audit.models import AuditLog
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice():
    return register_user(email="alice@test.dev", password="Str0ngDevPass!42", name="Alice")


@pytest.fixture
def source(alice):
    workspace = Workspace.objects.get(owner=alice)
    conversation = Conversation.objects.create(workspace=workspace, created_by=alice, title="T")
    rows = [
        ("user", "u1", Message.Intent.KNOWLEDGE_QUERY, {}),
        ("assistant", "a1", "", {}),
        ("user", "u2", Message.Intent.TASK_DRAFT, {"title": "x", "due_date": None}),
        ("assistant", "a2", "", {}),
    ]
    for role, content, intent, payload in rows:
        Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=role,
            content=content,
            intent=intent,
            intent_payload=payload,
            channel=Message.Channel.OPENAI,
            memories_used=["m1"] if role == "assistant" else [],
            overlay_source_workspaces={"m1": "w"} if role == "assistant" else {},
        )
    return conversation


def _ordered(conversation):
    return list(conversation.messages.order_by("created_at", "id"))


def test_fork_copies_the_prefix_before_the_cut(alice, source):
    cut = _ordered(source)[2]
    fork, id_map = fork_conversation(source=source, actor=alice, cut_before_id=cut.id)
    copies = _ordered(fork)
    assert [(m.role, m.content) for m in copies] == [("user", "u1"), ("assistant", "a1")]
    assert fork.workspace == source.workspace
    assert fork.title == "T"
    assert copies[0].intent == Message.Intent.KNOWLEDGE_QUERY
    assert copies[1].memories_used == ["m1"]
    assert copies[1].overlay_source_workspaces == {}
    assert copies[1].model_usage is None
    assert [m.created_at for m in copies] == [m.created_at for m in _ordered(source)[:2]]
    originals = _ordered(source)[:2]
    assert id_map == {str(o.id): str(c.id) for o, c in zip(originals, copies, strict=True)}
    fork.refresh_from_db()
    assert fork.last_extracted_message == copies[-1]
    assert len(_ordered(source)) == 4


def test_fork_without_a_usable_cut_copies_everything_but_drops_draft_intents(alice, source):
    fork, _ = fork_conversation(source=source, actor=alice, cut_before_id=uuid.uuid4())
    copies = _ordered(fork)
    assert len(copies) == 4
    assert copies[2].intent == ""
    assert copies[2].intent_payload == {}


def test_fork_is_audited(alice, source):
    fork, _ = fork_conversation(source=source, actor=alice, channel="openai")
    created = AuditLog.objects.get(action="assistant.conversation.created")
    assert created.metadata == {"private": False, "channel": "openai"}
    forked = AuditLog.objects.get(action="assistant.conversation.forked")
    assert forked.resource_id == str(fork.id)
    assert forked.metadata == {
        "source_conversation": str(source.id),
        "copied_messages": 4,
        "channel": "openai",
    }
