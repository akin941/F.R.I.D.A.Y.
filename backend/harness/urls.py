from django.urls import path

from harness.views import (
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
]
