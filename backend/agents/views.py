"""HTTP surface for the `task_draft` confirm/discard action (MVP 7 §3,
brief's round-4 fix -- an agents-local endpoint, not the existing tasks
API's `draft_message_id` mechanism). The `Agent` list/create, detail, and
cancel endpoints are branch 5's scope, bundled with the frontend that
consumes them -- this branch's own new user-facing surface is exactly
these two actions.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agents.execution import (
    AgentInvalidTransition,
    AgentPermissionDenied,
    confirm_task_draft_step,
    discard_task_draft_step,
)
from agents.models import AgentStep
from agents.serializers import AgentStepSerializer
from tasks.serializers import TaskSerializer
from tasks.services import InvalidTransition, TaskPermissionDenied


def _service_error_response(exc):
    if isinstance(exc, (AgentPermissionDenied, TaskPermissionDenied)):
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, (AgentInvalidTransition, InvalidTransition)):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    raise exc


def _get_step(request, step_id):
    # Out-of-scope rows -> 404, never 403 (existence must not leak).
    return AgentStep.objects.for_actor(request.user).filter(id=step_id).first()


class AgentStepConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, step_id):
        step = _get_step(request, step_id)
        if step is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            task = confirm_task_draft_step(step=step, actor=request.user)
        except (
            AgentPermissionDenied,
            AgentInvalidTransition,
            TaskPermissionDenied,
            InvalidTransition,
        ) as exc:
            # `confirm_task_draft_step` calls `tasks.services.create_task()`
            # internally (deliberately unguarded there -- see its own
            # docstring), which can itself raise if the goal-submitter's
            # role changed since planning; translate those the same way the
            # tasks API itself does, rather than letting them 500.
            return _service_error_response(exc)
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class AgentStepDiscardView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, step_id):
        step = _get_step(request, step_id)
        if step is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            step = discard_task_draft_step(step=step, actor=request.user)
        except (AgentPermissionDenied, AgentInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(AgentStepSerializer(step).data)
