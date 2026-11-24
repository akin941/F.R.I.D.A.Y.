# ruff: noqa: F811 -- pytest fixtures imported from the fixture modules are redefined as arguments
"""Phase 12 closeout end to end (brief §8, "e2e"): the reach features
through their real entry points, with fakes only at the edges.

1. **Digest.** The owner makes a `morning_digest` schedule over the web;
   the heartbeat (`run_due_schedules`, the `proactive.tasks.heartbeat`
   body) runs the boot probes, makes exactly one `proactive.digest` call
   (FakeAdapter, one `ModelUsage`) and stores the digest; `/today` reads it
   and the owner's `ack` marks it opened.
2. **Spoken digest on the Mac path.** The Phase 11 reference host + a real
   `friday-voice run` child + this Django over real HTTP (the live-voice
   e2e's harness): "günaydın" -> the stored digest is spoken sentence by
   sentence (the host's playback double), no model call, `spoken_at` set
   with `node_voice`; "bildirimler" -> the unread notice titles are read
   and nothing is marked read. Skipped where `node/friday-voice` has no
   environment (as the live-voice e2e).
3. **n8n.** Through the in-process relay to the fake n8n trigger (SDK
   in-memory server): an async read from a `node_voice` turn ends in one
   follow-up and, on the next heartbeat, one "Sonuç hazır" notice; a write
   by voice is a draft card only, the web approval sends it exactly once and
   the next heartbeat announces "n8n: Ev yedeği bitti".
4. **Skills.** A SKILL.md uploaded over the web stays `proposed` until the
   owner approves that hash; then a chat turn matches it (`skills.match`)
   and the chat completion carries it as an owner-approved `<skill>` block.
5. **Playbooks.** Three succeeded, reviewed fake harness jobs on one repo ->
   one provenance-checked playbook proposal (one `skills.playbook_distill`
   call) -> approved -> attachable to a new implementation request.
6. **Unattended deny.** One heartbeat run whose probes try every D2 effect
   (the branch-1 table plus "cron cannot create cron": schedule, pause,
   alias, skill and digest writes) -> every one denied in its owning
   service with one audit row each, nothing created.

No real model, n8n, MCP server outside the SDK's in-memory one, Mac,
microphone or network beyond the loopback."""

import json
from datetime import time
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.services import register_user
from ai_core.adapters import fake as fake_adapter
from ai_core.models import ModelProvider, ModelUsage
from approvals.models import ApprovalRequest
from assistant.models import Message, PendingFollowUp, WakeRouteAlias
from assistant.tests.skill_fixtures import (  # noqa: F401 -- fixtures
    match_prompt,
    matcher_answer,
    recorder,
)
from assistant.tests.wake_fixtures import chat_setup, make_digest, make_notice  # noqa: F401
from assistant.wake_aliases import create_wake_alias
from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION
from connectors.models import ConnectorWriteExecution
from connectors.tests import fakes
from connectors.tests.conftest import (  # noqa: F401 -- fixtures
    committed,
    enable,
    n8n,
    n8n_binding,
    n8n_state,
)
from connectors.tests.n8n_chat_fixtures import (  # noqa: F401 -- fixtures
    calls,
    chat_turn,
    live,
    n8n_chat,
    reply_text,
    route,
    voice,
)
from core import probes
from core.channels import CHANNEL_WEB
from harness.models import HarnessRepo
from harness.tests.test_playbook_hook import GOALS, make_done_job
from memory.models import Memory
from ops.tests.test_live_voice_e2e import DriverRun, stack  # noqa: F401 -- fixture
from proactive import services as proactive_services
from proactive.models import DigestRun, ProactiveNotice, ProactiveRun, ProactiveSchedule
from proactive.runs import run_schedule
from proactive.tasks import run_due_schedules
from proactive.tests.conftest import FakeProbe
from proactive.tests.test_unattended_guard import EFFECTS
from skills import playbooks
from skills import services as skill_services
from skills.models import InstructionSkill, InstructionSkillVersion, PlaybookDistillRun
from tasks import services as task_services
from tasks.models import Task
from tools.tests.conftest import make_link, node_home, node_world  # noqa: F401 -- fixtures
from tools.tests.test_node_action_trust import friday, speech  # noqa: F401 -- fixtures
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
SCHEDULES = "/api/v1/proactive/schedules"
DIGESTS = "/api/v1/proactive/digests"
NOTICES = "/api/v1/proactive/notices"
SKILLS = "/api/v1/skills"
APPROVALS = "/api/v1/approvals/requests"
DIGEST_TEXT = (
    "Günaydın, bugün seni bekleyen 2 onay ve 3 görev var. "
    "İlk toplantın saat 10:00'da, öncesinde kısa bir mola ver."
)
STARTED = "Başlattım: Sunucu durumu. Sonucu bu konuşmaya yazacağım."


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def digest_ai(db, settings):
    """The `proactive.digest` prompt and budget (ai_core 0009) and a local
    FakeAdapter provider allowed personal data."""
    import_module(
        "ai_core.migrations.0009_proactive_digest_prompt_and_budget"
    ).create_prompt_and_budget(django_apps, None)
    settings.AI_FAKE_PROVIDER = True
    settings.AI_RETRY_BACKOFF_SECONDS = 0
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


