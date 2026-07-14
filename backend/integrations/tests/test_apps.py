from django.apps import apps


def test_integrations_app_is_installed():
    assert apps.is_installed("integrations")
