from django.urls import path

from accounts.views import (
    AuditedLoginView,
    LogoutView,
    MeView,
    PersonalAccessTokenListCreateView,
    PersonalAccessTokenRevokeView,
    SignupView,
    ThrottledTokenRefreshView,
)

urlpatterns = [
    path("signup", SignupView.as_view(), name="auth-signup"),
    path("login", AuditedLoginView.as_view(), name="auth-login"),
    path("refresh", ThrottledTokenRefreshView.as_view(), name="auth-refresh"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
    path("me", MeView.as_view(), name="auth-me"),
    path("tokens", PersonalAccessTokenListCreateView.as_view(), name="auth-tokens"),
    path(
        "tokens/<uuid:token_id>/revoke",
        PersonalAccessTokenRevokeView.as_view(),
        name="auth-tokens-revoke",
    ),
]
