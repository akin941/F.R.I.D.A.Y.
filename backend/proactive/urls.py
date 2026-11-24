from django.urls import path

from proactive.views import (
    DigestAckView,
    DigestDetailView,
    DigestLatestView,
    NoticeDismissAllView,
    NoticeListView,
    NoticeReadView,
    PauseView,
    ScheduleDetailView,
    ScheduleListView,
)

urlpatterns = [
    path("proactive/schedules", ScheduleListView.as_view(), name="proactive-schedules"),
    path(
        "proactive/schedules/<str:kind>",
        ScheduleDetailView.as_view(),
        name="proactive-schedule-detail",
    ),
    path("proactive/notices", NoticeListView.as_view(), name="proactive-notices"),
    path(
        "proactive/notices/dismiss-all",
        NoticeDismissAllView.as_view(),
        name="proactive-notices-dismiss-all",
    ),
    path(
        "proactive/notices/<uuid:notice_id>/read",
        NoticeReadView.as_view(),
        name="proactive-notice-read",
    ),
    path("proactive/pause", PauseView.as_view(), name="proactive-pause"),
    path("proactive/digests/latest", DigestLatestView.as_view(), name="proactive-digest-latest"),
    path(
        "proactive/digests/<uuid:digest_id>",
        DigestDetailView.as_view(),
        name="proactive-digest-detail",
    ),
    path(
        "proactive/digests/<uuid:digest_id>/ack",
        DigestAckView.as_view(),
        name="proactive-digest-ack",
    ),
]
