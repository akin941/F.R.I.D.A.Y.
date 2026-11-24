"""Phase 11 branch 5 (brief D11 trigger backstop, D16 placement): the
`accounts` guards injected into the `voice` routes by `config/urls.py`.

- `X-Friday-Voice-Trigger: wake` on live sessions and transcriptions -> 403
  `voice.always_on_not_permitted` while no model's latest household +
  positives pair passes (or the setting is off), accepted when one does;
- `ptt` and `barge_in` always accepted; a missing or unknown header on the
  node voice channel -> 400 `voice.trigger_invalid`; the header is ignored
  on the web channel;
- the storm guard sits on the transcription route only; neither guard is
  on speech or the receipt;
- `voice` itself imports neither `accounts` nor `audit` (the guards come in
  through `build_urlpatterns`).
"""

import ast
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts import voice_live
from accounts.models import VoiceWakeEvaluation
from accounts.tests.node_voice_helpers import SESSIONS_URL, bearer, mint_token, pair_node
from assistant.services import create_conversation
from voice.tests.conftest import TRANSCRIPTIONS, make_wav

pytestmark = pytest.mark.django_db


@pytest.fixture
def node(owner):
    return pair_node(owner)


@pytest.fixture
def live(owner, workspace):
    return create_conversation(workspace=workspace, actor=owner, channel="node_voice")


def client_with(node, trigger):
    return bearer(mint_token(node[1]), trigger=trigger)


def upload(client, conversation_id):
    return client.post(
        TRANSCRIPTIONS,
        {
            "conversation": str(conversation_id),
            "audio": SimpleUploadedFile("u.wav", make_wav(seconds=1), content_type="audio/wav"),
        },
        format="multipart",
    )


def open_session(client, _conversation_id=None):
    return client.post(SESSIONS_URL, {}, format="json")


ROUTES = {"stt": (upload, 200), "sessions": (open_session, 201)}


def permit(owner, settings):
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    for kind, extra in (
        (VoiceWakeEvaluation.Kind.HOUSEHOLD, {"hours": "24", "false_accepts": 2}),
        (VoiceWakeEvaluation.Kind.POSITIVES, {"positives": 50, "false_rejects": 1}),
    ):
        voice_live.record_evaluation(
            owner=owner,
            model_sha256="c" * 64,
            kind=kind,
            threshold="0.5",
            refractory_ms=2000,
            **extra,
        )


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_wake_is_refused_while_not_permitted(node, live, speech_providers, route):
    call, _ = ROUTES[route]
    response = call(client_with(node, "wake"), live.id)
    assert response.status_code == 403
    assert response.json()["code"] == "voice.always_on_not_permitted"


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_wake_is_refused_with_rows_but_the_setting_off(
    owner, node, live, speech_providers, settings, route
):
    permit(owner, settings)
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = False
    call, _ = ROUTES[route]
    assert call(client_with(node, "wake"), live.id).status_code == 403


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_wake_is_accepted_when_a_models_latest_pair_passes(
    owner, node, live, speech_providers, settings, route
):
    permit(owner, settings)
    call, expected = ROUTES[route]
    assert call(client_with(node, "wake"), live.id).status_code == expected


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("trigger", ["ptt", "barge_in"])
def test_ptt_and_barge_in_are_always_accepted(node, live, speech_providers, route, trigger):
    call, expected = ROUTES[route]
    assert call(client_with(node, trigger), live.id).status_code == expected


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("trigger", [None, "", "WAKE", "hotkey", "wake,ptt"])
def test_a_missing_or_unknown_trigger_is_a_400(node, live, speech_providers, route, trigger):
    call, _ = ROUTES[route]
    response = call(client_with(node, trigger), live.id)
    assert response.status_code == 400
    assert response.json()["code"] == "voice.trigger_invalid"


@pytest.mark.parametrize("trigger", [None, "wake", "nonsense"])
def test_the_header_is_ignored_on_the_web_channel(owner, live, web_client, speech_providers,
                                                  trigger):  # fmt: skip
    client = web_client(owner)
    if trigger is not None:
        client.credentials(HTTP_X_FRIDAY_VOICE_TRIGGER=trigger)
    assert upload(client, live.id).status_code == 200


def test_the_guards_sit_on_exactly_the_routes_the_brief_names():
    from django.urls import get_resolver

    from accounts.voice_live import NodeVoiceStormGuard, NodeVoiceTriggerGuard

    found = {}
    for pattern in get_resolver().url_patterns:
        for sub in getattr(pattern, "url_patterns", []):
            view = getattr(sub.callback, "view_class", None)
            if view is None or not str(sub.pattern).startswith("voice/"):
                continue
            initkwargs = sub.callback.view_initkwargs
            classes = initkwargs.get("permission_classes", view.permission_classes)
            found[str(sub.pattern)] = {
                "trigger": NodeVoiceTriggerGuard in classes,
                "storm": NodeVoiceStormGuard in classes,
            }
    assert found == {
        "voice/transcriptions": {"trigger": True, "storm": True},
        "voice/speech": {"trigger": False, "storm": False},
        "voice/receipt": {"trigger": False, "storm": False},
        "voice/live/sessions": {"trigger": True, "storm": False},
    }


def test_voice_imports_neither_accounts_nor_audit():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in ("accounts", "audit"), (path, name)
