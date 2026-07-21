from django.urls import path

from agents.views import (
    AgentCancelView,
    AgentDetailView,
    AgentListCreateView,
    AgentStepConfirmView,
    AgentStepDiscardView,
)

urlpatterns = [
    path("agents", AgentListCreateView.as_view(), name="agent-list-create"),
    path("agents/<uuid:agent_id>", AgentDetailView.as_view(), name="agent-detail"),
    path("agents/<uuid:agent_id>/cancel", AgentCancelView.as_view(), name="agent-cancel"),
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
