"""Phase 12 D4/D6 (branch 2): the morning digest's Speak step -- the
script gate, one `proactive.digest` call with FakeAdapter, the template
fallbacks (no prompt, no personal-data provider, budget refusal, failed
number check), Markdown/invisible stripping, the length bound, calendar
third-party titles, workspace isolation, and the real heartbeat."""

from datetime import timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from ai_core.adapters import fake as fake_adapter
from ai_core.models import AIBudget, ModelProvider, ModelUsage, PromptTemplate
from core import probes
from proactive import digest as digest_step
from proactive import templates
from proactive.models import DigestRun, ProactiveNotice, ProactiveRun
from proactive.runs import run_schedule
from proactive.tasks import run_due_schedules
from proactive.tests.conftest import TUESDAY_10, FakeProbe, make_schedule
from proactive.tests.test_probes_owner_apps import BOOT_PROBES, HOSTILE, calendar_event
from tasks.models import Task

pytestmark = pytest.mark.django_db

PURPOSE = "proactive.digest"


@pytest.fixture(autouse=True)
def seeded_prompt_and_budget(db):
    """What the ai_core 0009 data migration creates (a transactional test
    elsewhere may have flushed it from a reused test database)."""
    migration = import_module("ai_core.migrations.0009_proactive_digest_prompt_and_budget")
    migration.create_prompt_and_budget(django_apps, None)


@pytest.fixture
def provider(settings):
    settings.AI_FAKE_PROVIDER = True
    return ModelProvider.objects.create(
        name="fake-local",
        provider_type=ModelProvider.ProviderType.FAKE,
        model_id="fake-1",
        priority=0,
        personal_data_allowed=True,
    )


@pytest.fixture
def scripted(monkeypatch):
    """Script the FakeAdapter's digest answer and capture the messages it
    was sent."""
    state = {"text": fake_adapter.FAKE_PROACTIVE_DIGEST_RESPONSE, "messages": []}
    original = fake_adapter.FakeAdapter._text_for

    def text_for(self, purpose, messages=None):
        if purpose == PURPOSE:
            state["messages"].append(messages)
            return state["text"]
        return original(self, purpose, messages)

    monkeypatch.setattr(fake_adapter.FakeAdapter, "_text_for", text_for)
    return state


@pytest.fixture
def approvals_probe(isolated_probes):
    probe = FakeProbe(name="approvals.fake_pending", section="approvals", link="/approvals")
    probes.register_probe(probe.spec)
    return probe


@pytest.fixture
def digest_schedule(owner, workspace):
    return make_schedule(workspace, owner, kind="morning_digest")


def _usage_count():
    return ModelUsage.objects.filter(purpose=PURPOSE).count()


def test_no_probe_content_means_no_call_and_no_digest(
    digest_schedule, approvals_probe, provider, scripted
):
    run = run_schedule(digest_schedule, now=TUESDAY_10)
    assert run.outcome == ProactiveRun.Outcome.QUIET
    assert _usage_count() == 0
    assert not DigestRun.objects.exists()
    assert scripted["messages"] == []


def test_content_makes_exactly_one_personal_data_call(
    workspace, digest_schedule, approvals_probe, provider, scripted
):
    approvals_probe.items[workspace.pk] = [("a1", "Onay: vault_export"), ("a2", "Onay: other")]
    run = run_schedule(digest_schedule, now=TUESDAY_10)

    assert run.outcome == ProactiveRun.Outcome.DIGEST
    usage = ModelUsage.objects.get(purpose=PURPOSE)
    assert usage.success
    assert usage.context_report["personal_data"] is True
    assert usage.workspace_id == workspace.pk
    assert usage.prompt_version.template.name == digest_step.PROMPT_NAME
    digest = DigestRun.objects.get()
    assert digest.run_id == run.id
    assert digest.composed_by == DigestRun.ComposedBy.MODEL
    assert digest.spoken_text == fake_adapter.FAKE_PROACTIVE_DIGEST_RESPONSE
    assert str(digest.model_usage_id) == str(usage.pk)
    assert str(run.model_usage_id) == str(usage.pk)
    assert digest.data_class == "personal"
    assert digest.sections == [
        {
            "section": "approvals",
            "title": "Onaylar",
            "entries": [
                {
                    "probe": "approvals.fake_pending",
                    "name": "approvals.fake_pending",
                    "count": 2,
                    "link": "/approvals",
                    "items": [
                        {"label": "Onay: vault_export", "link": "/approvals"},
                        {"label": "Onay: other", "link": "/approvals"},
                    ],
                }
            ],
        }
    ]
    # The facts are fenced; the system prompt is the seeded one.
    (messages,) = scripted["messages"]
    assert messages[0]["role"] == "system"
    assert "<digest_facts>" in messages[1]["content"]
    assert "- approvals.fake_pending: 2" in messages[1]["content"]
    # Nothing but the run's own rows.
    assert not ProactiveNotice.objects.exists()


