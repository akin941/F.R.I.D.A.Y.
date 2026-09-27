from django.urls import path

from tasks.views import (
    TaskDetailView,
    TaskListCreateView,
    TaskRestoreView,
    TaskTransitionView,
    TaskTrashListView,
)

urlpatterns = [
    path("tasks", TaskListCreateView.as_view(), name="task-list-create"),
    # Trash (ADR-009). Declared before the generic `<action>` route so
    # "restore" is never treated as a state-machine transition.
    path("tasks/trash", TaskTrashListView.as_view(), name="task-trash"),
    path("tasks/<uuid:task_id>/restore", TaskRestoreView.as_view(), name="task-restore"),
    path("tasks/<uuid:task_id>", TaskDetailView.as_view(), name="task-detail"),
    path(
        "tasks/<uuid:task_id>/<str:action>",
        TaskTransitionView.as_view(),
        name="task-transition",
    ),
]
