"""Phase 8B closeout: the HUD health panel reads the new graph check names.
`manage.py preflight` leaves the failing check names in the shared cache
(`HUD_PREFLIGHT_CACHE_KEY`) and `hud.services.health_section` forwards them
-- names and counts only, never a detail string (a URL, an image label, an
index name). It lives in `config` because it drives `ops` (a leaf no app may
import) and reads `hud` (a leaf too)."""

import functools
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.services import register_user
from ai_core.models import ModelProvider
from hud.services import health_section
from memory.models import MemoryEntity
from memory.tests.graph_helpers import entity, fact
from ops import preflight
from workspaces.models import Workspace
from workspaces.services import create_firm_workspace

pytestmark = pytest.mark.django_db


def test_the_hud_shows_the_failing_graph_check_names(settings, monkeypatch):
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    firm = create_firm_workspace(actor=owner, name="Acme", channel="web")

    # graph_integrity: an object entity moved to another workspace (raw write)
    api, hetzner = entity(workspace, "api"), entity(workspace, "hetzner")
    fact(workspace, api, obj=hetzner)
    MemoryEntity.all_objects.filter(pk=hetzner.pk).update(workspace=firm)
    # retrieval_indexes: an embedder added after the index build
    ModelProvider.objects.create(
        name="late-embedder",
        provider_type=ModelProvider.ProviderType.FAKE,
        capability=ModelProvider.Capability.EMBEDDING,
        model_id="fake-embedding-1",
        dimension=32,
    )
    # graphify: an image built from another pin (no Docker reached)
    monkeypatch.setattr(
        preflight,
        "docker_image_labels",
        lambda image, *, timeout: {preflight.GRAPHIFY_VERSION_LABEL: "0.0.1-other"},
    )
    # compressor: enabled, sidecar down
    settings.AI_COMPRESSION_ENABLED = True
    settings.AI_COMPRESSION_PURPOSES = frozenset({"chat"})
    settings.AI_COMPRESSION_URL = "http://compressor:8080/compress"

    def unreachable(url, *, timeout):
        raise ConnectionError("compressor down")

    monkeypatch.setattr(preflight, "http_get", unreachable)
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        functools.partial(preflight.run_preflight, checks=(("graph", preflight.check_graph),)),
    )
    with pytest.raises(SystemExit):
        call_command("preflight", "--no-model-calls", stdout=StringIO())

    block = health_section(workspace, owner)["preflight"]
    assert block["status"] == "fail"
    assert block["failed_checks"] == [
        "graph_integrity",
        "retrieval_indexes",
        "graphify",
        "compressor",
    ]
    text = str(block)
    for detail in ("compressor:8080", "0.0.1-other", "_hnsw_", "crossing", "late-embedder"):
        assert detail not in text
