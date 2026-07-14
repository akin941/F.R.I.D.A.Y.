"""HTTP surface for the `agents` app: goal-entry (list/create), detail,
cancel (branch 5, brief §5), and the `task_draft` confirm/discard action
(MVP 7 §3, brief's round-4 fix -- an agents-local endpoint, not the
existing tasks API's `draft_message_id` mechanism).
"""

import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from accounts.authentication import PersonalAccessTokenAuthentication
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
from core.channels import is_low_trust, scoped_for_actor
from tasks.serializers import TaskSerializer
from tasks.services import InvalidTransition, TaskPermissionDenied
from workspaces.models import Workspace

# Allowlisted for PAT auth (MVP 9 §1/§2) -- AgentStepConfirmView/
# AgentStepDiscardView are deliberately NOT allowlisted (out of this MVP's
# scope; only goal-entry/list/detail/cancel are named in the brief).
# PersonalAccessTokenAuthentication MUST precede JWTAuthentication -- see
# accounts/authentication.py's docstring for why.
_PAT_ALLOWLISTED_AUTHENTICATION_CLASSES = [
    PersonalAccessTokenAuthentication,
    *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
]


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
    Mirrors `tasks.views`/`approvals.views`'s identical helper. A CLI/PAT
    request has no workspace to supply -- the presence requirement is
    skipped entirely for a low-trust channel, since scoped_for_actor()
    forces the bound workspace regardless."""
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        if not is_low_trust(request):
            return None, Response(
                {"detail": "workspace query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace_uuid = None
    else:
        try:
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            return None, Response(
                {"detail": "workspace must be a valid UUID."}, status=status.HTTP_400_BAD_REQUEST
            )
    workspace = scoped_for_actor(request, Workspace.objects, workspace=workspace_uuid).first()
    if workspace is None:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return workspace, None


class AgentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        agents = scoped_for_actor(request, Agent.objects, workspace=workspace)
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in Agent.Status.values:
                return Response(
                    {"detail": "invalid status filter."}, status=status.HTTP_400_BAD_REQUEST
                )
            agents = agents.filter(status=status_filter)
        return Response(AgentSerializer(agents, many=True).data)

    def post(self, request):
        data = request.data
        if is_low_trust(request):
            # `.copy()` first: request.data is an immutable QueryDict for
            # form-encoded requests (Codex review, branch 1 round 1).
            data = data.copy()
            data["workspace"] = request.auth.bound_workspace_id
        serializer = AgentCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        workspace = scoped_for_actor(
            request, Workspace.objects, workspace=serializer.validated_data["workspace"]
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
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def get(self, request, agent_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak).
        # By-id lookup, no workspace parameter -- scoped_for_actor still
        # forces a channel-bound request to its bound workspace.
        agent = scoped_for_actor(request, Agent.objects).filter(id=agent_id).first()
        if agent is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(AgentDetailSerializer(agent).data)


class AgentCancelView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = _PAT_ALLOWLISTED_AUTHENTICATION_CLASSES

    def post(self, request, agent_id):
        agent = scoped_for_actor(request, Agent.objects).filter(id=agent_id).first()
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
