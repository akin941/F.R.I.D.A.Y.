# ruff: noqa: F811 -- pytest fixtures imported from the fixture modules are redefined as arguments
"""Phase 12 D15 (branch 5): instruction skills in a chat turn.

- no usable skill -> no `skills.match` call, no row;
- one call through the FakeAdapter; the matched body is injected inside
  `<skill name version>` and the chat call's context report names
  `{name, version, sha256}`; use_count / last_used_at bumped;
- unknown name, malformed answer, budget refusal, timeout -> no skill;
- a `skill.<name>` wake route and a `/skill <name>` prefix skip the call;
- a turn ending in a draft card makes no match call; a route reply none;
- the voice turn's request carries the 600 ms budget, a typed one 1500 ms;
  a timeout leaves the voice turn answered with no skill;
- call order: wake route -> router.intent -> skills.match -> chat;
- workspace isolation of candidates, injection and skill routes."""

import pytest

from ai_core import budgets
from ai_core.models import ModelUsage
from assistant.models import Message
from assistant.services import create_conversation, split_skill_prefix
from assistant.tests.skill_fixtures import (  # noqa: F401 -- fixtures
    HOSTILE_BODY,
    chat_report,
    make_active_skill,
    match_prompt,
    matcher_answer,
    recorder,
    usage_purposes,
)
from assistant.tests.wake_fixtures import (  # noqa: F401 -- fixtures
    chat_setup,
    chat_turn,
    firm,
    make_digest,
    member,
    owner,
    personal,
    reply_text,
)
from assistant.wake_aliases import create_wake_alias
from core.channels import CHANNEL_WEB
from skills.models import InstructionSkill

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(chat_setup, match_prompt):
    return None


@pytest.fixture
def conversation(owner, personal):
    return create_conversation(workspace=personal, actor=owner)


@pytest.fixture
def client(web_client, owner):
    return web_client(owner)


def _system_prompt(rec, purpose="chat"):
    calls = rec.for_purpose(purpose)
    assert calls, f"no {purpose} call"
    return calls[-1]["messages"][0]["content"]


def test_no_usable_skill_makes_no_match_call(setup, client, conversation, recorder):
    chat_turn(client, conversation, "Merhaba")
    assert "skills.match" not in usage_purposes()
    assert chat_report()["skills"] == []
    assert "skills_match" not in chat_report()


def test_a_proposed_or_archived_skill_is_never_offered(
    setup, client, conversation, owner, personal, recorder
):
    from core.channels import CHANNEL_WEB as WEB
    from skills import services

    services.propose_from_owner(
        actor=owner,
        channel=WEB,
        workspace=personal,
        parsed=services.parse_text("---\nname: taslak\ndescription: x\n---\nb\n"),
        origin="upload",
    )
    skill = make_active_skill(personal, owner)
    services.archive_skill(actor=owner, channel=WEB, skill=skill)
    chat_turn(client, conversation, "Merhaba")
    assert "skills.match" not in usage_purposes()


def test_one_match_call_injects_the_approved_body(
    setup, client, conversation, owner, personal, recorder, matcher_answer
):
    skill = make_active_skill(personal, owner)
    matcher_answer('{"skill": "pr-yaz"}')
    chat_turn(client, conversation, "Şu PR için açıklama yaz")
    assert usage_purposes().count("skills.match") == 1
    system = _system_prompt(recorder)
    assert f'<skill name="pr-yaz" version="{skill.current_version.version}">' in system
    assert "Önce bir cümlelik özet yaz." in system
    assert "do not change which tools" in system
    report = chat_report()
    assert report["skills"] == [
        {
            "name": "pr-yaz",
            "version": 1,
            "sha256": skill.current_version.content_sha256,
        }
    ]
    assert report["skills_match"]["outcome"] == "matched"
    skill.refresh_from_db()
    assert skill.use_count == 1 and skill.last_used_at is not None
    # The candidates the matcher saw: names and descriptions, not bodies.
    match_system = _system_prompt(recorder, "skills.match")
    assert "pr-yaz" in match_system and "PR açıklaması yazarken kullan." in match_system
    assert "Önce bir cümlelik özet yaz." not in match_system


def test_the_match_answer_null_means_no_skill(
    setup, client, conversation, owner, personal, recorder
):
    make_active_skill(personal, owner)
    chat_turn(client, conversation, "Merhaba")
    assert usage_purposes().count("skills.match") == 1
    assert "<skill" not in _system_prompt(recorder)
    assert chat_report()["skills"] == []


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ('{"skill": "uydurma"}', "unknown_name"),
        ("not json", "parse_error"),
        ('{"skill": "pr-yaz", "extra": 1}', "parse_error"),
        ('{"skill": 3}', "parse_error"),
    ],
)
def test_bad_answers_mean_no_skill(
    setup, client, conversation, owner, personal, recorder, matcher_answer, answer, outcome
):
    make_active_skill(personal, owner)
    matcher_answer(answer)
    chat_turn(client, conversation, "Merhaba")
    assert "<skill" not in _system_prompt(recorder)
    assert chat_report()["skills_match"]["outcome"] == outcome