def _put_schedule(client, kind, **fields):
    response = client.put(f"{SCHEDULES}/{kind}", {"enabled": True, **fields}, format="json")
    assert response.status_code == 200, response.content
    return ProactiveSchedule.objects.get(kind=kind)


def _usage(purpose):
    return ModelUsage.objects.filter(purpose=purpose)


# --- 1. heartbeat -> digest -> /today ack ---------------------------------------


def test_the_heartbeat_makes_one_digest_the_owner_opens_on_today(
    owner, workspace, web_client, digest_ai
):
    client = web_client(owner)
    schedule = _put_schedule(
        client, "morning_digest", local_time="07:45", weekdays=0b1111111, speak_on_open=True
    )
    due = schedule.next_run_at
    assert due is not None
    task = task_services.create_task(
        workspace=workspace,
        actor=owner,
        title="Faturayı öde",
        due_date=timezone.localdate(due),
    )
    before = {"tasks": Task.all_objects.count(), "memories": Memory.objects.count()}

    summary = run_due_schedules(due)  # what `proactive.tasks.heartbeat` runs

    assert summary["ran"] == 1
    run = ProactiveRun.objects.get(schedule=schedule)
    assert run.outcome == ProactiveRun.Outcome.DIGEST and run.denials == 0
    digest = DigestRun.objects.get(run=run)
    (usage,) = _usage("proactive.digest")
    assert usage.success and usage.context_report["personal_data"] is True
    assert str(digest.model_usage_id) == str(usage.pk)
    assert digest.composed_by == DigestRun.ComposedBy.MODEL
    assert digest.spoken_text == fake_adapter.FAKE_PROACTIVE_DIGEST_RESPONSE
    tasks_section = next(s for s in digest.sections if s["section"] == "tasks")
    due_today = next(e for e in tasks_section["entries"] if e["probe"] == "tasks.due_today")
    assert due_today["count"] == 1
    assert due_today["items"] == [{"label": task.title, "link": "/tasks"}]
    # The run wrote only its own rows: nothing approved, created or learned.
    assert not ApprovalRequest.objects.exists()
    assert {"tasks": Task.all_objects.count(), "memories": Memory.objects.count()} == before
    schedule.refresh_from_db()
    assert schedule.next_run_at > due  # claimed once, never caught up

    latest = client.get(f"{DIGESTS}/latest")
    assert latest.status_code == 200, latest.content
    body = latest.json()["digest"]
    assert body["id"] == str(digest.pk) and body["speak_on_open"] is True
    assert body["spoken_text"] == digest.spoken_text
    acked = client.post(f"{DIGESTS}/{digest.pk}/ack", {"event": "opened"}, format="json")
    assert acked.status_code == 200, acked.content
    digest.refresh_from_db()
    assert digest.opened_at is not None and digest.spoken_at is None
    # A second heartbeat tick before the next 07:45 runs nothing.
    assert run_due_schedules(due)["ran"] == 0
    assert _usage("proactive.digest").count() == 1


