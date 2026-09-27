"""Throttle backend outage handling (Phase 4 branch 1 review P2)."""

from unittest import mock

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_login_returns_503_not_500_when_the_throttle_store_is_down():
    with mock.patch(
        "rest_framework.throttling.SimpleRateThrottle.allow_request",
        side_effect=ConnectionError("redis down"),
    ):
        response = APIClient().post(
            "/api/v1/auth/login", {"email": "x@example.test", "password": "y"}, format="json"
        )
    assert response.status_code == 503
    assert response.json()["detail"]
