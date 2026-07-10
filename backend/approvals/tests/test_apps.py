import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings


def _approvals_app_config():
    return apps.get_app_config("approvals")


def test_ready_raises_if_self_approval_enabled_outside_debug():
    with override_settings(DEV_ALLOW_SELF_APPROVAL=True, DEBUG=False):
        with pytest.raises(ImproperlyConfigured):
            _approvals_app_config().ready()


def test_ready_is_fine_with_self_approval_enabled_in_debug():
    with override_settings(DEV_ALLOW_SELF_APPROVAL=True, DEBUG=True):
        _approvals_app_config().ready()  # must not raise


def test_ready_is_fine_with_self_approval_disabled_regardless_of_debug():
    with override_settings(DEV_ALLOW_SELF_APPROVAL=False, DEBUG=False):
        _approvals_app_config().ready()  # must not raise