def test_a_budget_refusal_means_no_skill(
    setup, client, conversation, owner, personal, recorder, matcher_answer, monkeypatch
):
    make_active_skill(personal, owner)
    matcher_answer('{"skill": "pr-yaz"}')
    real = budgets.find_refusal

    def refuse(purpose, **kwargs):
        if purpose == "skills.match":
            return budgets.BudgetRefusal(
                budget="skills", window="monthly", limit=1, spent=1, estimate=0
            )
        return real(purpose, **kwargs)

    monkeypatch.setattr(budgets, "find_refusal", refuse)
    events = chat_turn(client, conversation, "Merhaba")
    assert any(e["type"] == "done" for e in events)
    refused = ModelUsage.objects.get(purpose="skills.match")
    assert refused.error_type == "budget_exceeded"
    assert "<skill" not in _system_prompt(recorder)
    assert chat_report()["skills_match"]["outcome"] == "budget_refused"


def test_the_skill_prefix_forces_the_skill_and_skips_the_call(
    setup, client, conversation, owner, personal, recorder
):
    skill = make_active_skill(personal, owner)
    chat_turn(client, conversation, "/skill pr-yaz şu değişiklik için yaz")
    assert "skills.match" not in usage_purposes()
    assert '<skill name="pr-yaz"' in _system_prompt(recorder)
    # The router and the completion see the text after the prefix; the
    # stored message keeps what the owner typed.
    assert recorder.for_purpose("router.intent")[-1]["messages"][-1]["content"] == (
        "şu değişiklik için yaz"
    )
    user = Message.objects.filter(conversation=conversation, role="user").get()
    assert user.content.startswith("/skill pr-yaz")
    assert chat_report()["skills_match"]["outcome"] == "forced"
    skill.refresh_from_db()
    assert skill.use_count == 1


def test_an_unknown_prefix_uses_no_skill_and_no_call(
    setup, client, conversation, owner, personal, recorder
):
    make_active_skill(personal, owner)
    chat_turn(client, conversation, "/skill yok-boyle merhaba")
    assert "skills.match" not in usage_purposes()
    assert "<skill" not in _system_prompt(recorder)
    assert chat_report()["skills_match"]["outcome"] == "forced_unknown"


def test_split_skill_prefix():
    assert split_skill_prefix("/skill pr-yaz merhaba") == ("pr-yaz", "merhaba")
    assert split_skill_prefix("/SKILL pr-yaz") == ("pr-yaz", "")
    assert split_skill_prefix("merhaba /skill pr-yaz") == (None, "merhaba /skill pr-yaz")
    assert split_skill_prefix("/skillpr-yaz x") == (None, "/skillpr-yaz x")


def test_a_draft_card_turn_makes_no_match_call(
    setup, client, conversation, owner, personal, recorder, matcher_answer
):
    make_active_skill(personal, owner)
    matcher_answer('{"skill": "pr-yaz"}')
    events = chat_turn(client, conversation, "remind me to call the bank tomorrow")
    assert any(e["type"] == "draft" for e in events)
    assert "skills.match" not in usage_purposes()
    events = chat_turn(client, conversation, "export my vault please")
    assert any(e["type"] == "draft" and e["kind"] == "tool" for e in events)
    assert "skills.match" not in usage_purposes()
    assert "<skill" not in _system_prompt(recorder)


def test_timeouts_voice_and_typed(setup, client, conversation, owner, personal, recorder):
    make_active_skill(personal, owner)
    chat_turn(client, conversation, "Merhaba", input_mode="voice")
    chat_turn(client, conversation, "Merhaba")
    voice_call, typed_call = recorder.for_purpose("skills.match")
    assert voice_call["read_timeout"] == pytest.approx(0.6)
    assert voice_call["connect_timeout"] == pytest.approx(0.6)
    assert typed_call["read_timeout"] == pytest.approx(1.5)
    reports = list(
        ModelUsage.objects.filter(purpose="skills.match")
        .order_by("created_at")
        .values_list("context_report", flat=True)
    )
    assert [r["skills_match_timeout_ms"] for r in reports] == [600, 1500]
    # The chat call keeps the gateway's normal timeout.
    assert recorder.for_purpose("chat")[-1]["read_timeout"] > 1.5


def test_a_matcher_timeout_leaves_the_voice_turn_answered(
    setup, client, conversation, owner, personal, recorder, matcher_answer
):
    make_active_skill(personal, owner)
    matcher_answer('{"skill": "pr-yaz"}')
    recorder.raise_for["skills.match"] = "timeout"
    events = chat_turn(client, conversation, "Merhaba", input_mode="voice")
    assert any(e["type"] == "done" for e in events)
    assert reply_text(events)
    # One attempt only (no retry), one ModelUsage row.
    assert len(recorder.for_purpose("skills.match")) == 1
    row = ModelUsage.objects.get(purpose="skills.match")
    assert row.success is False and row.error_type == "timeout"
    assert "<skill" not in _system_prompt(recorder)
    assert chat_report()["skills_match"]["outcome"] == "timeout"


