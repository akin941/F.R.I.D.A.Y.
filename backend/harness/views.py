import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.throttling import CreateScopedRateThrottle
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo
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
from workspaces.models import Workspace


def _service_error_response(exc):
    if isinstance(exc, HarnessPermissionDenied):
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, HarnessInvalidTransition):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
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
                agent=serializer.validated_data["agent"],
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
