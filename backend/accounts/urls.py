from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import (
    AuditedLoginView,
    MeView,
    PersonalAccessTokenListCreateView,
    PersonalAccessTokenRevokeView,
    SignupView,
)

urlpatterns = [
    path("signup", SignupView.as_view(), name="auth-signup"),
    path("login", AuditedLoginView.as_view(), name="auth-login"),
    path("refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me", MeView.as_view(), name="auth-me"),
    path("tokens", PersonalAccessTokenListCreateView.as_view(), name="auth-tokens"),
    path(
        "tokens/<uuid:token_id>/revoke",
        PersonalAccessTokenRevokeView.as_view(),
        name="auth-tokens-revoke",
    ),
]
