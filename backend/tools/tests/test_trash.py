"""ADR-009 (Phase 4 branch 2): owner deletes of knowledge documents and
tasks are reversible moves to the trash (confirm-class: human, web channel,
audited), retrieval ignores trashed documents, retention purges them, and
an early permanent purge is the gate-class `purge_trash` tool. Plus the
role x endpoint matrix for the three owner deletes (documents, tasks,
memory suggestions)."""

import secrets
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from approvals.models import ApprovalRequest
from approvals.services import InvalidTransition, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from knowledge.models import KnowledgeChunk, KnowledgeDocument, compute_content_hash
from knowledge.services import (
    DuplicateDocument,
    KnowledgePermissionDenied,
    delete_document,
    purge_expired_trashed_documents,
    restore_document,
)
from memory.models import Memory
from tasks.models import Task
from tasks.services import (
    TaskPermissionDenied,
    create_task,
    delete_task,
    purge_expired_trashed_tasks,
    restore_task,
)
from tools.registry import list_tool_names
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_document(workspace, owner, title="Doc", content="Some content"):
    return KnowledgeDocument.objects.create(
        workspace=workspace,
        source_type=KnowledgeDocument.SourceType.PASTED_TEXT,
        title=title,
        status=KnowledgeDocument.Status.READY,
        raw_text=content,
        content_hash=compute_content_hash(content),
        created_by=owner,
    )


def make_task(workspace, owner, title="Task"):
    return create_task(workspace=workspace, actor=owner, title=title)


# --- soft delete + trash + restore (services) ---


def test_task_delete_is_a_reversible_move_to_the_trash(workspace, owner, settings):
    task = make_task(workspace, owner)
    delete_task(task=task, actor=owner, channel=CHANNEL_WEB)
    assert not Task.objects.filter(pk=task.pk).exists()
    assert not Task.objects.for_actor(owner).filter(pk=task.pk).exists()
    trashed = Task.all_objects.get(pk=task.pk)
    assert trashed.deleted_at is not None
    deleted_log = AuditLog.objects.get(action="task.deleted", resource_id=str(task.pk))
    assert deleted_log.metadata["retention_days"] == settings.TRASH_RETENTION_DAYS

    restore_task(task=trashed, actor=owner, channel=CHANNEL_WEB)
    assert Task.objects.get(pk=task.pk).deleted_at is None
    assert AuditLog.objects.filter(action="task.restored").exists()


def test_document_delete_keeps_chunks_but_hides_them_from_retrieval(workspace, owner):
    document = make_document(workspace, owner)
    KnowledgeChunk.objects.create(
        workspace=workspace, document=document, ordinal=0, content="x", embedding=[0.1, 0.2]
    )
    assert KnowledgeChunk.objects.cloud_eligible().count() == 1
    delete_document(document=document, actor=owner, channel=CHANNEL_WEB)
    assert KnowledgeChunk.objects.filter(document=document).count() == 1
    assert KnowledgeChunk.objects.cloud_eligible().count() == 0
    restore_document(document=document, actor=owner, channel=CHANNEL_WEB)
    assert KnowledgeChunk.objects.cloud_eligible().count() == 1


def test_reupload_while_trashed_is_allowed_and_restore_refuses_the_duplicate(workspace, owner):
    first = make_document(workspace, owner, content="Same text")
    delete_document(document=first, actor=owner, channel=CHANNEL_WEB)
    make_document(workspace, owner, title="Again", content="Same text")
    with pytest.raises(DuplicateDocument):
        restore_document(document=first, actor=owner, channel=CHANNEL_WEB)


@pytest.mark.parametrize("channel", [CHANNEL_CLI, "telegram", "unknown", "system"])
def test_delete_and_restore_require_the_web_channel(workspace, owner, channel):
    task = make_task(workspace, owner)
    document = make_document(workspace, owner)
    with pytest.raises(TaskPermissionDenied):
        delete_task(task=task, actor=owner, channel=channel)
    with pytest.raises(KnowledgePermissionDenied):
        delete_document(document=document, actor=owner, channel=channel)
    delete_task(task=task, actor=owner, channel=CHANNEL_WEB)
    with pytest.raises(TaskPermissionDenied):
        restore_task(task=task, actor=owner, channel=channel)


