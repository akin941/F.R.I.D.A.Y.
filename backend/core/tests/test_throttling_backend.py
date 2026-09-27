"""Throttle backend outage handling (Phase 4 branch 1 review P2): every
throttled anonymous auth endpoint fails closed with a deliberate 503."""

from unittest import mock

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/v1/auth/refresh", {"refresh": "x"}),
        ("/api/v1/auth/enroll", {"code": "12345678", "device_name": "x"}),
        ("/api/v1/auth/devices/session", {"device_secret": "jdv_x"}),
    ],
)
def test_auth_endpoints_return_503_not_500_when_the_throttle_store_is_down(path, body):
    with mock.patch(
        "rest_framework.throttling.SimpleRateThrottle.allow_request",
        side_effect=ConnectionError("redis down"),
    ):
        response = APIClient().post(path, body, format="json")
    assert response.status_code == 503
    assert response.json()["detail"]
