"""`GET /api/v1/activity/summary` -- the shell's live badge feed (Phase 4
branch 3, brief "Live updates: polling, not SSE").

One cheap, read-only aggregate the web UI polls instead of every list:
per workspace the actor belongs to, the number of pending approval
requests and of live (non-terminal) harness jobs and agents.

Lives in the project package, not in a kernel app: it reads approvals,
agents and harness side by side, and `harness` is a leaf dependency root
no other app may import (pyproject import-linter contracts). `config` is
the composition root that already wires every app's URLs together, so an
aggregate over several apps belongs here rather than in a new app.

Scoping: every count goes through `for_actor()` (ADR-001) -- only
workspaces the actor is a member of appear, and only their rows are
counted. Web only: a low-trust channel (PAT/CLI) and machine (`is_agent`)
accounts are refused with a deliberate 403. The PAT authenticator is
accepted here only so that refusal is an explicit channel check rather
than an incidental 401 -- same pattern as the approvals transition view.
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from accounts.authentication import PersonalAccessTokenAuthentication
from agents.models import Agent
from approvals.models import ApprovalRequest
from core.channels import is_low_trust
from harness.models import HarnessImplementationJob, HarnessJob
from workspaces.models import Workspace

LIVE_AGENT_STATUSES = [
    Agent.Status.PLANNING,
    Agent.Status.RUNNING,
    Agent.Status.BLOCKED_ON_APPROVAL,
    Agent.Status.BLOCKED_ON_CONFIRMATION,
]
LIVE_HARNESS_JOB_STATUSES = [HarnessJob.Status.PENDING, HarnessJob.Status.RUNNING]
TERMINAL_IMPLEMENTATION_STATUSES = [
    HarnessImplementationJob.Status.SUCCEEDED,
    HarnessImplementationJob.Status.FAILED,
    HarnessImplementationJob.Status.CANCELLED,
]


def _counts_by_workspace(queryset):
    rows = queryset.order_by().values("workspace").annotate(n=Count("id", distinct=True))
    return {row["workspace"]: row["n"] for row in rows}


class ActivitySummaryView(APIView):
    permission_classes = [IsAuthenticated]
    # PersonalAccessTokenAuthentication must precede JWTAuthentication
    # (accounts/authentication.py).
    authentication_classes = [
        PersonalAccessTokenAuthentication,
        *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
    ]

    def get(self, request):
        user = request.user
        if is_low_trust(request) or getattr(user, "is_agent", False):
            return Response(
                {"detail": "Activity summary is web-only.", "code": "permission_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        workspace_filter = None
        raw_workspace = request.query_params.get("workspace")
        if raw_workspace:
            try:
                workspace_filter = uuid.UUID(raw_workspace)
            except ValueError:
                return Response(
                    {"detail": "workspace must be a valid UUID.", "code": "invalid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        workspaces = list(
            Workspace.objects.for_actor(user, workspace=workspace_filter)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if workspace_filter is not None and not workspaces:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # A pending request older than the TTL is no longer decidable
        # (approvals.services._expire_if_stale) -- not counted as pending
        # even before the sweep has flipped it to `expired`.
        fresh_since = timezone.now() - timedelta(hours=settings.APPROVAL_REQUEST_TTL_HOURS)
        pending = _counts_by_workspace(
            ApprovalRequest.objects.for_actor(user, workspace=workspace_filter).filter(
                status=ApprovalRequest.Status.PENDING, created_at__gte=fresh_since
            )
        )
        agents = _counts_by_workspace(
            Agent.objects.for_actor(user, workspace=workspace_filter).filter(
                status__in=LIVE_AGENT_STATUSES
            )
        )
        discovery = _counts_by_workspace(
            HarnessJob.objects.for_actor(user, workspace=workspace_filter).filter(
                status__in=LIVE_HARNESS_JOB_STATUSES
            )
        )
        implementation = _counts_by_workspace(
            HarnessImplementationJob.objects.for_actor(user, workspace=workspace_filter).exclude(
                status__in=TERMINAL_IMPLEMENTATION_STATUSES
            )
        )

        rows = [
            {
                "workspace": str(workspace_id),
                "pending_approvals": pending.get(workspace_id, 0),
                "live_harness_jobs": discovery.get(workspace_id, 0)
                + implementation.get(workspace_id, 0),
                "live_agents": agents.get(workspace_id, 0),
            }
            for workspace_id in workspaces
        ]
        totals = {
            key: sum(row[key] for row in rows)
            for key in ("pending_approvals", "live_harness_jobs", "live_agents")
        }
        return Response({"workspaces": rows, "totals": totals})
