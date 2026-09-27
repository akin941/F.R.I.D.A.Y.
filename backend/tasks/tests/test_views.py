import uuid

import pytest
from rest_framework.test import APIClient

from accounts.services import register_user
from assistant.models import Conversation, Message
from audit.models import AuditLog
from tasks.models import Task
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"

ROLES = [
    WorkspaceMember.Role.OWNER,
    WorkspaceMember.Role.ADMIN,
    WorkspaceMember.Role.MEMBER,
    WorkspaceMember.Role.VIEWER,
]


@pytest.fixture
def owner():
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def outsider():
    return register_user(email="outsider@test.dev", password=PASSWORD, name="Outsider")


def user_with_role(workspace, role, email):
    user = register_user(email=email, password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=role)
    return user


def make_task(workspace, title="Buy milk", **overrides):
    fields = {"workspace": workspace, "title": title}
    fields.update(overrides)
    return Task.objects.create(**fields)


def create_payload(workspace, **overrides):
    payload = {"workspace": str(workspace.id), "title": "Buy milk"}
    payload.update(overrides)
    return payload


class TestListCreate:
    def test_requires_auth(self, workspace):
        response = APIClient().get("/api/v1/tasks", {"workspace": str(workspace.id)})
        assert response.status_code == 401

    def test_requires_workspace_param(self, owner, web_client):
        assert web_client(owner).get("/api/v1/tasks").status_code == 400

    def test_list_filters_by_status(self, workspace, owner, web_client):
        open_task = make_task(workspace)
        make_task(workspace, title="done one", status=Task.Status.DONE)
        response = web_client(owner).get(
            "/api/v1/tasks", {"workspace": str(workspace.id), "status": "open"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(open_task.id)]

    def test_invalid_status_filter_is_400(self, workspace, owner, web_client):
        response = web_client(owner).get(
            "/api/v1/tasks", {"workspace": str(workspace.id), "status": "bogus"}
        )
        assert response.status_code == 400

    def test_due_date_filters(self, workspace, owner, web_client):
        today_task = make_task(workspace, title="today", due_date="2026-07-08")
        make_task(workspace, title="future", due_date="2026-08-01")
        make_task(workspace, title="undated")
        response = web_client(owner).get(
            "/api/v1/tasks", {"workspace": str(workspace.id), "due_before": "2026-07-08"}
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(today_task.id)]

    def test_invalid_date_filter_is_400(self, workspace, owner, web_client):
        response = web_client(owner).get(
            "/api/v1/tasks", {"workspace": str(workspace.id), "due_before": "not-a-date"}
        )
        assert response.status_code == 400

    def test_ordering_by_due_date_then_priority(self, workspace, owner, web_client):
        low_today = make_task(
            workspace, title="low today", due_date="2026-07-08", priority=Task.Priority.LOW
        )
        high_today = make_task(
            workspace, title="high today", due_date="2026-07-08", priority=Task.Priority.HIGH
        )
        later = make_task(
            workspace, title="later", due_date="2026-07-09", priority=Task.Priority.HIGH
        )
        response = web_client(owner).get("/api/v1/tasks", {"workspace": str(workspace.id)})
        ids = [row["id"] for row in response.json()]
        assert ids == [str(high_today.id), str(low_today.id), str(later.id)]

    def test_out_of_scope_workspace_is_404(self, workspace, outsider, web_client):
        response = web_client(outsider).get("/api/v1/tasks", {"workspace": str(workspace.id)})
        assert response.status_code == 404

    def test_create_ignores_client_supplied_server_fields(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/tasks",
            {
                **create_payload(workspace),
                "status": "done",
                "created_by": None,
                "source": "ai_suggested",
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "open"
        assert body["created_by"] == str(owner.id)
        # `source` is never accepted as a client-asserted value either (MVP 6
        # §3) -- only a verified `draft_message_id` can produce anything but
        # the default `user_created` (see TestDraftMessageId below).
        assert body["source"] == Task.Source.USER_CREATED

    def test_create_empty_title_is_400(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/tasks", create_payload(workspace, title="   "), format="json"
        )
        assert response.status_code == 400


class TestPermissionMatrix:
    """Role x endpoint matrix (CLAUDE.md rule 7)."""

    @pytest.mark.parametrize("role", ROLES)
    def test_all_roles_can_read(self, workspace, role, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        make_task(workspace)
        response = web_client(user).get("/api/v1/tasks", {"workspace": str(workspace.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 201),
            (WorkspaceMember.Role.ADMIN, 201),
            (WorkspaceMember.Role.MEMBER, 201),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_create(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        response = web_client(user).post("/api/v1/tasks", create_payload(workspace), format="json")
        assert response.status_code == expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 200),
            (WorkspaceMember.Role.ADMIN, 200),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_edit_foreign_row(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_task(workspace)  # created_by is None -> not the actor's own
        response = web_client(user).patch(
            f"/api/v1/tasks/{row.id}", {"title": "New"}, format="json"
        )
        assert response.status_code == expected

    def test_member_edits_own_row(self, workspace, web_client):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "m@test.dev")
        row = make_task(workspace, created_by=user)
        response = web_client(user).patch(
            f"/api/v1/tasks/{row.id}", {"title": "Mine"}, format="json"
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 200),
            (WorkspaceMember.Role.ADMIN, 200),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    @pytest.mark.parametrize("action", ["complete", "cancel"])
    def test_transition_foreign_row(self, workspace, role, expected, action, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_task(workspace)  # created_by is None -> not the actor's own
        response = web_client(user).post(f"/api/v1/tasks/{row.id}/{action}", format="json")
        assert response.status_code == expected

    def test_member_transitions_own_row(self, workspace, web_client):
        user = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "own@test.dev")
        row = make_task(workspace, created_by=user)
        response = web_client(user).post(f"/api/v1/tasks/{row.id}/complete", format="json")
        assert response.status_code == 200
        assert response.json()["status"] == "done"

    @pytest.mark.parametrize(
        "role,expected",
        [
            (WorkspaceMember.Role.OWNER, 204),
            (WorkspaceMember.Role.ADMIN, 204),
            (WorkspaceMember.Role.MEMBER, 403),
            (WorkspaceMember.Role.VIEWER, 403),
        ],
    )
    def test_delete_foreign_row(self, workspace, role, expected, web_client):
        user = user_with_role(workspace, role, f"role-{role}@test.dev")
        row = make_task(workspace)
        response = web_client(user).delete(f"/api/v1/tasks/{row.id}")
        assert response.status_code == expected


class TestDetailAndTransitions:
    def test_out_of_scope_detail_is_404_not_403(self, workspace, outsider, web_client):
        row = make_task(workspace)
        client = web_client(outsider)
        assert client.get(f"/api/v1/tasks/{row.id}").status_code == 404
        assert (
            client.patch(f"/api/v1/tasks/{row.id}", {"title": "X"}, format="json").status_code
            == 404
        )
        assert client.post(f"/api/v1/tasks/{row.id}/complete", format="json").status_code == 404

    def test_unknown_transition_is_404(self, workspace, owner, web_client):
        row = make_task(workspace)
        response = web_client(owner).post(f"/api/v1/tasks/{row.id}/promote", format="json")
        assert response.status_code == 404

    def test_illegal_transition_is_400(self, workspace, owner, web_client):
        row = make_task(workspace, status=Task.Status.DONE)
        response = web_client(owner).post(f"/api/v1/tasks/{row.id}/cancel", format="json")
        assert response.status_code == 400

    def test_reopen_from_cancelled_via_http(self, workspace, owner, web_client):
        row = make_task(workspace, status=Task.Status.CANCELLED)
        response = web_client(owner).post(f"/api/v1/tasks/{row.id}/reopen", format="json")
        assert response.status_code == 200
        assert response.json()["status"] == "open"


class TestDraftMessageId:
    """MVP 6 §3: `draft_message_id` is a server-verified REFERENCE, never a
    client-asserted claim. Absent -> unchanged `user_created` path; a valid,
    same-actor/same-workspace/`intent="task_draft"` reference -> the ONLY way
    to get `ai_suggested`; anything else -> a hard 400 with no `Task` row
    created at all (never a silent downgrade to `user_created`)."""

    def _conversation(self, workspace, actor):
        return Conversation.objects.create(workspace=workspace, created_by=actor)

    def _task_draft_message(self, workspace, conversation, actor, intent=Message.Intent.TASK_DRAFT):
        return Message.objects.create(
            workspace=workspace,
            conversation=conversation,
            role=Message.Role.USER,
            content="remind me to call mom",
            created_by=actor,
            intent=intent,
            intent_payload={"title": "Call mom", "due_date": None},
        )

    def test_absent_draft_message_id_creates_user_created_task(self, workspace, owner, web_client):
        response = web_client(owner).post("/api/v1/tasks", create_payload(workspace), format="json")
        assert response.status_code == 201
        assert response.json()["source"] == Task.Source.USER_CREATED

    def test_valid_draft_message_id_creates_ai_suggested_task(self, workspace, owner, web_client):
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(draft_message.id)),
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["source"] == Task.Source.AI_SUGGESTED
        # Phase 4 branch 4: the verified draft is linked as the task's
        # source message (what lets a reloaded chat show it as confirmed).
        assert Task.objects.get().source_message_id == draft_message.id

    def test_manual_create_links_no_source_message(self, workspace, owner, web_client):
        response = web_client(owner).post("/api/v1/tasks", create_payload(workspace), format="json")
        assert response.status_code == 201
        assert Task.objects.get().source_message_id is None

    def test_second_confirm_of_the_same_draft_returns_400_and_creates_nothing(
        self, workspace, owner, web_client
    ):
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        client = web_client(owner)
        payload = create_payload(workspace, draft_message_id=str(draft_message.id))

        first = client.post("/api/v1/tasks", payload, format="json")
        second = client.post("/api/v1/tasks", payload, format="json")

        assert first.status_code == 201
        assert second.status_code == 400
        assert second.json()["code"] == "task.draft_already_confirmed"
        assert Task.objects.count() == 1
        assert AuditLog.objects.filter(action="task.created").count() == 1

    def test_second_confirm_by_another_member_is_still_refused(self, workspace, owner, web_client):
        # Even a draft the second caller could not have confirmed anyway
        # (wrong actor -> 400 from verification) never yields a second task.
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        member = user_with_role(workspace, WorkspaceMember.Role.MEMBER, "draft-member@test.dev")
        payload = create_payload(workspace, draft_message_id=str(draft_message.id))

        assert web_client(owner).post("/api/v1/tasks", payload, format="json").status_code == 201
        assert web_client(member).post("/api/v1/tasks", payload, format="json").status_code == 400
        assert Task.objects.count() == 1

    def test_draft_can_be_confirmed_again_after_its_task_is_deleted(
        self, workspace, owner, web_client
    ):
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        client = web_client(owner)
        payload = create_payload(workspace, draft_message_id=str(draft_message.id))
        task_id = client.post("/api/v1/tasks", payload, format="json").json()["id"]

        assert client.delete(f"/api/v1/tasks/{task_id}").status_code == 204
        assert client.post("/api/v1/tasks", payload, format="json").status_code == 201

    def test_restoring_a_trashed_task_after_reconfirm_does_not_collide(
        self, workspace, owner, web_client
    ):
        """Trash releases the draft link (ADR-009 x branch 4): the draft can
        be re-confirmed, and restoring the trashed task later brings it back
        as an independent task instead of failing on the unique link."""
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        client = web_client(owner)
        payload = create_payload(workspace, draft_message_id=str(draft_message.id))
        first_id = client.post("/api/v1/tasks", payload, format="json").json()["id"]
        assert client.delete(f"/api/v1/tasks/{first_id}").status_code == 204
        second_id = client.post("/api/v1/tasks", payload, format="json").json()["id"]

        assert client.post(f"/api/v1/tasks/{first_id}/restore").status_code == 200
        restored = Task.objects.get(pk=first_id)
        assert restored.deleted_at is None
        assert restored.source_message_id is None
        assert Task.objects.get(pk=second_id).source_message_id == draft_message.id

    def test_deleting_the_conversation_keeps_the_confirmed_task(self, workspace, owner, web_client):
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, owner)
        web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(draft_message.id)),
            format="json",
        )
        conversation.delete()
        task = Task.objects.get()
        assert task.source_message_id is None
        assert task.source == Task.Source.AI_SUGGESTED

    def test_nonexistent_draft_message_id_returns_400_and_creates_no_task(
        self, workspace, owner, web_client
    ):
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(uuid.uuid4())),
            format="json",
        )
        assert response.status_code == 400
        assert Task.objects.count() == 0

    def test_wrong_actor_draft_message_id_returns_400_and_creates_no_task(
        self, workspace, owner, web_client
    ):
        stranger = register_user(
            email="stranger-draft@test.dev", password=PASSWORD, name="Stranger"
        )
        WorkspaceMember.objects.create(
            workspace=workspace, user=stranger, role=WorkspaceMember.Role.MEMBER
        )
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(workspace, conversation, stranger)
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(draft_message.id)),
            format="json",
        )
        assert response.status_code == 400
        assert Task.objects.count() == 0

    def test_wrong_workspace_draft_message_id_returns_400_and_creates_no_task(
        self, workspace, owner, web_client
    ):
        other_workspace = Workspace.objects.create(
            name="Other", owner=owner, type=Workspace.Type.COMPANY
        )
        WorkspaceMember.objects.create(
            workspace=other_workspace, user=owner, role=WorkspaceMember.Role.OWNER
        )
        other_conversation = self._conversation(other_workspace, owner)
        draft_message = self._task_draft_message(other_workspace, other_conversation, owner)
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(draft_message.id)),
            format="json",
        )
        assert response.status_code == 400
        assert Task.objects.count() == 0

    def test_wrong_intent_draft_message_id_returns_400_and_creates_no_task(
        self, workspace, owner, web_client
    ):
        conversation = self._conversation(workspace, owner)
        draft_message = self._task_draft_message(
            workspace, conversation, owner, intent=Message.Intent.CHAT
        )
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id=str(draft_message.id)),
            format="json",
        )
        assert response.status_code == 400
        assert Task.objects.count() == 0

    def test_malformed_draft_message_id_returns_400(self, workspace, owner, web_client):
        response = web_client(owner).post(
            "/api/v1/tasks",
            create_payload(workspace, draft_message_id="not-a-uuid"),
            format="json",
        )
        assert response.status_code == 400