def test_no_personal_data_provider_means_template_and_no_call(
    workspace, digest_schedule, approvals_probe, provider, scripted
):
    ModelProvider.objects.filter(pk=provider.pk).update(personal_data_allowed=False)
    approvals_probe.items[workspace.pk] = [("a1", "Onay: vault_export")]
    run = run_schedule(digest_schedule, now=TUESDAY_10)

    digest = DigestRun.objects.get()
    assert digest.composed_by == DigestRun.ComposedBy.TEMPLATE
    assert digest.fallback_reason == DigestRun.FallbackReason.NO_PROVIDER
    assert _usage_count() == 0
    assert run.outcome == ProactiveRun.Outcome.DIGEST
    assert run.probe_failures["digest"] == "no_provider"
    assert digest.spoken_text.startswith(templates.OPENING)


def test_a_missing_prompt_means_template(
    workspace, digest_schedule, approvals_probe, provider, scripted
):
    PromptTemplate.objects.filter(name=digest_step.PROMPT_NAME).delete()
    approvals_probe.items[workspace.pk] = [("a1", "Onay: vault_export")]
    run_schedule(digest_schedule, now=TUESDAY_10)
    digest = DigestRun.objects.get()
    assert digest.fallback_reason == DigestRun.FallbackReason.NO_PROMPT
    assert _usage_count() == 0


def test_a_budget_refusal_means_template(
    workspace, digest_schedule, approvals_probe, provider, scripted, frozen_now
):
    frozen_now(TUESDAY_10)
    ModelProvider.objects.filter(pk=provider.pk).update(input_price_per_mtok=Decimal("1"))
    budget = AIBudget.objects.get(name="proactive")
    assert PURPOSE in budget.purposes
    ModelUsage.objects.create(
        purpose=PURPOSE, estimated_cost=budget.daily_limit_usd, success=True, workspace=workspace
    )
    approvals_probe.items[workspace.pk] = [("a1", "Onay: vault_export")]
    run = run_schedule(digest_schedule, now=TUESDAY_10)

    digest = DigestRun.objects.get()
    assert digest.composed_by == DigestRun.ComposedBy.TEMPLATE
    assert digest.fallback_reason == DigestRun.FallbackReason.BUDGET_EXCEEDED
    refused = ModelUsage.objects.get(purpose=PURPOSE, error_type="budget_exceeded")
    assert str(digest.model_usage_id) == str(refused.pk)
    assert run.outcome == ProactiveRun.Outcome.DIGEST
    assert scripted["messages"] == []


@pytest.mark.parametrize(
    ("answer", "model_kept"),
    [
        ("Günaydın! 5 onay seni bekliyor.", False),
        ("Günaydın! Beş onay seni bekliyor.", False),
        ("Good morning! Five approvals are waiting.", False),
        ("Günaydın! Yirmi beş onay seni bekliyor.", False),
        ("Günaydın! İki onay seni bekliyor.", True),
        ("Günaydın! 2 onay seni bekliyor.", True),
        ("Günaydın! Bugün on onay var.", False),
        ("Günaydın! Bir onay seni bekliyor.", True),  # the R2 residual (`bir`)
        ("Günaydın! Onayların seni bekliyor, onu unutma.", True),
    ],
)
def test_the_number_check_decides_between_model_and_template(
    workspace, digest_schedule, approvals_probe, provider, scripted, answer, model_kept
):
    scripted["text"] = answer
    approvals_probe.items[workspace.pk] = [("a1", "Onay: vault_export"), ("a2", "Onay: other")]
    run = run_schedule(digest_schedule, now=TUESDAY_10)

    digest = DigestRun.objects.get()
    assert _usage_count() == 1
    if model_kept:
        assert digest.composed_by == DigestRun.ComposedBy.MODEL
        assert digest.spoken_text == answer
        assert "digest" not in run.probe_failures
    else:
        assert digest.composed_by == DigestRun.ComposedBy.TEMPLATE
        assert digest.fallback_reason == DigestRun.FallbackReason.NUMBER_CHECK_FAILED
        assert run.probe_failures["digest"] == "number_check_failed"
        assert answer not in digest.spoken_text


