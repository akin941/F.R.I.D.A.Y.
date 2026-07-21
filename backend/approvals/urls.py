from django.urls import path

from approvals.views import ApprovalRequestListCreateView, ApprovalRequestTransitionView

urlpatterns = [
    path(
        "approvals/requests", ApprovalRequestListCreateView.as_view(), name="approval-list-create"
    ),
    path(
        "approvals/requests/<uuid:request_id>/<str:action>",
        ApprovalRequestTransitionView.as_view(),
        name="approval-transition",
    ),
]
