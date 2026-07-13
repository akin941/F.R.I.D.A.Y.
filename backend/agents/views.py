"""HTTP surface for the `agents` app: goal-entry (list/create), detail,
cancel (branch 5, brief §5), and the `task_draft` confirm/discard action
(MVP 7 §3, brief's round-4 fix -- an agents-local endpoint, not the
existing tasks API's `draft_message_id` mechanism).
"""

import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agents.execution import (
    AgentInvalidTransition,
    AgentPermissionDenied,
    cancel_agent,
    confirm_task_draft_step,
    discard_task_draft_step,
)
from agents.models import Agent, AgentStep
from agents.planner_service import create_agent_from_goal
from agents.serializers import (
    AgentCreateSerializer,
    AgentDetailSerializer,
    AgentSerializer,
    AgentStepSerializer,
)
from tasks.serializers import TaskSerializer
from tasks.services import InvalidTransition, TaskPermissionDenied
from workspaces.models import Workspace


def _service_error_response(exc):
    if isinstance(exc, (AgentPermissionDenied, TaskPermissionDenied)):
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, (AgentInvalidTransition, InvalidTransition)):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    raise exc


def _get_step(request, step_id):
    # Out-of-scope rows -> 404, never 403 (existence must not leak).
    return AgentStep.objects.for_actor(request.user).filter(id=step_id).first()


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error).
    Mirrors `tasks.views`/`approvals.views`'s identical helper."""
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


class AgentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        agents = Agent.objects.for_actor(request.user, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in Agent.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            agents = agents.filter(status=status_filter)
        return Response(AgentSerializer(agents, many=True).data)

    def post(self, request):
        serializer = AgentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            # Blocks on the planner's own gateway call (the one
            # `purpose="agent.plan"` LLM round-trip) -- mirrors MVP 6's
            # chat POST, which already waits on its own classification
            # call synchronously within the request/response cycle.
            agent = create_agent_from_goal(
                workspace=workspace, actor=request.user, goal=serializer.validated_data["goal"]
            )
        except AgentPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(AgentSerializer(agent).data, status=status.HTTP_201_CREATED)


class AgentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, agent_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak).
        agent = Agent.objects.for_actor(request.user).filter(id=agent_id).first()
        if agent is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(AgentDetailSerializer(agent).data)


class AgentCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, agent_id):
        agent = Agent.objects.for_actor(request.user).filter(id=agent_id).first()
        if agent is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            agent = cancel_agent(agent=agent, actor=request.user)
        except (AgentPermissionDenied, AgentInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(AgentSerializer(agent).data)


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