def test_a_time_fact_allows_its_spoken_hour(
    owner, workspace, digest_schedule, provider, scripted, isolated_probes
):
    from memory.models import IngestionSource

    probes.register_probe(BOOT_PROBES["memory.calendar_today"])
    source = IngestionSource.objects.create(
        workspace=workspace, kind=IngestionSource.Kind.CALENDAR, enabled=True
    )
    calendar_event(
        source, workspace, at=TUESDAY_10 - timedelta(hours=1), title="Stand-up", organiser=True
    )
    scripted["text"] = "Günaydın! Saat dokuzda Stand-up var."
    run_schedule(digest_schedule, now=TUESDAY_10)
    assert DigestRun.objects.get().composed_by == DigestRun.ComposedBy.MODEL

    DigestRun.objects.all().delete()
    scripted["text"] = "Günaydın! Saat beşte Stand-up var."
    run_schedule(digest_schedule, now=TUESDAY_10)
    assert DigestRun.objects.get().fallback_reason == DigestRun.FallbackReason.NUMBER_CHECK_FAILED


def test_markdown_and_invisible_characters_are_stripped(
    workspace, digest_schedule, approvals_probe, provider, scripted
):
    scripted["text"] = (
        "## Günaydın\n\n- **İki** onay​ seni [bekliyor](https://evil.example).\n> Kolay gelsin"
    )
    approvals_probe.items[workspace.pk] = [("a1", "Onay: a"), ("a2", "Onay: b")]
    run_schedule(digest_schedule, now=TUESDAY_10)
    assert DigestRun.objects.get().spoken_text == "Günaydın İki onay seni bekliyor. Kolay gelsin"


def test_the_spoken_text_is_length_bounded(
    workspace, digest_schedule, approvals_probe, provider, scripted, settings
):
    settings.PROACTIVE_DIGEST_MAX_CHARS = 60
    scripted["text"] = "Günaydın. " + "Onaylar seni bekliyor. " * 20
    approvals_probe.items[workspace.pk] = [("a1", "Onay: a")]
    run_schedule(digest_schedule, now=TUESDAY_10)
    text = DigestRun.objects.get().spoken_text
    assert len(text) <= 60
    assert text.endswith(".")


def test_the_template_is_bounded_and_whole_sentences():
    sections = [
        {
            "section": "tasks",
            "title": "Görevler",
            "entries": [
                {
                    "probe": "tasks.due_today",
                    "name": "Bugün biten görev",
                    "count": 2,
                    "link": "/tasks",
                    "items": [
                        {"label": "Fatura", "link": "/tasks"},
                        {"label": "Rapor", "link": ""},
                    ],
                },
                {"probe": "x.unknown", "name": "x.unknown", "count": 4, "link": "", "items": []},
            ],
        }
    ]
    text = templates.render(sections, max_chars=900)
    assert text == (
        "Günaydın. 2 görevin bugün bitiyor: Fatura, Rapor. x.unknown: 4. "
        "Ayrıntılar Bugün sayfasında."
    )
    assert len(templates.render(sections, max_chars=40)) <= 40
    assert templates.render([], max_chars=900) == templates.EMPTY