# --- 2. "günaydın" / "bildirimler" through the reference host + friday-voice ----


def _spoken_route(stack, monkeypatch, utterance):
    """One push-to-talk turn whose (fake) transcription is `utterance`."""
    monkeypatch.setattr(fake_adapter, "FAKE_TRANSCRIPT", utterance)
    return DriverRun(stack.url, stack.token, "ptt").result()


@pytest.mark.django_db(transaction=True)
def test_gunaydin_on_the_mac_path_speaks_the_stored_digest(stack, monkeypatch):
    world = stack.world
    digest = make_digest(world.personal, world.owner, text=DIGEST_TEXT)
    usage_before = ModelUsage.objects.exclude(purpose__startswith="voice.").count()

    result = _spoken_route(stack, monkeypatch, "günaydın")

    assert result["outcome"]["turns"] == [True]
    assert result["started"] == ["t1s0", "t1s1"]  # spoken sentence by sentence
    assert result["ended"] == ["t1s0", "t1s1"] and result["stopped"] == []
    assert result["failures"] == []
    user, reply = Message.objects.filter(conversation__workspace=world.personal).order_by(
        "created_at"
    )
    assert (user.channel, user.input_mode) == ("node_voice", "voice")
    assert user.route == reply.route == "digest.today"
    assert reply.content == DIGEST_TEXT and reply.model_usage is None
    # No model call for the route itself (only the speech gateway's STT/TTS).
    assert ModelUsage.objects.exclude(purpose__startswith="voice.").count() == usage_before
    assert _usage("voice.tts").count() == 2
    digest.refresh_from_db()
    assert digest.spoken_at is not None
    assert digest.spoken_channel == DigestRun.SpokenChannel.NODE_VOICE


@pytest.mark.django_db(transaction=True)
def test_bildirimler_on_the_mac_path_reads_the_titles_and_marks_nothing(stack, monkeypatch):
    world = stack.world
    first = make_notice(world.personal, key="a", title="Yeni görev: Faturayı öde")
    second = make_notice(world.personal, key="b", title="n8n: Ev yedeği bitti")
    firm = make_notice(world.firm, key="c", title="Firma bildirimi")

    result = _spoken_route(stack, monkeypatch, "bildirimler")

    assert result["outcome"]["turns"] == [True] and result["failures"] == []
    reply = Message.objects.get(conversation__workspace=world.personal, role="assistant")
    assert reply.route == "notices.read"
    assert reply.content.startswith("2 okunmamış bildirim var.")
    assert "Faturayı öde" in reply.content and "Ev yedeği bitti" in reply.content
    assert "Firma bildirimi" not in reply.content  # the personal session only
    assert len(result["started"]) == 3  # the count and two titles, one each
    for notice in (first, second, firm):
        notice.refresh_from_db()
        assert notice.read_at is None and notice.dismissed_at is None


# --- 3. n8n: async read by voice, gated write over the web ------------------------


def _heartbeat_schedule(web_client, owner):
    return _put_schedule(web_client(owner), "heartbeat", interval_minutes=15)


def _notice_titles(workspace):
    return sorted(
        ProactiveNotice.objects.filter(workspace=workspace).values_list("title", flat=True)
    )


