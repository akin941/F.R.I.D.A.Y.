from django.urls import path

from nodes.views import NodePairView

urlpatterns = [
    path("nodes/pair", NodePairView.as_view(), name="node-pair"),
]
