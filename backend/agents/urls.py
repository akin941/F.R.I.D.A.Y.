from django.urls import path

from agents.views import AgentStepConfirmView, AgentStepDiscardView

urlpatterns = [
    path(
        "agents/steps/<uuid:step_id>/confirm",
        AgentStepConfirmView.as_view(),
        name="agent-step-confirm",
    ),
    path(
        "agents/steps/<uuid:step_id>/discard",
        AgentStepDiscardView.as_view(),
        name="agent-step-discard",
    ),
]
