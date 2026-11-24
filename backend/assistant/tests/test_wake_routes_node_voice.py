# ruff: noqa: F811 -- pytest fixtures imported from wake_fixtures are redefined as arguments
"""Phase 12 D7/D8 (branch 3): the spoken digest on `node_voice` -- the
server-side contract the Mac session (M1) must pass.

A `node_voice` token minted with a paired node's secret (exactly as the
Swift host and the Phase 11 reference host do), a live session
conversation (`opened_via=node_voice`), and "Hey Friday, günaydın":

- the reply is the stored `spoken_text`, with no model call and no
  `ModelUsage` row; `spoken_at` is set with `spoken_channel=node_voice`;
- the only row the route itself writes is that digest telemetry (the two
  turn messages aside): no notice, memory, task, approval, extraction job,
  audit row or schedule;
- no digest today -> the template; a firm digest never answers a personal
  session;
- Phase 12 adds no node protocol surface: `build_node_conformance --check`
  and the `friday-voice` child's `conformance --check` against the
  unchanged `conformance.lock` stay green."""

import shutil
import subprocess
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings as django_settings
from django.core.management import call_command

from accounts import devices
from accounts.tests.node_voice_helpers import bearer, mint_token, pair_node
from ai_core.models import ModelUsage
from assistant.models import Message
from assistant.services import create_conversation
from assistant.tests.wake_fixtures import (  # noqa: F401 -- fixtures
    chat_setup,
    chat_turn,
    firm,
    make_digest,
    make_notice,
    member,
    owner,
    personal,
    reply_text,
)
from assistant.wake_routes import NO_DIGEST_TEXT
from proactive.models import DigestRun, ProactiveNotice

pytestmark = pytest.mark.django_db

FRIDAY_VOICE = Path(django_settings.BASE_DIR).resolve().parent / "node" / "friday-voice"
DIGEST = "Günaydın. Bugün 2 onay bekliyor ve ilk toplantın saat 10:00'da."


@pytest.fixture
def voice(owner, chat_setup):
    _, secret = pair_node(owner)
    return bearer(mint_token(secret))


@pytest.fixture
def live(owner, personal):
    return create_conversation(workspace=personal, actor=owner, channel="node_voice")


def _row_counts():
    return {
        model._meta.label: model.objects.count()
        for model in apps.get_models()
        if not model._meta.proxy and model._meta.managed
    }


def test_hey_friday_gunaydin_speaks_the_stored_digest(owner, personal, voice, live):
    digest = make_digest(personal, owner, text=DIGEST)
    events = chat_turn(voice, live, "Hey Friday, günaydın")
    assert reply_text(events) == DIGEST
    done = events[-1]
    assert done["type"] == "done"
    assert done["usage"] == {"input_tokens": 0, "output_tokens": 0, "estimated_cost": "0"}
    assert not ModelUsage.objects.exists()
    user, reply = Message.objects.filter(conversation=live).order_by("created_at")
    assert user.channel == reply.channel == Message.Channel.NODE_VOICE
    assert user.input_mode == Message.InputMode.VOICE
    assert user.route == reply.route == "digest.today"
    assert reply.content == DIGEST and reply.model_usage is None
    digest.refresh_from_db()
    assert digest.spoken_at is not None
    assert digest.spoken_channel == DigestRun.SpokenChannel.NODE_VOICE
    assert digest.opened_at is None


@pytest.mark.parametrize("asserted", [None, "text"])
def test_the_body_cannot_opt_out_of_routing(owner, personal, voice, live, asserted):
    # node_voice forces input_mode=voice, so the route applies whatever the
    # body asserts.
    make_digest(personal, owner, text=DIGEST)
    body = {"input_mode": asserted} if asserted else {}
    events = chat_turn(voice, live, "Friday, brifing", **body)
    assert reply_text(events) == DIGEST


def test_the_route_writes_only_the_digest_telemetry(owner, personal, voice, live):
    digest = make_digest(personal, owner, text=DIGEST)
    notice = make_notice(personal)
    before = _row_counts()
    snapshot = DigestRun.objects.filter(pk=digest.pk).values().get()
    chat_turn(voice, live, "Hey Friday, günaydın")
    after = _row_counts()
    changed = {
        label: after[label] - before[label] for label in after if after[label] != before[label]
    }
    # The turn's own two messages; nothing else is created (no extraction
    # job, audit row, notice, memory, task, approval or ModelUsage).
    assert changed == {"assistant.Message": 2}
    now = DigestRun.objects.filter(pk=digest.pk).values().get()
    differing = {key for key in now if now[key] != snapshot[key]}
    assert differing == {"spoken_at", "spoken_channel"}
    notice.refresh_from_db()
    assert notice.read_at is None and notice.dismissed_at is None


def test_notices_read_on_node_voice_marks_nothing(owner, personal, voice, live):
    make_notice(personal, key="a", title="Yeni görev: Faturayı öde")
    before = _row_counts()
    events = chat_turn(voice, live, "Hey Friday, bildirimler")
    assert "Faturayı öde" in reply_text(events)
    after = _row_counts()
    assert {k: after[k] - before[k] for k in after if after[k] != before[k]} == {
        "assistant.Message": 2
    }
    assert ProactiveNotice.objects.get().read_at is None
    assert not ModelUsage.objects.exists()


def test_no_digest_answers_the_template(owner, personal, voice, live):
    events = chat_turn(voice, live, "Hey Friday, günaydın")
    assert reply_text(events) == NO_DIGEST_TEXT
    assert not ModelUsage.objects.exists()
    assert Message.objects.filter(route="digest.today").count() == 2


def test_a_firm_digest_never_answers_a_personal_session(owner, personal, firm, voice, live):
    firm_digest = make_digest(firm, owner, text="Firma brifingi.")
    events = chat_turn(voice, live, "Hey Friday, günaydın")
    assert reply_text(events) == NO_DIGEST_TEXT
    firm_digest.refresh_from_db()
    assert firm_digest.spoken_at is None


def test_a_miss_on_node_voice_goes_to_the_router(owner, personal, voice, live):
    make_digest(personal, owner, text=DIGEST)
    events = chat_turn(voice, live, "Hey Friday, günaydın, yarın hava nasıl olacak?")
    assert events[-1]["type"] == "done"
    purposes = set(ModelUsage.objects.values_list("purpose", flat=True))
    assert {"router.intent", "chat"} <= purposes
    assert DigestRun.objects.get().spoken_at is None


def test_a_kill_switched_owner_gets_no_digest(owner, personal, voice, live):
    make_digest(personal, owner, text=DIGEST)
    devices.engage_node_kill_switch(owner=owner, via="test")
    response = voice.post(
        f"/api/v1/ai/conversations/{live.pk}/messages",
        {"content": "Hey Friday, günaydın"},
        format="json",
    )
    assert response.status_code == 401
    assert DigestRun.objects.get().spoken_at is None


# --- frozen protocols ------------------------------------------------------------


def test_the_node_conformance_files_are_unchanged():
    call_command("build_node_conformance", "--check")


def test_the_friday_voice_conformance_lock_is_unchanged():
    uv = shutil.which("uv")
    if uv is None or not (FRIDAY_VOICE / ".venv").is_dir():
        pytest.skip("node/friday-voice has no environment (run `uv sync --frozen` there)")
    result = subprocess.run(
        [
            uv,
            "run",
            "--no-sync",
            "--directory",
            str(FRIDAY_VOICE),
            "friday-voice",
            "conformance",
            "--check",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