def test_an_agent_actor_can_never_delete(workspace, owner):
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    WorkspaceMember.objects.create(workspace=workspace, user=agent, role="owner")
    task = make_task(workspace, owner)
    with pytest.raises(TaskPermissionDenied):
        delete_task(task=task, actor=agent, channel=CHANNEL_WEB)
    with pytest.raises(KnowledgePermissionDenied):
        delete_document(document=make_document(workspace, owner), actor=agent, channel=CHANNEL_WEB)


# --- retention purge (beat) ---


def test_retention_purge_removes_only_rows_older_than_the_window(workspace, owner, settings):
    old_task, new_task = make_task(workspace, owner, "old"), make_task(workspace, owner, "new")
    old_doc = make_document(workspace, owner, "old", "old content")
    for row in (old_task, new_task):
        delete_task(task=row, actor=owner, channel=CHANNEL_WEB)
    delete_document(document=old_doc, actor=owner, channel=CHANNEL_WEB)
    long_ago = timezone.now() - timedelta(days=settings.TRASH_RETENTION_DAYS + 1)
    Task.all_objects.filter(pk=old_task.pk).update(deleted_at=long_ago)
    KnowledgeDocument.all_objects.filter(pk=old_doc.pk).update(deleted_at=long_ago)

    assert purge_expired_trashed_tasks() == 1
    assert purge_expired_trashed_documents() == 1
    assert not Task.all_objects.filter(pk=old_task.pk).exists()
    assert Task.all_objects.filter(pk=new_task.pk).exists()
    assert not KnowledgeDocument.all_objects.filter(pk=old_doc.pk).exists()
    purged = AuditLog.objects.get(action="task.purged")
    assert purged.metadata["reason"] == "retention"
    assert purged.actor is None and purged.actor_type == AuditLog.ActorType.SYSTEM


# --- gate-class purge_trash ---


def _trash(workspace, owner):
    task = make_task(workspace, owner, "Trashed task")
    document = make_document(workspace, owner, "Trashed doc")
    delete_task(task=task, actor=owner, channel=CHANNEL_WEB)
    delete_document(document=document, actor=owner, channel=CHANNEL_WEB)
    return task, document


def test_purge_trash_is_gated_and_runs_only_on_an_explicit_web_decision(
    workspace, owner, web_client
):
    task, document = _trash(workspace, owner)
    client = web_client(owner)
    created = client.post(
        "/api/v1/approvals/requests",
        {
            "workspace": str(workspace.id),
            "tool_name": "purge_trash",
            "payload": {"task_ids": [str(task.pk)], "document_ids": [str(document.pk)]},
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    summary = created.json()["display_summary"]
    assert summary["kind"] == "purge_trash"
    assert summary["task_count"] == 1 and summary["document_count"] == 1
    assert sorted(summary["titles"]) == ["Trashed doc", "Trashed task"]
    # Requesting purged nothing.
    assert Task.all_objects.filter(pk=task.pk).exists()

    decided = client.post(f"/api/v1/approvals/requests/{created.json()['id']}/approve")
    assert decided.status_code == 200
    assert not Task.all_objects.filter(pk=task.pk).exists()
    assert not KnowledgeDocument.all_objects.filter(pk=document.pk).exists()
    logs = AuditLog.objects.filter(action__in=["task.purged", "knowledge.purged"])
    assert logs.count() == 2
    assert {log.metadata["reason"] for log in logs} == {"approval"}
    assert {log.metadata["approval_request_id"] for log in logs} == {created.json()["id"]}
    assert all(log.actor == owner for log in logs)


def test_a_row_restored_before_the_decision_survives_the_purge(workspace, owner, web_client):
    task, document = _trash(workspace, owner)
    request = create_request(
        workspace=workspace,
        actor=owner,
        tool_name="purge_trash",
        payload={"task_ids": [str(task.pk)], "document_ids": [str(document.pk)]},
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )
    restore_task(task=task, actor=owner, channel=CHANNEL_WEB)
    web_client(owner).post(f"/api/v1/approvals/requests/{request.id}/approve")
    assert Task.objects.filter(pk=task.pk).exists()
    assert not KnowledgeDocument.all_objects.filter(pk=document.pk).exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"task_ids": []},
        {"task_ids": "not-a-list"},
        {"task_ids": ["not-a-uuid"]},
        {"bogus": []},
        {"task_ids": ["LIVE"]},
        {"task_ids": ["FOREIGN"]},
    ],
)
def test_purge_trash_refuses_anything_but_this_workspaces_trash(workspace, owner, payload):
    live = make_task(workspace, owner)
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    foreign_ws = Workspace.objects.get(owner=stranger)
    foreign = make_task(foreign_ws, stranger)
    delete_task(task=foreign, actor=stranger, channel=CHANNEL_WEB)
    ids = payload.get("task_ids")
    if ids == ["LIVE"]:
        payload = {"task_ids": [str(live.pk)]}
    elif ids == ["FOREIGN"]:
        payload = {"task_ids": [str(foreign.pk)]}
    with pytest.raises(InvalidTransition):
        create_request(
            workspace=workspace,
            actor=owner,
            tool_name="purge_trash",
            payload=payload,
            origin=ApprovalRequest.Origin.HUMAN,
            channel=CHANNEL_WEB,
        )