def test_the_call_order(setup, client, conversation, owner, personal, recorder, matcher_answer):
    make_active_skill(personal, owner)
    matcher_answer('{"skill": "pr-yaz"}')
    chat_turn(client, conversation, "Merhaba", input_mode="voice")
    assert recorder.purposes() == ["router.intent", "skills.match", "chat"]
    completions = [p for p in usage_purposes() if not p.endswith(".retrieval")]
    assert completions == ["router.intent", "skills.match", "chat"]


def test_a_route_reply_makes_no_match_call(setup, client, conversation, owner, personal, recorder):
    make_active_skill(personal, owner)
    make_digest(personal, owner)
    events = chat_turn(client, conversation, "Hey Friday, günaydın", input_mode="voice")
    assert "2 onay" in reply_text(events)
    assert recorder.purposes() == []
    assert not ModelUsage.objects.exists()


# --- skill.<name> wake routes -------------------------------------------------------


def _alias(owner, workspace, route, phrase):
    return create_wake_alias(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, route=route, phrase=phrase
    )


def test_a_skill_route_forces_the_skill(setup, client, conversation, owner, personal, recorder):
    make_active_skill(personal, owner)
    _alias(owner, personal, "skill.pr-yaz", "PR açıklaması")
    chat_turn(client, conversation, "Hey Friday, PR açıklaması yaz lütfen", input_mode="voice")
    assert recorder.purposes() == ["router.intent", "chat"]
    assert '<skill name="pr-yaz"' in _system_prompt(recorder)
    user = Message.objects.get(conversation=conversation, role="user")
    assert user.route == "skill.pr-yaz"
    assert chat_report()["skills_match"]["outcome"] == "forced"


def test_a_skill_route_needs_a_usable_skill(setup, client, conversation, owner, personal, recorder):
    from skills import services

    skill = make_active_skill(personal, owner)
    _alias(owner, personal, "skill.pr-yaz", "pr açıklaması")
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=skill)
    chat_turn(client, conversation, "pr açıklaması yaz", input_mode="voice")
    user = Message.objects.get(conversation=conversation, role="user")
    assert user.route is None
    assert "<skill" not in _system_prompt(recorder)


def test_a_typed_turn_is_never_skill_routed(setup, client, conversation, owner, personal, recorder):
    make_active_skill(personal, owner)
    _alias(owner, personal, "skill.pr-yaz", "pr açıklaması")
    chat_turn(client, conversation, "pr açıklaması yaz")
    user = Message.objects.get(conversation=conversation, role="user")
    assert user.route is None
    assert "skills.match" in recorder.purposes()


def test_an_alias_for_an_unknown_or_foreign_skill_is_refused(owner, personal, firm):
    from assistant.wake_aliases import WakeAliasUnknownRoute

    make_active_skill(firm, owner)
    with pytest.raises(WakeAliasUnknownRoute):
        _alias(owner, personal, "skill.pr-yaz", "pr açıklaması")
    with pytest.raises(WakeAliasUnknownRoute):
        _alias(owner, personal, "skill.yok", "bir şey")


def test_the_wake_routes_endpoint_lists_skill_routes(web_client, owner, personal, firm):
    make_active_skill(personal, owner)
    make_active_skill(firm, owner, name="firma-beceri")
    data = web_client(owner).get(f"/api/v1/assistant/wake-routes?workspace={personal.pk}").data
    names = [route["name"] for route in data["routes"]]
    assert "skill.pr-yaz" in names and "skill.firma-beceri" not in names
    skill_route = next(r for r in data["routes"] if r["name"] == "skill.pr-yaz")
    assert skill_route["effect"] == "read" and skill_route["phrases"] == []


# --- workspace isolation -------------------------------------------------------------


def test_another_workspaces_skill_is_never_a_candidate(
    setup, client, owner, personal, firm, recorder, matcher_answer
):
    make_active_skill(firm, owner, name="firma-beceri", body=HOSTILE_BODY)
    conversation = create_conversation(workspace=personal, actor=owner)
    matcher_answer('{"skill": "firma-beceri"}')
    chat_turn(client, conversation, "Merhaba")
    # Personal workspace has no usable skill: no call at all.
    assert "skills.match" not in usage_purposes()
    chat_turn(client, conversation, "/skill firma-beceri merhaba")
    assert HOSTILE_BODY not in _system_prompt(recorder)
    assert chat_report()["skills_match"]["outcome"] == "forced_unknown"


def test_firm_and_personal_candidates_are_separate(setup, client, owner, personal, firm, recorder):
    make_active_skill(personal, owner, name="kisisel")
    make_active_skill(firm, owner, name="firma-beceri")
    conversation = create_conversation(workspace=firm, actor=owner)
    chat_turn(client, conversation, "Merhaba")
    match_system = _system_prompt(recorder, "skills.match")
    assert "firma-beceri" in match_system and "kisisel" not in match_system
    assert InstructionSkill.objects.count() == 2