def test_a_third_party_calendar_title_never_reaches_the_digest(
    owner, workspace, digest_schedule, provider, scripted, isolated_probes
):
    from memory.models import IngestionSource

    probes.register_probe(BOOT_PROBES["memory.calendar_today"])
    source = IngestionSource.objects.create(
        workspace=workspace, kind=IngestionSource.Kind.CALENDAR, enabled=True
    )
    calendar_event(source, workspace, at=TUESDAY_10, title=HOSTILE, organiser=False)
    calendar_event(source, workspace, at=TUESDAY_10, title="Sprint planı", organiser=True)

    run_schedule(digest_schedule, now=TUESDAY_10)
    digest = DigestRun.objects.get()
    (messages,) = scripted["messages"]
    prompt = "\n".join(message["content"] for message in messages)
    assert HOSTILE not in prompt
    assert "10:00 takvim öğesi" in prompt and "10:00 Sprint planı" in prompt
    assert HOSTILE not in str(digest.sections)
    assert HOSTILE not in digest.spoken_text

    # The template digest too.
    DigestRun.objects.all().delete()
    ModelProvider.objects.update(is_active=False)
    run_schedule(digest_schedule, now=TUESDAY_10)
    template_digest = DigestRun.objects.get()
    assert template_digest.composed_by == DigestRun.ComposedBy.TEMPLATE
    assert HOSTILE not in template_digest.spoken_text
    assert "takvim öğesi" in template_digest.spoken_text
    assert not ProactiveNotice.objects.filter(title__contains="ignore").exists()


def test_a_personal_digest_never_lists_firm_rows(
    owner, workspace, firm, digest_schedule, provider, scripted, isolated_probes
):
    probes.register_probe(BOOT_PROBES["tasks.due_today"])
    today = timezone.localdate(TUESDAY_10)
    Task.objects.create(workspace=workspace, title="Kişisel iş", due_date=today, created_by=owner)
    Task.objects.create(workspace=firm, title="Firma işi", due_date=today, created_by=owner)
    firm_schedule = make_schedule(firm, owner, kind="morning_digest")

    run_schedule(digest_schedule, now=TUESDAY_10)
    run_schedule(firm_schedule, now=TUESDAY_10)
    personal = DigestRun.objects.get(workspace=workspace)
    firm_digest = DigestRun.objects.get(workspace=firm)
    assert "Kişisel iş" in str(personal.sections) and "Firma işi" not in str(personal.sections)
    assert "Firma işi" in str(firm_digest.sections) and "Kişisel iş" not in str(
        firm_digest.sections
    )
    assert personal.data_class == "personal" and firm_digest.data_class == "firm"
    prompts = [m[1]["content"] for m in scripted["messages"]]
    assert sum("Firma işi" in p for p in prompts) == 1
    assert sum("Kişisel iş" in p for p in prompts) == 1


def test_the_real_heartbeat_produces_a_number_checked_digest_at_the_scheduled_minute(
    owner, workspace, provider, scripted, isolated_probes
):
    """The branch "Done when": seeded approvals, tasks, a calendar episode and
    learning proposals -> a digest at the scheduled minute through the real
    heartbeat (`run_due_schedules`)."""
    from approvals.models import ApprovalRequest
    from memory.models import IngestionSource

    for name in BOOT_PROBES:
        probes.register_probe(BOOT_PROBES[name])
    today = timezone.localdate(TUESDAY_10)
    ApprovalRequest.objects.create(
        workspace=workspace, tool_name="vault_export", requested_by=owner
    )
    Task.objects.create(workspace=workspace, title="Fatura", due_date=today, created_by=owner)
    source = IngestionSource.objects.create(
        workspace=workspace, kind=IngestionSource.Kind.CALENDAR, enabled=True
    )
    calendar_event(source, workspace, at=TUESDAY_10, title="Sprint", organiser=True)
    scripted["text"] = "Günaydın! 1 onay ve 1 görev seni bekliyor; saat 10:00'da Sprint var."

    schedule = make_schedule(workspace, owner, kind="morning_digest", next_run_at=TUESDAY_10)
    early = run_due_schedules(now=TUESDAY_10 - timedelta(minutes=1))
    assert early["ran"] == 0 and not DigestRun.objects.exists()
    summary = run_due_schedules(now=TUESDAY_10)
    assert summary["ran"] == 1
    digest = DigestRun.objects.get(schedule=schedule)
    assert digest.composed_by == DigestRun.ComposedBy.MODEL
    sections = [section["section"] for section in digest.sections]
    assert sections[:3] == ["approvals", "calendar", "tasks"]
    schedule.refresh_from_db()
    assert schedule.next_run_at > TUESDAY_10
