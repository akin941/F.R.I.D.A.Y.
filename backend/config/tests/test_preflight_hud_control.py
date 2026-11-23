"""Phase 10 closeout: the HUD health panel reads the new control check
names. `manage.py preflight` leaves the failing check names in the shared
cache (`HUD_PREFLIGHT_CACHE_KEY`) and `hud.services.health_section`
forwards them -- names and counts only, never a detail string (a setting
name, a pin, a host). It lives in `config` because it drives `ops` (a leaf
no app may import) and reads `hud` (a leaf too)."""

import functools
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.services import register_user
from hud.services import health_section
from ops import preflight
from tools import node_action_ports
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def ports():
    state = node_action_ports.snapshot_ports()
    yield
    node_action_ports.restore_ports(state)


def test_the_hud_shows_the_failing_control_check_names(settings, monkeypatch, ports):
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)

    settings.PREFLIGHT_NODE_GATEWAY_URL = "http://node-gateway:8002/healthz"
    settings.NODE_GRANT_TTL_SECONDS = 300
    settings.HARNESS_DRIVERS_ENABLED = True
    settings.HARNESS_DRIVER_CODEX = "app_server"
    settings.HARNESS_DRIVER_TERMS_CHECKED_ON = ""
    node_action_ports.register_trust_check(node_action_ports.pending_trust_check)

    def healthz(url, *, timeout):
        return 200, '{"status": "ok", "redis": true, "output_scrubber": false}'

    monkeypatch.setattr(preflight, "http_get", healthz)
    monkeypatch.setattr(preflight, "node_conformance_drift", lambda: ["cases/v2/x.json"])
    monkeypatch.setattr(preflight, "escape_suite_report", lambda: (False, 31, 32))
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        functools.partial(preflight.run_preflight, checks=(("control", preflight.check_control),)),
    )
    with pytest.raises(SystemExit):
        call_command("preflight", "--no-model-calls", stdout=StringIO())

    block = health_section(workspace, owner)["preflight"]
    assert block["status"] == "fail"
    assert block["failed_checks"] == [
        "node_grant_config",
        "node_conformance_v2",
        "node_escape_suite",
        "node_output_scrubber",
        "trust_check",
        "harness_drivers:codex_cli",
    ]
    assert block["warn"] == 1  # node_protocol_v2: no node paired (Mac session)
    text = str(block)
    for detail in ("NODE_GRANT_TTL_SECONDS", "cases/v2/x.json", "node-gateway:8002", "31/32"):
        assert detail not in text
