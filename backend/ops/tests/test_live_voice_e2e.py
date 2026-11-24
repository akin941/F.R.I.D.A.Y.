"""Phase 11 closeout end to end (brief §8, "E2E (CI, fakes)"): the reference
voice host + a real `friday-voice` child process + a live Friday over real
HTTP.

The server is this test process's Django (`config.asgi`'s application)
served by uvicorn on a loopback port in a thread, with FakeAdapter scripted
per purpose (`tools.tests.test_node_action_trust.friday`) and the fake
speech provider rows (`speech`). The voice token is minted with the paired
node's secret exactly as the Swift host does (`POST /auth/nodes/
voice-token`). The driver (`friday-voice e2e`, `node/friday-voice/
friday_voice/e2e.py`) runs inside the child's own environment through
`uv run --no-sync --directory node/friday-voice` -- the backend imports
nothing from it -- plays the Swift host's side with `HostCore` (paced
playback, the echo double where a scenario needs it) and spawns
`friday-voice run` (or, for the self-trigger clip, the same loop with the
scripted detector stack, `friday_voice.e2e_child`). Its JSON lines are the
observations; its `await` lines are where this test acts as the owner
(the kill switch, the release button on screen).

1. push-to-talk turn spoken back sentence by sentence;
2. a voice turn naming a node action -> draft only, the card on
   `/chat/<id>` (the persisted user message), nothing requested;
3. barge-in mid-reply (the hotkey) -> playback stopped,
   `interrupted_at_ms` on the trace, the next turn answered;
4. the self-trigger clip at residual 1.0 -> no second turn;
5. kill switch mid-session -> next request refused, `release_mic`, the
   HUD's mic state `off`;
6. storm -> server mute, 423, release on screen re-enables.

Runs where `node/friday-voice` has its environment (`uv sync --frozen`
there; the CI `friday-voice` job). Elsewhere it is skipped, unless
`FRIDAY_VOICE_E2E=1` demands it. No real model, microphone, Mac or
network beyond the loopback."""

import asyncio
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.db import connections
from rest_framework.test import APIClient

from accounts import devices
from accounts.models import VoiceLiveMute
from accounts.tests.node_voice_helpers import MUTE_URL, FakeRedis
from accounts.voice_live import record_evaluation
from ai_core.adapters.base import SynthesizeAdapterResult, TranscribeAdapterResult
from ai_core.adapters.fake import FakeAdapter
from ai_core.models import ModelUsage
from nodes.selectors import node_health
from tools.tests.conftest import make_link, node_home, node_world  # noqa: F401 -- fixtures
from tools.tests.test_node_action_trust import (  # noqa: F401 -- fixtures
    Conversation,
    Message,
    friday,
    nothing_executed,
    propose,
    speech,
)

pytestmark = pytest.mark.django_db(transaction=True)

FRIDAY_VOICE = Path(django_settings.BASE_DIR).resolve().parent / "node" / "friday-voice"
REPLY = "Merhaba, ben Friday. Bugün takviminde iki toplantı var."
CLIP = "Hey Friday, terminali aç."
QUESTION = "bir örnek komut söyle"
CODE = {"repo_path": "~/code/friday", "command_kind": "test"}
WAKE_MODEL = "f" * 64  # the scripted detector's model hash (friday_voice.replay)
STATE_KEYS = (
    "state",
    "mode",
    "session_open",
    "wake_accepts_today",
    "wake_discards_today",
    "storm_mutes_today",
)


def _driver_prefix():
    uv = shutil.which("uv")
    ready = uv is not None and (FRIDAY_VOICE / ".venv").is_dir()
    if not ready:
        reason = "node/friday-voice has no environment (run `uv sync --frozen` there)"
        if os.environ.get("FRIDAY_VOICE_E2E") == "1":
            pytest.fail(reason)
        pytest.skip(reason)
    return [uv, "run", "--no-sync", "--directory", str(FRIDAY_VOICE), "friday-voice", "e2e"]


