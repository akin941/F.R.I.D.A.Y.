"""Routes of the eval-run read API, mounted by `memory/urls.py`."""

from django.urls import path

from memory.evals.views import EvalRunListView

urlpatterns = [
    path("memory/evals/runs", EvalRunListView.as_view(), name="memory-eval-runs"),
]
