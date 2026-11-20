"""Shared fixtures for the Phase 8A ingestion tests. Test modules that
define their own `owner` etc. keep theirs (a module fixture wins)."""

import pytest

from accounts.services import register_user
from core.channels import CHANNEL_WEB
from memory.ingestion.services import ensure_default_sources
from memory.models import IngestionSource
from memory.tests.ingestion_helpers import PASSWORD
from workspaces.models import Workspace


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    from workspaces.services import create_firm_workspace

    return create_firm_workspace(actor=owner, name="Acme", channel=CHANNEL_WEB)


@pytest.fixture
def sources(owner, personal):
    ensure_default_sources(workspace=personal, created_by=owner)
    return {s.kind: s for s in IngestionSource.objects.filter(workspace=personal)}


@pytest.fixture
def hooks(sources):
    source = sources[IngestionSource.Kind.CLAUDE_CODE_HOOKS]
    source.enabled = True
    source.save()
    return source
