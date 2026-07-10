import pytest
from rest_framework.test import APIClient

from accounts.services import register_user

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"


@pytest.fixture
def user():
    return register_user(email="alice@test.dev", password=PASSWORD, name="Alice")


@pytest.fixture
def client():
    return APIClient()


def login(client, email, password):
    return client.post("/api/v1/auth/login", {"email": email, "password": password})


def test_login_success(client, user):
    response = login(client, "alice@test.dev", PASSWORD)
    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data


def test_login_wrong_password(client, user):
    response = login(client, "alice@test.dev", "wrong-password")
    assert response.status_code == 401


def test_inactive_user_cannot_login(client, user):
    user.is_active = False
    user.save()
    response = login(client, "alice@test.dev", PASSWORD)
    assert response.status_code == 401


def test_refresh_returns_new_access(client, user):
    tokens = login(client, "alice@test.dev", PASSWORD).data
    response = client.post("/api/v1/auth/refresh", {"refresh": tokens["refresh"]})
    assert response.status_code == 200
    assert "access" in response.data


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_user_and_workspaces(client, user):
    tokens = login(client, "alice@test.dev", PASSWORD).data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.data["user"]["email"] == "alice@test.dev"
    assert len(response.data["workspaces"]) == 1
    ws = response.data["workspaces"][0]
    assert ws["type"] == "personal"
    assert ws["role"] == "owner"


def test_signup_disabled_returns_403(client, settings):
    settings.SIGNUP_ENABLED = False
    response = client.post(
        "/api/v1/auth/signup",
        {"email": "new@test.dev", "password": PASSWORD, "name": "New"},
    )
    assert response.status_code == 403
