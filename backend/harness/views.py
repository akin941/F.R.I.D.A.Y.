import uuid

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_errors import service_error_response
from core.channels import request_channel
from core.permissions import IsOwner, IsWebChannel
from core.throttling import CreateScopedRateThrottle
from harness.models import GraphifyRun, HarnessImplementationJob, HarnessJob, HarnessRepo
from harness.pull_requests import (
    HarnessGithubUnavailable,
    HarnessPullRequestExists,
    HarnessPullRequestNotReady,
    pull_request_state,
    request_pull_request,
)
from harness.serializers import (
    HarnessImplementationJobCreateSerializer,
    HarnessImplementationJobDetailSerializer,
    HarnessImplementationJobSerializer,
    HarnessJobCreateSerializer,
    HarnessJobDetailSerializer,
    HarnessJobSerializer,
    HarnessRepoCreateSerializer,
    HarnessRepoEditSerializer,
    HarnessRepoSerializer,
)
from harness.services import (
    HarnessInvalidTransition,
    HarnessPermissionDenied,
    HarnessServiceError,
    cancel_harness_implementation_job,
    cancel_harness_job,
    create_harness_implementation_job,
    create_harness_job,
    create_repo,
    delete_repo,
    edit_repo,
    get_implementation_job,
    get_job,
    get_repo,
    list_implementation_jobs,
    list_jobs,
    list_repos,
)
from workspaces.models import Workspace, WorkspaceMember


def _service_error_response(exc):
    if isinstance(exc, HarnessPermissionDenied):
        return service_error_response(exc, status.HTTP_403_FORBIDDEN)
    if isinstance(exc, HarnessInvalidTransition):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    raise exc


def _resolve_workspace(request):
    """Validate the `workspace` query param and return (workspace, error) --
    mirrors `tasks.views._resolve_workspace`."""
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


class HarnessRepoListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        try:
            repos = list_repos(workspace=workspace, actor=request.user).order_by("-created_at")
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(HarnessRepoSerializer(repos, many=True).data)

    def post(self, request):
        serializer = HarnessRepoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        fields = {k: v for k, v in serializer.validated_data.items() if k != "workspace"}
        try:
            repo = create_repo(workspace=workspace, actor=request.user, **fields)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(HarnessRepoSerializer(repo).data, status=status.HTTP_201_CREATED)


class HarnessRepoDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_repo(self, request, repo_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak).
        # Curator gating happens inside the service layer, which needs a
        # concrete row/workspace to check the role against -- this lookup
        # itself is scoped by ordinary workspace membership so a non-member
        # never learns a repo exists, before the curator-only check below
        # even runs.
        return HarnessRepo.objects.for_actor(request.user).filter(id=repo_id).first()

    def get(self, request, repo_id):
        repo = self._get_repo(request, repo_id)
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            repo = get_repo(workspace=repo.workspace, actor=request.user, repo_id=repo.id)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(HarnessRepoSerializer(repo).data)

    def patch(self, request, repo_id):
        repo = self._get_repo(request, repo_id)
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = HarnessRepoEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            repo = edit_repo(repo=repo, actor=request.user, **serializer.validated_data)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(HarnessRepoSerializer(repo).data)

    def delete(self, request, repo_id):
        repo = self._get_repo(request, repo_id)
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            delete_repo(repo=repo, actor=request.user)
        except (HarnessPermissionDenied, HarnessInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HarnessJobListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    # Each job may spend up to HARNESS_MAX_JOB_COST.
    throttle_classes = [CreateScopedRateThrottle]
    throttle_scope = "cost"

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        try:
            jobs = (
                list_jobs(workspace=workspace, actor=request.user)
                .select_related("repo")
                .order_by("-created_at")
            )
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(HarnessJobSerializer(jobs, many=True).data)

    def post(self, request):
        serializer = HarnessJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Out-of-scope/nonexistent repo -> 404, never 403 (existence must
        # not leak) -- same convention as HarnessRepoDetailView._get_repo.
        # create_harness_job's own re-lock still re-verifies workspace
        # membership and `enabled` live, under lock (services.py
        # docstring) -- this lookup only needs to resolve *a* repo the
        # requester can see at all.
        repo = (
            HarnessRepo.objects.for_actor(request.user)
            .filter(id=serializer.validated_data["repo"])
            .first()
        )
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = create_harness_job(
                workspace=workspace,
                actor=request.user,
                repo=repo,
                goal=serializer.validated_data["goal"],
                agent=serializer.validated_data.get("agent"),
            )
        except (HarnessPermissionDenied, HarnessInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(HarnessJobSerializer(job).data, status=status.HTTP_201_CREATED)


class HarnessJobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_job(self, request, job_id):
        # Out-of-scope rows -> 404, never 403 (existence must not leak).
        return HarnessJob.objects.for_actor(request.user).filter(id=job_id).first()

    def get(self, request, job_id):
        job = self._get_job(request, job_id)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = get_job(workspace=job.workspace, actor=request.user, job_id=job.id)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(HarnessJobDetailSerializer(job).data)


class HarnessJobCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        job = HarnessJob.objects.for_actor(request.user).filter(id=job_id).first()
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = cancel_harness_job(job=job, actor=request.user)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        return Response(HarnessJobSerializer(job).data)


# --- Implementation jobs (Phase 3 branch 6) ---------------------------------


def _implementation_queryset(queryset):
    return queryset.select_related("repo", "gate_a_request", "gate_b_request")


class HarnessImplementationJobListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreateScopedRateThrottle]
    throttle_scope = "cost"

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        try:
            jobs = list_implementation_jobs(workspace=workspace, actor=request.user)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        jobs = _implementation_queryset(jobs).order_by("-created_at")
        return Response(HarnessImplementationJobSerializer(jobs, many=True).data)

    def post(self, request):
        serializer = HarnessImplementationJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = Workspace.objects.for_actor(
            request.user, workspace=serializer.validated_data["workspace"]
        ).first()
        if workspace is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Out-of-scope/nonexistent repo or discovery job -> 404, never 403
        # (existence must not leak); the service re-verifies both under lock.
        repo = (
            HarnessRepo.objects.for_actor(request.user, workspace=workspace)
            .filter(id=serializer.validated_data["repo"])
            .first()
        )
        if repo is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        discovery_job = None
        discovery_job_id = serializer.validated_data.get("discovery_job")
        if discovery_job_id is not None:
            discovery_job = (
                HarnessJob.objects.for_actor(request.user, workspace=workspace)
                .filter(id=discovery_job_id)
                .first()
            )
            if discovery_job is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = create_harness_implementation_job(
                workspace=workspace,
                actor=request.user,
                repo=repo,
                goal=serializer.validated_data["goal"],
                discovery_job=discovery_job,
            )
        except (HarnessPermissionDenied, HarnessInvalidTransition) as exc:
            return _service_error_response(exc)
        return Response(
            HarnessImplementationJobSerializer(job).data, status=status.HTTP_201_CREATED
        )


class HarnessImplementationJobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        job = HarnessImplementationJob.objects.for_actor(request.user).filter(id=job_id).first()
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = get_implementation_job(workspace=job.workspace, actor=request.user, job_id=job.id)
        except HarnessPermissionDenied as exc:
            return _service_error_response(exc)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        job = (
            _implementation_queryset(HarnessImplementationJob.objects.filter(pk=job.pk))
            .prefetch_related("reviews")
            .get()
        )
        return Response(HarnessImplementationJobDetailSerializer(job).data)


class HarnessImplementationJobCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        job = HarnessImplementationJob.objects.for_actor(request.user).filter(id=job_id).first()
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            job = cancel_harness_implementation_job(job=job, actor=request.user)
        except (HarnessPermissionDenied, HarnessInvalidTransition) as exc:
            return _service_error_response(exc)
        job.refresh_from_db()
        return Response(HarnessImplementationJobSerializer(job).data)


class PullRequestCreateSerializer(serializers.Serializer):
    # Owner-editable; omitted -> the goal's first line.
    title = serializers.CharField(max_length=256, required=False, trim_whitespace=True)


def _pull_request_error_response(exc):
    if isinstance(exc, HarnessPullRequestExists):
        return service_error_response(exc, status.HTTP_409_CONFLICT)
    if isinstance(exc, (HarnessPullRequestNotReady, HarnessGithubUnavailable)):
        return service_error_response(exc, status.HTTP_400_BAD_REQUEST)
    return _service_error_response(exc)


class HarnessImplementationJobPullRequestView(APIView):
    """`github_open_pr` for a pushed job (Phase 5 branch 5, brief D7): the
    installation owner over a trusted web session only -- default
    authentication (no PAT allowlist), `IsWebChannel`, `IsOwner`. POST
    mints the system-created gate; GET is the job's PR/CI panel."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def _job(self, request, job_id):
        return (
            HarnessImplementationJob.objects.for_actor(request.user)
            .filter(id=job_id)
            .select_related("workspace", "pull_request_request")
            .first()
        )

    def get(self, request, job_id):
        job = self._job(request, job_id)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            state = pull_request_state(
                job=job, actor=request.user, channel=request_channel(request)
            )
        except HarnessServiceError as exc:
            return _pull_request_error_response(exc)
        return Response(state)

    def post(self, request, job_id):
        job = self._job(request, job_id)
        if job is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = PullRequestCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            approval_request = request_pull_request(
                job=job,
                actor=request.user,
                channel=request_channel(request),
                title=body.validated_data.get("title"),
            )
        except HarnessServiceError as exc:
            return _pull_request_error_response(exc)
        job.refresh_from_db()
        state = pull_request_state(job=job, actor=request.user, channel=request_channel(request))
        state["request"]["id"] = str(approval_request.id)
        return Response(state, status=status.HTTP_201_CREATED)


# --- Graphify runs (Phase 8B branch 9, brief D10) -----------------------------

GRAPHIFY_RUN_LIST_CAP = 50


def _serialize_graphify_run(run):
    """Status, shas, times and the importer's counts only: never a label,
    container output or the stored error message (`error_type` is a closed
    code)."""
    counts = run.counts if isinstance(run.counts, dict) else {}
    return {
        "id": str(run.id),
        "workspace": str(run.workspace_id),
        "repo": {"id": str(run.repo_id), "name": run.repo.name},
        "status": run.status,
        "requested_sha": run.requested_sha,
        "commit_sha": run.commit_sha,
        "committed_at": run.committed_at.isoformat() if run.committed_at else None,
        "attempts": run.attempts,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_type": run.error_type,
        "counts": {
            str(k): v
            for k, v in counts.items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        },
        "created_at": run.created_at.isoformat(),
    }


class GraphifyRunListView(APIView):
    """`GET /api/v1/harness/graphify-runs?workspace=<uuid>`: the ingestion
    audit view's Graphify run list (Phase 8B branch 9). The installation
    owner over the web who owns the workspace; rows through `for_actor()`,
    so another workspace's runs never appear (its id is a 404). Read-only;
    no audit row (status and counts only, the harness job list precedent)."""

    permission_classes = [IsAuthenticated, IsWebChannel, IsOwner]

    def get(self, request):
        workspace, error = _resolve_workspace(request)
        if error is not None:
            return error
        if not WorkspaceMember.objects.filter(
            workspace=workspace, user=request.user, role=WorkspaceMember.Role.OWNER
        ).exists():
            return Response(
                {"detail": "Only the workspace owner can read its Graphify runs."},
                status=status.HTTP_403_FORBIDDEN,
            )
        runs = (
            GraphifyRun.objects.for_actor(request.user, workspace=workspace)
            .select_related("repo")
            .order_by("-created_at")[:GRAPHIFY_RUN_LIST_CAP]
        )
        return Response([_serialize_graphify_run(run) for run in runs])
