import pytest
from django.contrib.admin.sites import site as admin_site

from accounts.services import register_user
from ai_core.models import ModelProvider, ModelUsage, PromptTemplate, PromptVersion

pytestmark = pytest.mark.django_db


def test_ai_models_registered_in_admin():
    for model in (ModelProvider, PromptTemplate, PromptVersion, ModelUsage):
        assert model in admin_site._registry


def test_model_usage_admin_is_readonly(rf):
    user = register_user(email="admin@test.dev", password="Str0ngDevPass!42")
    model_admin = admin_site._registry[ModelUsage]
    request = rf.get("/admin/ai_core/modelusage/")
    request.user = user
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