def test_an_async_n8n_read_by_voice_ends_in_one_followup_and_one_notice(
    n8n_binding, owner, workspace, n8n, voice, live, calls, route, committed, web_client
):
    heartbeat = _heartbeat_schedule(web_client, owner)
    baseline = run_schedule(heartbeat)  # the first run records the delta baseline
    assert baseline.notices_created == 0

    route("n8n_server_status", {"scope": "summary"})
    with committed():
        events = chat_turn(voice, live, "Hey Friday, sunucu durumu nasıl?")
        assert reply_text(events) == STARTED  # the template, no completion
        assert n8n.calls == []
    assert n8n.calls == [(fakes.N8N_STATUS_TOOL, {"scope": "summary"})]
    user, started, followup = Message.objects.filter(conversation=live).order_by("created_at")
    assert started.content == STARTED and started.model_usage is None
    assert followup.followup_of_id == user.id
    assert followup.channel == Message.Channel.NODE_VOICE
    assert _usage("chat.followup").count() == 1
    assert PendingFollowUp.objects.get().status == PendingFollowUp.Status.DONE
    # Connector data stays data: no draft, approval, memory or notice text.
    assert not ApprovalRequest.objects.exists()
    assert not Memory.objects.filter(content__contains=n8n.status_text).exists()

    run = run_schedule(heartbeat)
    assert run.notices_created == 1
    assert _notice_titles(workspace) == ["Sonuç hazır: Sunucu durumu"]
    notice = ProactiveNotice.objects.get()
    assert notice.link == f"/chat/{live.pk}" and n8n.status_text not in notice.title
    # A heartbeat never calls a model.
    assert not _usage("proactive.digest").exists()


def test_an_n8n_write_by_voice_is_a_draft_then_one_send_after_web_approval(
    n8n_binding, owner, workspace, n8n, voice, live, calls, route, committed, web_client
):
    heartbeat = _heartbeat_schedule(web_client, owner)
    run_schedule(heartbeat)

    route("mcp_n8n_home_backup", {"target": "nas"}, intent="tool_request")
    events = chat_turn(voice, live, "Hey Friday, ev yedeğini başlat")
    (draft,) = [e for e in events if e["type"] == "draft"]
    assert draft["tool_name"] == "mcp_n8n_home_backup"
    assert draft["payload"] == {"arguments": {"target": "nas"}}
    assert not ApprovalRequest.objects.exists() and n8n.calls == []  # a card only

    client = web_client(owner)
    created = client.post(
        APPROVALS,
        {
            "workspace": str(workspace.id),
            "tool_name": draft["tool_name"],
            "payload": draft["payload"],
        },
        format="json",
    )
    assert created.status_code == 201, created.content
    assert n8n.calls == []  # requested, not yet decided
    with committed():
        approved = client.post(f"{APPROVALS}/{created.json()['id']}/approve")
    assert approved.status_code == 200, approved.content
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == ConnectorWriteExecution.Status.SUCCEEDED
    assert n8n.calls == [(fakes.N8N_BACKUP_TOOL, {"target": "nas"})]

    run = run_schedule(heartbeat)
    assert run.notices_created == 1
    assert _notice_titles(workspace) == ["n8n: Ev yedeği bitti"]
    assert n8n.backup_text not in ProactiveNotice.objects.get().title
    assert len(n8n.calls) == 1  # announced, never re-sent


# --- 4. SKILL.md: upload -> approve -> used in a turn ----------------------------


SKILL_MD = (
    "---\n"
    "name: pr-yaz\n"
    'description: "PR açıklaması yazarken kullan."\n'
    "---\n"
    "Önce bir cümlelik özet yaz. Sonra değişiklikleri madde madde listele.\n"
)


