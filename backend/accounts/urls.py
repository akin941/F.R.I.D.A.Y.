from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import AuditedLoginView, MeView, SignupView

urlpatterns = [
    path("signup", SignupView.as_view(), name="auth-signup"),
    path("login", AuditedLoginView.as_view(), name="auth-login"),
    path("refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me", MeView.as_view(), name="auth-me"),
]