class LiveFriday:
    """This process's Django served by uvicorn on a loopback port."""

    def __init__(self):
        import uvicorn
        from django.core.asgi import get_asgi_application

        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        config = uvicorn.Config(
            get_asgi_application(), lifespan="off", log_level="warning", ws="none"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "uvicorn did not start"
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
        # Django ran the views on asgiref's shared sync thread: close the
        # connections it opened there, or the test database cannot be dropped.
        from asgiref.sync import sync_to_async

        asyncio.run(sync_to_async(connections.close_all, thread_sensitive=True)())


class DriverRun:
    """One `friday-voice e2e` process: its JSON lines, its `await` steps."""

    def __init__(self, url, token_file, scenario):
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
        self.process = subprocess.Popen(  # noqa: S603 -- our own driver, no shell
            [
                *_driver_prefix(),
                "--server",
                url,
                "--token-file",
                str(token_file),
                "--scenario",
                scenario,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.events = queue.Queue()
        self.states = []
        self.stderr = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.events.put(event)
        self.events.put({"event": "eof"})

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line.rstrip("\n"))

    def until(self, kind, timeout=180):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {kind!r} from the driver; stderr: {self.stderr[-20:]}"
            try:
                event = self.events.get(timeout=remaining)
            except queue.Empty:
                continue
            if event["event"] == "state":
                self.states.append({key: event[key] for key in STATE_KEYS})
                continue
            assert event["event"] != "eof", f"driver ended early; stderr: {self.stderr[-20:]}"
            if event["event"] == kind:
                return event

    def go(self):
        self.process.stdin.write("go\n")
        self.process.stdin.flush()

    def result(self):
        result = self.until("result")
        assert self.process.wait(timeout=30) == 0, self.stderr[-20:]
        return result


@pytest.fixture
def stack(node_world, friday, speech, make_link, settings, monkeypatch, tmp_path):  # noqa: F811
    _driver_prefix()  # skip before any setup when the child cannot run here
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    settings.VOICE_TTS_ROUTES = {"tr": {}, "en": {}}
    settings.VOICE_TTS_PROVIDER_PARAMS = {}
    settings.AI_PURPOSE_TIERS = {}
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    monkeypatch.setattr("core.revocation._client", lambda: FakeRedis())
    friday["chat"] = REPLY
    link = make_link()
    node = APIClient()
    node.credentials(HTTP_AUTHORIZATION=f"Bearer {node_world.node_secret}")
    minted = node.post("/api/v1/auth/nodes/voice-token")
    assert minted.status_code == 201, minted.content
    token_file = tmp_path / "voice-token"
    token_file.write_text(minted.json()["token"], encoding="utf-8")
    token_file.chmod(0o600)
    server = LiveFriday().start()
    yield SimpleNamespace(
        world=node_world, friday=friday, link=link, url=server.url, token=token_file
    )
    server.stop()


def run(stack, scenario):
    return DriverRun(stack.url, stack.token, scenario)


def _live_conversations(world):
    return list(Conversation().objects.filter(workspace=world.personal, opened_via="node_voice"))


def _user_messages(world):
    return list(
        Message()
        .objects.filter(conversation__workspace=world.personal, role="user")
        .order_by("created_at")
    )


def _usage(purpose):
    return ModelUsage.objects.filter(purpose=purpose, success=True).count()


def _traces():
    from django.apps import apps

    return list(apps.get_model("voice", "VoiceTurnTrace").objects.order_by("created_at"))


def _relay(stack, state):
    """What the Swift host does with the child's `state` (D15): relay it as
    the node's `voice.state` event. The reference node builds the v2 frame
    (`relay_voice_state`'s item: the child's fields plus `at`); the frame
    goes through the gateway's event handler (`nodes.events.handle_event`,
    what `NodeSocketApp` calls for an `event` frame). The socket path itself
    is `nodes/tests/test_node_voice_state.py`."""
    from nodes.events import handle_event

    item = {**state, "at": stack.link.node.clock.now()}
    outcome = handle_event(node=stack.world.node, frame=stack.link.node._voice_frame(item))
    assert outcome.stored == 1, outcome
    stack.world.node.refresh_from_db()
    assert stack.world.node.voice_state == state["state"]


def _hud_voice(world):
    """The HUD's node mic state: `hud.services.node_section` forwards
    `nodes.selectors.node_health`'s voice block (`hud` is a leaf `ops` may
    not import, so this reads the same block one layer down)."""
    return node_health(world.owner)["voice"]["state"]


# --- 1. push-to-talk ---------------------------------------------------------


def test_a_push_to_talk_turn_is_spoken_back_sentence_by_sentence(stack):
    result = run(stack, "ptt").result()
    assert result["outcome"]["turns"] == [True]
    assert result["started"] == ["t1s0", "t1s1"]  # one utterance per sentence
    assert result["ended"] == ["t1s0", "t1s1"]
    assert result["stopped"] == [] and result["failures"] == []
    assert result["last_state"]["state"] == "armed_ptt"
    (conversation,) = _live_conversations(stack.world)
    (user,) = _user_messages(stack.world)
    assert user.conversation_id == conversation.pk
    assert (user.channel, user.input_mode) == ("node_voice", "voice")
    assert _usage("voice.stt") == 1 and _usage("voice.tts") == 2
    assert {trace.source for trace in _traces()} == {"node"}
    # The HUD shows the node's mic state once the host relays it (D15).
    _relay(stack, result["last_state"])
    assert _hud_voice(stack.world) == "armed_ptt"


# --- 2. a node action by voice: a draft only --------------------------------


def test_a_voice_turn_naming_a_node_action_is_a_card_on_screen_and_nothing_else(stack, web_client):
    propose(stack.friday, stack.world.node, "repo.command.run", CODE)
    result = run(stack, "node_action").result()
    assert result["outcome"]["turns"] == [True]
    # The reply, then "Kart ekranda." (the card is on screen).
    assert len(result["ended"]) >= 2 and result["stopped"] == []
    (conversation,) = _live_conversations(stack.world)
    page = web_client(stack.world.owner).get(f"/api/v1/ai/conversations/{conversation.pk}/messages")
    assert page.status_code == 200, page.content
    body = page.json()
    rows = body["results"] if isinstance(body, dict) else body
    (user,) = [row for row in rows if row["role"] == "user"]
    # What `drafts.ts` rebuilds the card from on `/chat/<id>` (D4).
    assert user["channel"] == "node_voice" and user["intent"] == "tool_request"
    assert user["intent_payload"]["tool_name"] == "node_action"
    assert user["intent_payload"]["payload"]["args"] == CODE
    stack.link.pump()
    nothing_executed(stack.world)


# --- 3. barge-in -------------------------------------------------------------


def test_barge_in_mid_reply_stops_playback_and_the_next_turn_is_answered(stack):
    result = run(stack, "barge_in").result()
    assert result["outcome"]["turns"] == [True, True]
    assert any(uid.startswith("t1s") for uid in result["stopped"])
    assert "t2s0" in result["ended"]
    assert len(_user_messages(stack.world)) == 2
    interrupted = [t.interrupted_at_ms for t in _traces() if t.interrupted_at_ms is not None]
    assert interrupted and all(0 <= value < 60_000 for value in interrupted)


# --- 4. the self-trigger clip at residual 1.0 --------------------------------


@pytest.fixture
def echo_server(stack, monkeypatch, settings):
    """Always-on permitted for the scripted detector's model (D11), fake TTS
    speaking the clip, fake STT hearing what was last spoken (residual 1.0:
    no echo cancellation at all)."""
    settings.VOICE_LIVE_ALWAYS_ON_ENABLED = True
    for kind, numbers in (
        ("household", {"hours": 24, "false_accepts": 2}),
        ("positives", {"positives": 50, "false_rejects": 3}),
    ):
        record_evaluation(
            owner=stack.world.owner,
            model_sha256=WAKE_MODEL,
            kind=kind,
            threshold="0.5",
            refractory_ms=2000,
            **numbers,
        )
    stack.friday["chat"] = CLIP
    clip = (FRIDAY_VOICE / "fixtures" / "selftrigger_hey_friday_terminali_ac.wav").read_bytes()
    spoken = []

    def synthesize(self, text, *, params=None, purpose=""):
        spoken.append(text)
        return SynthesizeAdapterResult(
            audio=clip,
            content_type="audio/wav",
            audio_seconds=2.0,
            input_tokens=4,
            raw_usage={"fake": True},
        )

    def transcribe(self, audio, *, content_type="", params=None, purpose=""):
        text = spoken[-1].rstrip(".") if spoken else QUESTION
        return TranscribeAdapterResult(
            text=text, audio_seconds=1.0, output_tokens=4, raw_usage={"fake": True}
        )

    monkeypatch.setattr(FakeAdapter, "synthesize", synthesize)
    monkeypatch.setattr(FakeAdapter, "transcribe", transcribe)
    return spoken


def test_the_self_trigger_clip_at_residual_one_posts_no_turn(stack, echo_server):
    result = run(stack, "selftrigger").result()
    assert result["outcome"]["armed"] is True  # the server permitted the model
    assert "wake" in result["earcons"]  # the scripted detector fired on the echo
    # Friday's own words were uploaded and discarded: one turn, one reply.
    assert len(_user_messages(stack.world)) == 1
    assert [m.content for m in _user_messages(stack.world)] == [QUESTION]
    assert _usage("voice.stt") == 2
    assert echo_server == [CLIP]  # nothing spoken in reply to the echo
    assert all(uid.startswith("t1s") for uid in result["started"])
    assert result["last_state"]["wake_discards_today"] >= 1


# --- 5. kill switch mid-session ---------------------------------------------


def test_the_kill_switch_mid_session_refuses_the_next_request_and_releases_the_mic(stack):
    driver = run(stack, "kill_switch")
    step = driver.until("await")
    assert step["step"] == "kill_switch" and step["ended"] == ["t1s0", "t1s1"]
    _relay(stack, step["last_state"])
    assert _hud_voice(stack.world) == "armed_ptt"
    devices.engage_node_kill_switch(owner=stack.world.owner, via="web")
    stt_before = _usage("voice.stt")
    driver.go()
    result = driver.result()
    refused = result["outcome"]["after_refusal"]
    assert result["outcome"]["released_after_refusal"] is True
    assert "child:credential_refused" in refused["releases"]
    assert refused["mic_held"] is False
    assert (result["indicator"], result["mic_held"]) == ("off", False)
    assert len(_user_messages(stack.world)) == 1  # nothing after the switch
    assert _usage("voice.stt") == stt_before
    assert _hud_voice(stack.world) == "off"


# --- 6. storm ----------------------------------------------------------------


def test_a_storm_mutes_the_node_until_the_owner_releases_it_on_screen(stack, settings, web_client):
    settings.VOICE_LIVE_STORM_MAX_STT_PER_MINUTE = 2
    driver = run(stack, "storm")
    step = driver.until("await")
    assert step["step"] == "storm_muted"
    assert step["last_state"]["state"] == "storm_muted"
    assert "child:storm" in step["releases"] and step["mic_held"] is False
    mute = VoiceLiveMute.objects.get(node_device=stack.world.node.device)
    assert mute.released_at is None
    assert len(_user_messages(stack.world)) == 2  # the third upload was the storm
    # The release button on /voice (BFF -> DELETE .../voice-mute, web session).
    released = web_client(stack.world.owner).delete(MUTE_URL.format(stack.world.node.device.pk))
    assert released.status_code in (200, 204), released.content
    driver.go()
    result = driver.result()
    assert result["outcome"]["storm_muted"] is True
    assert result["outcome"]["answered"] is True
    assert result["last_state"]["state"] == "armed_ptt"
    assert len(_user_messages(stack.world)) == 3
    mute.refresh_from_db()
    assert mute.released_at is not None
