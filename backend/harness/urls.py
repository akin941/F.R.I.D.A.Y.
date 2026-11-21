from django.urls import path

from harness.views import (
    GraphifyRunListView,
    HarnessImplementationJobCancelView,
    HarnessImplementationJobDetailView,
    HarnessImplementationJobListCreateView,
    HarnessImplementationJobPullRequestView,
    HarnessJobCancelView,
    HarnessJobDetailView,
    HarnessJobListCreateView,
    HarnessRepoDetailView,
    HarnessRepoListCreateView,
)

urlpatterns = [
    # Phase 8B branch 9: the Graphify run list of the ingestion audit view.
    path("harness/graphify-runs", GraphifyRunListView.as_view(), name="harness-graphify-runs"),
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
    path(
        "harness/implementation-jobs/<uuid:job_id>/pull-request",
        HarnessImplementationJobPullRequestView.as_view(),
        name="harness-implementation-job-pull-request",
    ),
]
