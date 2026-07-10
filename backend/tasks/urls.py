from django.urls import path

from tasks.views import TaskDetailView, TaskListCreateView, TaskTransitionView

urlpatterns = [
    path("tasks", TaskListCreateView.as_view(), name="task-list-create"),
    path("tasks/<uuid:task_id>", TaskDetailView.as_view(), name="task-detail"),
    path(
        "tasks/<uuid:task_id>/<str:action>",
        TaskTransitionView.as_view(),
        name="task-transition",
    ),
]
