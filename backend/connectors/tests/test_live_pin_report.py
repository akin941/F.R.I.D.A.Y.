"""`live_pin_report` (Phase 5 closeout, used by `manage.py preflight`): a
live `tools/list` against the manifest pins that stores nothing and audits
nothing -- the pin beat stays the one writer of binding health."""

import pytest

from audit.models import AuditLog
from connectors import mcp_client
from connectors.models import ConnectorBinding
from connectors.services import live_pin_report
from connectors.tests import fakes

pytestmark = pytest.mark.django_db


def test_matching_pins_report_ok(fake_manifest, server):
    assert live_pin_report(fake_manifest, timeout=5) == {
        "fake_search": "ok",
        "fake_create_note": "ok",
    }


def test_drift_and_missing_are_reported_without_side_effects(
    fake_manifest, workspace, enable, server
):
    binding = enable(fake_manifest, workspace)
    health_before = dict(binding.last_health)
    audits_before = AuditLog.objects.count()
    server.schemas["fake_search"] = {"type": "object", "properties": {"q": {"type": "integer"}}}
    del server.schemas["fake_create_note"]

    assert live_pin_report(fake_manifest, timeout=5) == {
        "fake_search": "mismatch",
        "fake_create_note": "missing",
    }
    binding = ConnectorBinding.objects.get(pk=binding.pk)
    assert binding.last_health == health_before
    assert binding.last_health["tools"]["fake_search"] == "ok"
    assert AuditLog.objects.count() == audits_before


def test_unconfigured_server_raises_not_configured(fake_manifest, server, monkeypatch):
    monkeypatch.delenv(fakes.URL_ENV)
    with pytest.raises(mcp_client.ConnectorError) as exc:
        live_pin_report(fake_manifest, timeout=5)
    assert exc.value.code == "not_configured"
