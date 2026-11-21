"""`GET /api/v1/memory/evals/runs` (Phase 8B branch 3, brief D9).

The installation owner, a human over a trusted device's web session only
(`IsWebChannel` + `IsOwner`); default authentication only, so a CLI,
facade or hook PAT or a node secret is never authenticated here. Rows come
through `MemoryEvalRun.objects.for_actor(request.user)`: they live in the
owner's personal workspace, so a firm-only member sees none and rule 1
needs no exception. The response carries the metric fields only.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsOwner, IsWebChannel
from memory.evals.serializers import serialize_run
from memory.models import MemoryEvalRun

LIST_CAP = 200


class EvalRunListView(APIView):
    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def get(self, request):
        runs = MemoryEvalRun.objects.for_actor(request.user).order_by("-started_at", "arm")
        return Response([serialize_run(run) for run in runs[:LIST_CAP]])