def test_purge_trash_is_human_only_and_never_proposed(workspace, owner):
    task, _ = _trash(workspace, owner)
    assert "purge_trash" not in list_tool_names()
    for origin in (ApprovalRequest.Origin.AGENT, ApprovalRequest.Origin.HARNESS):
        with pytest.raises(InvalidTransition):
            create_request(
                workspace=workspace,
                actor=owner,
                tool_name="purge_trash",
                payload={"task_ids": [str(task.pk)]},
                origin=origin,
                channel="system",
            )


# --- trash/restore endpoints ---


def test_trash_listing_and_restore_endpoints(workspace, owner, web_client):
    task, document = _trash(workspace, owner)
    client = web_client(owner)
    tasks = client.get("/api/v1/tasks/trash", {"workspace": str(workspace.id)}).json()
    docs = client.get("/api/v1/knowledge/documents/trash", {"workspace": str(workspace.id)}).json()
    assert [t["id"] for t in tasks] == [str(task.pk)] and tasks[0]["deleted_at"]
    assert [d["id"] for d in docs] == [str(document.pk)]
    assert client.get("/api/v1/tasks", {"workspace": str(workspace.id)}).json() == []

    assert client.post(f"/api/v1/tasks/{task.pk}/restore").status_code == 200
    assert client.post(f"/api/v1/knowledge/documents/{document.pk}/restore").status_code == 200
    assert client.post(f"/api/v1/tasks/{task.pk}/restore").status_code == 404  # not trashed


# --- role x endpoint: the three owner deletes ---


def _principal(kind, owner, workspace, web_client):
    if kind == "owner-web":
        return web_client(owner)
    if kind == "owner-pat":
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        PersonalAccessToken.objects.create(
            workspace=workspace, user=owner, name="cli", token_hash=hash_token(raw)
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client
    if kind == "non-owner":
        viewer = register_user(email="viewer@test.dev", password=PASSWORD)
        WorkspaceMember.objects.create(workspace=workspace, user=viewer, role="viewer")
        return web_client(viewer)
    if kind == "agent":
        agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
        WorkspaceMember.objects.create(workspace=workspace, user=agent, role="owner")
        return web_client(agent)
    return APIClient()


def _target(kind, workspace, owner):
    if kind == "task":
        return f"/api/v1/tasks/{make_task(workspace, owner).pk}"
    if kind == "document":
        return f"/api/v1/knowledge/documents/{make_document(workspace, owner).pk}"
    memory = Memory.objects.create(
        workspace=workspace,
        title="Suggestion",
        content="x",
        status=Memory.Status.PENDING_APPROVAL,
        source=Memory.Source.AI_SUGGESTED,
    )
    return f"/api/v1/memories/{memory.pk}"


@pytest.mark.parametrize("target", ["task", "document", "memory"])
@pytest.mark.parametrize(
    "principal, expected",
    [
        ("owner-web", 204),
        ("owner-pat", 401),
        ("non-owner", 403),
        ("agent", 403),
        ("anonymous", 401),
    ],
)
def test_owner_delete_role_matrix(workspace, owner, web_client, target, principal, expected):
    url = _target(target, workspace, owner)
    response = _principal(principal, owner, workspace, web_client).delete(url)
    assert response.status_code == expected, response.content
