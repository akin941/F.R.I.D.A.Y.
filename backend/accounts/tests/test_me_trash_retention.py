"""`/auth/me` states the trash retention window (Phase 4 branch 5, ADR-009):
the delete confirmation dialogs name it."""

import pytest

from accounts.services import register_user

pytestmark = pytest.mark.django_db


def test_me_reports_trash_retention_days(settings, web_client):
    settings.TRASH_RETENTION_DAYS = 12
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")

    response = web_client(owner).get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["trash_retention_days"] == 12
