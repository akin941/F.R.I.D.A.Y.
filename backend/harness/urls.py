from django.urls import path

from harness.views import (
    HarnessImplementationJobCancelView,
    HarnessImplementationJobDetailView,
    HarnessImplementationJobListCreateView,
    HarnessJobCancelView,
    HarnessJobDetailView,
    HarnessJobListCreateView,
    HarnessRepoDetailView,
    HarnessRepoListCreateView,
)

urlpatterns = [
    path("harness/repos", HarnessRepoListCreateView.as_view(), name="harness-repo-list"),
    path(
        "harness/repos/<uuid:repo_id>",
        HarnessRepoDetailView.as_view(),
        name="harness-repo-detail",
    ),
    path("harness/jobs", HarnessJobListCreateView.as_view(), name="harness-job-list"),
    path(
        "harness/jobs/<uuid:job_id>",
        HarnessJobDetailView.as_view(),
        name="harness-job-detail",
    ),
    path(
        "harness/jobs/<uuid:job_id>/cancel",
        HarnessJobCancelView.as_view(),
        name="harness-job-cancel",
    ),
    path(
        "harness/implementation-jobs",
        HarnessImplementationJobListCreateView.as_view(),
        name="harness-implementation-job-list",
    ),
    path(
        "harness/implementation-jobs/<uuid:job_id>",
        HarnessImplementationJobDetailView.as_view(),
        name="harness-implementation-job-detail",
    ),
    path(
        "harness/implementation-jobs/<uuid:job_id>/cancel",
        HarnessImplementationJobCancelView.as_view(),
        name="harness-implementation-job-cancel",
    ),
]
