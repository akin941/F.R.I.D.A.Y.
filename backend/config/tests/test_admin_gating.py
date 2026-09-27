"""Django admin is break-glass only (Phase 4 Q3): routed when
DJANGO_ADMIN_ENABLED, never able to edit ownership directly."""

import importlib

import pytest
from django.contrib import admin
from django.test import override_settings
from django.urls import clear_url_caches

import config.urls
from accounts.models import User


def _reload_urls():
    clear_url_caches()
    importlib.reload(config.urls)


@pytest.fixture
def restore_urls():
    yield
    _reload_urls()


@pytest.mark.django_db
def test_admin_is_not_routed_when_disabled(client, restore_urls):
    with override_settings(DJANGO_ADMIN_ENABLED=False, ROOT_URLCONF="config.urls"):
        _reload_urls()
        assert client.get("/admin/login/").status_code == 404


@pytest.mark.django_db
def test_admin_is_routed_when_enabled(client, restore_urls):
    with override_settings(DJANGO_ADMIN_ENABLED=True, ROOT_URLCONF="config.urls"):
        _reload_urls()
        assert client.get("/admin/login/").status_code == 200


def test_is_owner_is_read_only_in_admin():
    """Ownership moves only via `manage.py set_owner`, which revokes the
    outgoing owner's devices; the admin form must not bypass that."""
    model_admin = admin.site._registry[User]
    assert "is_owner" in model_admin.get_readonly_fields(request=None)
