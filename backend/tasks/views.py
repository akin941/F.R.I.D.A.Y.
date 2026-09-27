import uuid
from datetime import date

from django.db.models import Case, IntegerField, When
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.models import Message
from core.channels import request_channel
from core.permissions import IsWebChannel
from tasks.models import Task
from tasks.serializers import TaskCreateSerializer, TaskEditSerializer, TaskSerializer
from tasks.services import (
    InvalidTransition,
    TaskPermissionDenied,
    cancel_task,
    complete_task,
    create_task,
    delete_task,
    edit_task,
    reopen_task,
    restore_task,
)
from workspaces.models import Workspace

_TRANSITIONS = {
    "complete": complete_task,
    "cancel": cancel_task,
    "reopen": reopen_task,
}

# `priority` is a CharField ("high"/"normal"/"low"), so a plain `-priority`
# ordering would sort alphabetically (normal, low, high) instead of by
# urgency; this annotation maps each value to a rank Postgres can order by.
_PRIORITY_ORDER = Case(
    When(priority=Task.Priority.HIGH, then=0),
    When(priority=Task.Priority.NORMAL, then=1),
    When(priority=Task.Priority.LOW, then=2),
    output_field=IntegerField(),
)


def _service_error_response(exc):
    if isinstance(exc, TaskPermissionDenied):
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, InvalidTransition):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    raise exc


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error)."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        return None, Response(
            {"detail": "workspace query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        workspace_uuid = uuid.UUID(workspace_id)
    except ValueError:
        return None, Response(
            {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
        )
    workspace = Workspace.objects.for_actor(request.user, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


def _parse_date_param(request, name):
    """Parse an optional ISO `YYYY-MM-DD` query param. Returns (date_or_None,
    error_response_or_None). The Today page passes the *client's* local date
    here -- the server never computes "today" itself (brief §2)."""
    raw = request.query_params.get(name)
    if not raw:
        return None, None
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, Response(
            {"detail": f"{name} must be an ISO date (YYYY-MM-DD)."},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _resolve_draft_source(request, workspace, draft_message_id):
    """Resolve `TaskCreateSerializer`'s optional `draft_message_id` reference
    into a `Task.Source` value (MVP 6 §3). Returns `(source, error_response)`
    — exactly one is non-`None`.

    `draft_message_id` absent (`None`) is the ordinary manual-create path,
    unchanged: `(Task.Source.USER_CREATED, None)`. Present but failing
    verification (message not found, wrong actor, wrong workspace, wrong
    `intent`) is a hard 400 with NO `Task` row created — never a silent
    downgrade to `user_created`, which could let a confirm click that should
    have produced an `ai_suggested` task silently produce an
    indistinguishable ordinary one instead (the exact outcome `source`
    exists to prevent). `created_by=request.user` enforces "same actor": the
    referenced message must be this requester's own drafted message, not
    merely any message visible in the workspace.
    """
    if draft_message_id is None:
        return Task.Source.USER_CREATED, None

    draft_message = (
        Message.objects.for_actor(request.user, workspace=workspace)
        .filter(id=draft_message_id, intent=Message.Intent.TASK_DRAFT, created_by=request.user)
        .first()
    )
    if draft_message is None:
        return None, Response(
            {"detail": "draft_message_id does not reference a valid task_draft message."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Task.Source.AI_SUGGESTED, None


class TaskListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        tasks = Task.objects.for_actor(request.user, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in Task.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            tasks = tasks.filter(status=status_filter)
        due_before, error = _parse_date_param(request, "due_before")
        if error is not None:
            return error
        if due_before is not None:
            tasks = tasks.filter(due_date__lte=due_before)
        due_after, error = _parse_date_param(request, "due_after")
        if error is not None:
            return error
        if due_after is not None:
            tasks = tasks.filter(due_date__gte=due_after)
        tasks = tasks.annotate(_priority_rank=_PRIORITY_ORDER).order_by(
            "due_date", "_priority_rank", "-updated_at", "-id"
        )
        return Response(TaskSerializer(tasks, many=True).data)

    def post(self, request):
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        source, error = _resolve_draft_source(
            request, workspace, serializer.validated_data["draft_message_id"]
        )
        if error is not None:
            return error

        try:
            task = create_task(
                workspace=workspace,
                actor=request.user,
                title=serializer.validated_data["title"],
                description=serializer.validated_data["description"],
                due_date=serializer.validated_data["due_date"],
                priority=serializer.validated_data["priority"],
                source=source,
            )
        except (TaskPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel]

    def _get_task(self, request, task_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak).
        return Task.objects.for_actor(request.user).filter(id=task_id).first()

    def get(self, request, task_id):
        task = self._get_task(request, task_id)
        if task is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(TaskSerializer(task).data)

    def patch(self, request, task_id):
        task = self._get_task(request, task_id)
        if task is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = TaskEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            task = edit_task(task=task, actor=request.user, **serializer.validated_data)
        except (TaskPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(TaskSerializer(task).data)

    def delete(self, request, task_id):
        task = self._get_task(request, task_id)
        if task is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            delete_task(task=task, actor=request.user, channel=request_channel(request))
        except (TaskPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id, action):
        transition = _TRANSITIONS.get(action)
        if transition is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        task = Task.objects.for_actor(request.user).filter(id=task_id).first()
        if task is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            task = transition(task=task, actor=request.user)
        except (TaskPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(TaskSerializer(task).data)


class TaskTrashListView(APIView):
    """`GET /api/v1/tasks/trash?workspace=` -- the workspace's trashed tasks
    ("Çöp kutusu", ADR-009), newest deletion first."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        tasks = (
            Task.all_objects.for_actor(request.user, workspace=workspace)
            .filter(deleted_at__isnull=False)
            .order_by("-deleted_at", "-id")
        )
        return Response(TaskSerializer(tasks, many=True).data)


class TaskRestoreView(APIView):
    """`POST /api/v1/tasks/<id>/restore` -- bring a trashed task back."""

    permission_classes = [IsAuthenticated, IsWebChannel]

    def post(self, request, task_id):
        task = (
            Task.all_objects.for_actor(request.user)
            .filter(id=task_id, deleted_at__isnull=False)
            .first()
        )
        if task is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            task = restore_task(task=task, actor=request.user, channel=request_channel(request))
        except (TaskPermissionDenied, InvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(TaskSerializer(task).data)
