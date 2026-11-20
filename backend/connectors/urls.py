from django.urls import path

from connectors.views import ConnectorBindingDetailView, ConnectorBindingListView

urlpatterns = [
    path("connectors/bindings", ConnectorBindingListView.as_view(), name="connector-binding-list"),
    path(
        "connectors/bindings/<str:connector>",
        ConnectorBindingDetailView.as_view(),
        name="connector-binding-detail",
    ),
]
