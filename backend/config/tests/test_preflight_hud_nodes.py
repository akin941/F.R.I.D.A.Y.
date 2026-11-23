"""Phase 9 closeout: the HUD health panel reads the new node check names.
`manage.py preflight` leaves the failing check names in the shared cache
(`HUD_PREFLIGHT_CACHE_KEY`) and `hud.services.health_section` forwards them
-- names and counts only, never a detail string (a host, a key id, an env
var name). It lives in `config` because it drives `ops` (a leaf no app may
import) and reads `hud` (a leaf too)."""

import functools
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.services import register_user
from hud.services import health_section
from ops import preflight
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def test_the_hud_shows_the_failing_node_check_names(settings, monkeypatch):
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)

    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.PREFLIGHT_NODE_GATEWAY_URL = "http://node-gateway:8002/healthz"

    def unreachable(url, *, timeout):
        raise ConnectionError("node-gateway down")

    monkeypatch.setattr(preflight, "http_get", unreachable)
    monkeypatch.setattr(preflight, "worker_signing_key_id", lambda *, timeout: "0123456789abcdef")
    monkeypatch.setattr(preflight, "node_conformance_drift", lambda: ["cases/v1/x.json"])
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        functools.partial(preflight.run_preflight, checks=(("nodes", preflight.check_nodes),)),
    )
    with pytest.raises(SystemExit):
        call_command("preflight", "--no-model-calls", stdout=StringIO())

    block = health_section(workspace, owner)["preflight"]
    assert block["status"] == "fail"
    assert block["failed_checks"] == ["node_gateway", "node_signing_key", "node_conformance"]
    # The node line itself is the node link's own block (branch 7).
    assert health_section(workspace, owner)["node"]["status"] == "never_paired"
    text = str(block)
    for detail in ("node-gateway:8002", "0123456789abcdef", "cases/v1/x.json"):
        assert detail not in text