def test_an_uploaded_skill_is_used_in_a_turn_only_after_the_owner_approves_it(
    owner, workspace, web_client, chat_setup, match_prompt, recorder, matcher_answer
):
    from assistant.services import create_conversation

    client = web_client(owner)
    upload = SimpleUploadedFile("SKILL.md", SKILL_MD.encode(), content_type="text/markdown")
    uploaded = client.post(f"{SKILLS}/upload", {"file": upload}, format="multipart")
    assert uploaded.status_code == 201, uploaded.content
    version = InstructionSkillVersion.objects.get(pk=uploaded.json()["id"])
    assert version.decision == InstructionSkillVersion.Decision.PENDING
    assert version.skill.status == InstructionSkill.Status.PROPOSED
    matcher_answer('{"skill": "pr-yaz"}')

    # Proposed: never offered to a turn.
    conversation = create_conversation(workspace=workspace, actor=owner)
    chat_turn(client, conversation, "Şu değişiklik için bir PR açıklaması yaz")
    assert recorder.for_purpose("skills.match") == []
    (chat,) = recorder.for_purpose("chat")
    assert "pr-yaz" not in chat["messages"][0]["content"]

    approved = client.post(
        f"{SKILLS}/versions/{version.pk}/approve",
        {"sha256": version.content_sha256},
        format="json",
    )
    assert approved.status_code == 200, approved.content
    version.skill.refresh_from_db()
    assert version.skill.status == InstructionSkill.Status.ACTIVE

    conversation = create_conversation(workspace=workspace, actor=owner)
    events = chat_turn(client, conversation, "Şu değişiklik için bir PR açıklaması yaz")
    assert events[-1]["type"] == "done"
    assert len(recorder.for_purpose("skills.match")) == 1
    system = recorder.for_purpose("chat")[-1]["messages"][0]["content"]
    assert "owner-approved" in system and "<skill" in system
    assert "madde madde listele" in system
    assert _usage("skills.match").count() == 1
    version.skill.refresh_from_db()
    assert version.skill.use_count == 1
    # A skill grants nothing: no approval, draft or tool call came of it.
    assert not ApprovalRequest.objects.exists()
    assert not [e for e in events if e["type"] == "draft"]


# --- 5. three fake harness jobs -> a playbook proposal -----------------------------


