from django.urls import path

from approvals.views import (
    ApprovalRequestDetailView,
    ApprovalRequestListCreateView,
    ApprovalRequestTransitionView,
)

urlpatterns = [
    path(
        "approvals/requests", ApprovalRequestListCreateView.as_view(), name="approval-list-create"
    ),
    path(
        "approvals/requests/<uuid:request_id>",
        ApprovalRequestDetailView.as_view(),
        name="approval-detail",
    ),
    path(
        "approvals/requests/<uuid:request_id>/<str:action>",
        ApprovalRequestTransitionView.as_view(),
        name="approval-transition",
    ),
]
