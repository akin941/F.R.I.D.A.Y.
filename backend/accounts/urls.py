from django.urls import path

from accounts.views import (
    CurrentDeviceRevokeView,
    DeviceListView,
    DeviceRevokeView,
    DeviceSessionView,
    DeviceTokenRefreshView,
    EnrollView,
    LogoutView,
    MeView,
    PairingCodeCreateView,
    PersonalAccessTokenListCreateView,
    PersonalAccessTokenRevokeView,
    RemoteAccessKeyRevokeView,
    RemoteAccessKeyView,
)

# No signup and no password login (Phase 4 branch 2): identity is device
# trust. `enroll` turns a pairing code / the remote key into a trusted
# device; `devices/session` mints a session from a device secret.
urlpatterns = [
    path("enroll", EnrollView.as_view(), name="auth-enroll"),
    path("devices/session", DeviceSessionView.as_view(), name="auth-device-session"),
    path("refresh", DeviceTokenRefreshView.as_view(), name="auth-refresh"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
    path("me", MeView.as_view(), name="auth-me"),
    path("devices", DeviceListView.as_view(), name="auth-devices"),
    path(
        "devices/current/revoke",
        CurrentDeviceRevokeView.as_view(),
        name="auth-devices-current-revoke",
    ),
    path(
        "devices/<uuid:device_id>/revoke",
        DeviceRevokeView.as_view(),
        name="auth-devices-revoke",
    ),
    path("pairing-codes", PairingCodeCreateView.as_view(), name="auth-pairing-codes"),
    path("remote-key", RemoteAccessKeyView.as_view(), name="auth-remote-key"),
    path("remote-key/revoke", RemoteAccessKeyRevokeView.as_view(), name="auth-remote-key-revoke"),
    path("tokens", PersonalAccessTokenListCreateView.as_view(), name="auth-tokens"),
    path(
        "tokens/<uuid:token_id>/revoke",
        PersonalAccessTokenRevokeView.as_view(),
        name="auth-tokens-revoke",
    ),
]
