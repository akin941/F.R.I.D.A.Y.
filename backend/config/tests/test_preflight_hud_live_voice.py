"""Phase 11 closeout: the HUD health panel reads the new live-voice check
names. `manage.py preflight` leaves the failing check names in the shared
cache (`HUD_PREFLIGHT_CACHE_KEY`) and `hud.services.health_section`
forwards them -- names and counts only, never a detail string (a setting
name, a path, a view name, a reason code). It lives in `config` because it
drives `ops` (a leaf no app may import) and reads `hud` (a leaf too)."""

import functools
import json
from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework.views import APIView

from accounts.authentication import NodeVoiceTokenAuthentication
from accounts.services import register_user
from hud.services import health_section
from ops import preflight
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db


class LeakingView(APIView):
    authentication_classes = [NodeVoiceTokenAuthentication]


def test_the_hud_shows_the_failing_live_voice_check_names(settings, monkeypatch, tmp_path):
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)

    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True  # without evidence: a failure
    result = tmp_path / "selftest.json"
    result.write_text(
        json.dumps(
            {
                "schema": "friday-voice/selftest/v1",
                "mode": "nightly",
                "result": "fail",
                "at": "2026-11-24T03:00:00Z",
                "wakes": 1,
                "posted_turns": 1,
                "reason": "posted_turn",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    settings.VOICE_LIVE_SELFTEST_RESULT_PATH = str(result)
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = str(tmp_path / "friday-voice")
    (tmp_path / "friday-voice").mkdir()  # a checkout without its lock
    real_routes = preflight.drf_routes()
    monkeypatch.setattr(
        preflight,
        "drf_routes",
        lambda: [*real_routes, ("api/v1/tasks/leak", LeakingView, [NodeVoiceTokenAuthentication])],
    )
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        functools.partial(
            preflight.run_preflight, checks=(("live_voice", preflight.check_live_voice),)
        ),
    )
    with pytest.raises(SystemExit):
        call_command("preflight", "--no-model-calls", stdout=StringIO())

    block = health_section(workspace, owner)["preflight"]
    assert block["status"] == "fail"
    assert block["failed_checks"] == [
        "voice_live_channel",
        "voice_live_policy",
        "voice_live_selftest",
        "friday_voice_conformance",
    ]
    assert block["warn"] == 1  # node_voice_state: no node paired (Mac session)
    text = str(block)
    for detail in (
        "VOICE_LIVE_ALWAYS_ON_ENABLED",
        "LeakingView",
        "posted_turn",
        "conformance.lock",
        str(tmp_path),
    ):
        assert detail not in text