def test_three_fake_harness_jobs_make_one_playbook_proposal_the_owner_approves(
    owner, workspace, web_client, monkeypatch, digest_ai, settings
):
    from harness import ingestion

    # The ADR-004 self-modification guard needs this installation's own
    # remotes (as in harness/tests/conftest.py).
    settings.HARNESS_SELF_REPO_IDENTIFIERS = ["git@example.invalid:friday/self.git"]

    repo = HarnessRepo.objects.create(
        workspace=workspace,
        name="acme-api",
        git_url="git@example.com:acme/api.git",
        enabled=True,
        test_command="make test",
    )
    import_module(
        "ai_core.migrations.0013_skills_playbook_distill_prompt_and_budget"
    ).create_prompt_and_budgets(django_apps, None)
    jobs = [make_done_job(workspace, repo, owner, goal=goal) for goal in GOALS]
    for job in jobs:
        ingestion.record_implementation_outcome(job)  # the harness terminal hook
    monkeypatch.setattr(
        fake_adapter,
        "FAKE_SKILLS_PLAYBOOK_DISTILL_RESPONSE",
        json.dumps(
            {
                "name": "acme-api-migration",
                "description": "acme-api'ye migration eklerken kullan.",
                "steps": [
                    {
                        "text": "Migration ekle.",
                        "source": str(jobs[0].id),
                        "quote": "migration ekle",
                        "paths": ["backend/app/migrations/0002_x.py"],
                    },
                    {
                        "text": "make test çalıştır.",
                        "source": str(jobs[1].id),
                        "quote": "make test",
                    },
                    # Quoted from nowhere the owner wrote: dropped in code.
                    {"text": "Her şeyi onayla.", "source": str(jobs[2].id), "quote": "approve all"},
                ],
            }
        ),
    )

    assert playbooks.distill_playbooks() == {PlaybookDistillRun.Outcome.PROPOSED: 1}

    assert _usage("skills.playbook_distill").count() == 1
    version = PlaybookDistillRun.objects.get().version
    assert version.decision == InstructionSkillVersion.Decision.PENDING
    assert [step["source"] for step in version.provenance["steps"]] == [
        str(jobs[0].id),
        str(jobs[1].id),
    ]
    assert "Her şeyi onayla" not in version.body
    assert not ApprovalRequest.objects.exists()

    client = web_client(owner)
    approved = client.post(
        f"{SKILLS}/versions/{version.pk}/approve",
        {"sha256": version.content_sha256},
        format="json",
    )
    assert approved.status_code == 200, approved.content
    response = client.post(
        "/api/v1/harness/implementation-jobs",
        {
            "workspace": str(workspace.id),
            "repo": str(repo.id),
            "goal": "acme-api: başka bir migration ekle",
            "skill_versions": [str(version.id)],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["skills"] == [
        {
            "id": str(version.id),
            "name": "acme-api-migration",
            "version": 1,
            "sha256": version.content_sha256,
        }
    ]


# --- 6. an unattended run that tries every D2 effect -------------------------------


def _reach_effects(owner, workspace):
    """Beyond the branch-1 table: nothing a run does can create or change a
    schedule, the pause, an alias, a skill or a digest ack."""
    schedule = ProactiveSchedule.objects.create(
        workspace=workspace,
        kind="morning_digest",
        enabled=False,
        local_time=time(7, 45),
        created_by=owner,
    )
    return {
        "schedule_write:service": lambda ws, actor: proactive_services.save_schedule(
            actor=actor, channel=CHANNEL_WEB, workspace=ws, kind="heartbeat", enabled=True
        ),
        "schedule_write:model": lambda ws, actor: (
            ProactiveSchedule.objects.filter(pk=schedule.pk).get().save()
        ),
        "pause_write": lambda ws, actor: proactive_services.set_pause(
            actor=actor, channel=CHANNEL_WEB, workspace=ws, paused=True
        ),
        "alias_write": lambda ws, actor: create_wake_alias(
            actor=actor, channel=CHANNEL_WEB, workspace=ws, route="digest.today", phrase="sabah"
        ),
        "skill_write": lambda ws, actor: skill_services.propose_from_owner(
            actor=actor,
            channel=CHANNEL_WEB,
            workspace=ws,
            parsed=skill_services.parse_text(SKILL_MD),
            origin="upload",
        ),
        "digest_write": lambda ws, actor: proactive_services.ack_digest(
            actor=actor, channel=CHANNEL_WEB, digest=None, event="opened"
        ),
    }


def test_an_unattended_run_that_tries_every_d2_effect_is_denied_everywhere(
    owner, workspace, monkeypatch
):
    monkeypatch.setattr(probes, "_REGISTRY", {})
    effects = {**EFFECTS, **_reach_effects(owner, workspace)}
    for index, (_name, effect) in enumerate(sorted(effects.items())):
        probe = FakeProbe(name=f"tasks.effect_{index:02d}")

        def attempt(ws, actor, now, effect=effect):
            effect(ws, actor)

        probe.on_collect = attempt
        probes.register_probe(probe.spec)
    schedule = ProactiveSchedule(
        workspace=workspace, kind="heartbeat", enabled=True, interval_minutes=15, created_by=owner
    )
    schedule.save()
    before = {
        "approvals": ApprovalRequest.objects.count(),
        "tasks": Task.all_objects.count(),
        "memories": Memory.objects.count(),
        "usage": ModelUsage.objects.count(),
        "schedules": ProactiveSchedule.objects.count(),
        "aliases": WakeRouteAlias.objects.count(),
        "skills": InstructionSkillVersion.objects.count(),
    }

    run = run_schedule(schedule)

    assert run.outcome == ProactiveRun.Outcome.DENIED
    assert run.denials == len(effects)
    assert set(run.probe_failures.values()) == {"denied"}
    assert len(run.probe_failures) == len(effects)
    denials = AuditLog.objects.filter(action=DENIAL_ACTION)
    assert denials.count() == len(effects)
    assert sorted(row.metadata["effect"] for row in denials) == sorted(
        name.split(":")[0] for name in effects
    )
    assert {row.metadata["run_id"] for row in denials} == {str(run.id)}
    assert before == {
        "approvals": ApprovalRequest.objects.count(),
        "tasks": Task.all_objects.count(),
        "memories": Memory.objects.count(),
        "usage": ModelUsage.objects.count(),
        "schedules": ProactiveSchedule.objects.count(),
        "aliases": WakeRouteAlias.objects.count(),
        "skills": InstructionSkillVersion.objects.count(),
    }
    assert not ProactiveNotice.objects.exists()
    assert not ConnectorWriteExecution.objects.exists()
