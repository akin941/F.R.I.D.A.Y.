"""Phase 8A branch 1: the `record_episode` port, workspace mapping, TTL,
forget/restore source, isolation and the seed command (brief D1-D4, D11)."""

import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from accounts.services import register_user
from audit.models import AuditLog
from core.channels import CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_SYSTEM, CHANNEL_WEB
from memory.ingestion import ports, services
from memory.ingestion.mapping import normalise_path, resolve_workspace
from memory.ingestion.ports import InvalidEpisode, record_episode
from memory.ingestion.tasks import purge_expired_episodes_task
from memory.models import (
    Episode,
    IngestionCounter,
    IngestionSource,
    ScrubTerm,
    WorkspaceMappingRule,
)
from memory.tests.ingestion_helpers import PASSWORD, WHEN, add_rule
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX
REPO = WorkspaceMappingRule.RuleType.REPO
CAL = WorkspaceMappingRule.RuleType.CALENDAR_BINDING
FAKE_KEY = "sk-" + "ant-api03-NEVERSTORED0123456789abcdefXYZ"


def _record(source, external_id="e1", mapping_key="path:/Users/o/code/friday", **kwargs):
    fields = {
        "source": source,
        "kind": "hook.notification",
        "external_id": external_id,
        "occurred_at": WHEN,
        "mapping_key": mapping_key,
    }
    fields.update(kwargs)
    return record_episode(**fields)


def _counter(source):
    return IngestionCounter.objects.get(source=source, day=timezone.localdate())


# --- seed --------------------------------------------------------------------


def test_seed_creates_four_disabled_sources_idempotently(owner, personal):
    call_command("seed_ingestion_sources")
    call_command("seed_ingestion_sources")
    rows = IngestionSource.objects.filter(workspace=personal)
    assert sorted(r.kind for r in rows) == sorted(IngestionSource.Kind.values)
    assert not any(r.enabled for r in rows)
    assert {r.daily_token_budget for r in rows} == {40000}
    assert AuditLog.objects.filter(action="memory.ingestion.source_created").count() == 4


def test_seed_never_changes_an_existing_source(sources):
    git = sources["git"]
    git.enabled = True
    git.save()
    call_command("seed_ingestion_sources")
    git.refresh_from_db()
    assert git.enabled is True


def test_seed_without_owner_fails(db):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("seed_ingestion_sources")


def test_sources_only_live_in_a_personal_workspace(owner, firm):
    with pytest.raises(services.IngestionInvalid):
        services.ensure_default_sources(workspace=firm, created_by=owner)


# --- mapping -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/Users/o/code/", "/Users/o/code"),
        ("//Users//o/./code/x/..", "/Users/o/code"),
        ("~/code/friday/", "~/code/friday"),
        ("~", "~"),
        ("~/", "~"),
        ("~//code/./x/../friday", "~/code/friday"),
        ("~/code/..", "~"),
        ("/../x", "/x"),
    ],
)
def test_normalise_path(raw, expected):
    assert normalise_path(raw) == expected


def test_relative_path_is_refused():
    from memory.ingestion.mapping import InvalidMappingKey

    with pytest.raises(InvalidMappingKey):
        normalise_path("code/friday")


@pytest.mark.parametrize("raw", ["~/..", "~/../x", "~/code/../../x", "~//../x"])
def test_home_path_climbing_above_home_is_refused(raw):
    from memory.ingestion.mapping import InvalidMappingKey

    with pytest.raises(InvalidMappingKey):
        normalise_path(raw)


def test_home_path_climbing_above_home_never_matches_a_home_rule(hooks, personal):
    add_rule(hooks, PATH, "~/x", personal)
    assert resolve_workspace(hooks, "path:~/x/y") == personal
    assert resolve_workspace(hooks, "path:~/../x") is None
    assert resolve_workspace(hooks, "path:~/../x/y") is None


def test_longest_prefix_wins(hooks, personal, firm):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    add_rule(hooks, PATH, "/Users/o/code/acme", firm)
    assert resolve_workspace(hooks, "path:/Users/o/code/acme/api") == firm
    assert resolve_workspace(hooks, "path:/Users/o/code/acme") == firm
    assert resolve_workspace(hooks, "path:/Users/o/code/acmeX") == personal
    assert resolve_workspace(hooks, "path:/Users/o/code/friday/") == personal
    assert resolve_workspace(hooks, "path:/Users/o/other") is None


def test_exact_repo_and_calendar_rules(sources, personal, firm):
    git, cal = sources["git"], sources["calendar"]
    add_rule(git, REPO, "42", firm)
    add_rule(cal, CAL, "binding-1", personal)
    assert resolve_workspace(git, "repo:42") == firm
    assert resolve_workspace(git, "repo:420") is None
    assert resolve_workspace(cal, "calendar:binding-1") == personal
    # A rule of another source never applies.
    assert resolve_workspace(cal, "repo:42") is None


def test_disabled_rule_and_malformed_keys_are_unmapped(hooks, personal):
    add_rule(hooks, PATH, "/Users/o", personal, enabled=False)
    for key in ["path:/Users/o/x", "nope:/Users/o", "path:relative", "", None, 7]:
        assert resolve_workspace(hooks, key) is None


def test_rule_to_a_workspace_the_owner_no_longer_owns_is_unmapped(owner, hooks, firm):
    add_rule(hooks, PATH, "/w", firm)
    WorkspaceMember.objects.filter(workspace=firm, user=owner).update(
        role=WorkspaceMember.Role.ADMIN
    )
    assert resolve_workspace(hooks, "path:/w/x") is None


def test_rule_targeting_a_workspace_the_owner_does_not_own_is_refused(owner, hooks):
    stranger = register_user(email="stranger@test.dev", password=PASSWORD)
    foreign = Workspace.objects.get(owner=stranger)
    WorkspaceMember.objects.create(workspace=foreign, user=owner, role=WorkspaceMember.Role.ADMIN)
    with pytest.raises(services.IngestionPermissionDenied):
        services.create_mapping_rule(
            hooks,
            actor=owner,
            channel=CHANNEL_WEB,
            rule_type=PATH,
            pattern="/x",
            target_workspace=foreign,
        )
    assert not WorkspaceMappingRule.objects.exists()


def test_create_rule_normalises_and_rejects_duplicates(owner, hooks, firm):
    rule = services.create_mapping_rule(
        hooks,
        actor=owner,
        channel=CHANNEL_WEB,
        rule_type=PATH,
        pattern="/a/b/",
        target_workspace=firm,
    )
    assert rule.pattern == "/a/b"
    with pytest.raises(services.IngestionInvalid):
        services.create_mapping_rule(
            hooks,
            actor=owner,
            channel=CHANNEL_WEB,
            rule_type=PATH,
            pattern="/a/b",
            target_workspace=firm,
        )
    with pytest.raises(services.IngestionInvalid):
        services.create_mapping_rule(
            hooks,
            actor=owner,
            channel=CHANNEL_WEB,
            rule_type=PATH,
            pattern="rel",
            target_workspace=firm,
        )


# --- port --------------------------------------------------------------------


def test_episode_lands_scrubbed_in_the_mapped_workspace(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    result = _record(
        hooks,
        owner_text=f"rotate {FAKE_KEY} and mail me at owner@example.com",
        context_text="notification: waiting",
        metadata={"session": "s1", "note": f"key={FAKE_KEY}"},
    )
    assert result.status == ports.STORED and result.stored
    episode = Episode.objects.get(id=result.episode_id)
    assert episode.workspace == personal
    assert "[SECRET:anthropic_key]" in episode.owner_text and "[PII:email]" in episode.owner_text
    assert episode.metadata["session"] == "s1"
    assert FAKE_KEY not in json.dumps(episode.metadata)
    assert episode.scrub_report == {"SECRET:anthropic_key": 2, "PII:email": 1}
    assert episode.token_estimate > 0
    counter = _counter(hooks)
    assert counter.stored == 1 and counter.scrubber_hits == episode.scrub_report


def test_no_raw_secret_reaches_any_row(hooks, personal):
    """Release-gate regression: a raw secret from a payload in any row."""
    add_rule(hooks, PATH, "/Users/o/code", personal)
    _record(
        hooks,
        owner_text=FAKE_KEY,
        context_text=FAKE_KEY,
        metadata={FAKE_KEY: [FAKE_KEY]},
        external_id=f"x-{FAKE_KEY}",
    )
    _record(hooks, external_id="e2", mapping_key=f"path:/nowhere/{FAKE_KEY}")
    with connection.cursor() as cursor:
        for table in (
            Episode._meta.db_table,
            IngestionCounter._meta.db_table,
            AuditLog._meta.db_table,
        ):
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 -- fixed names
            assert FAKE_KEY not in repr(cursor.fetchall()), table


def test_unmapped_item_is_dropped_and_counted_never_defaulted(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    result = _record(hooks, mapping_key="path:/tmp/elsewhere/a.py", owner_text="hello")
    assert result.status == ports.DROPPED_UNMAPPED and result.episode_id is None
    assert not Episode.all_objects.exists()
    counter = _counter(hooks)
    assert counter.dropped_unmapped == 1
    [entry] = counter.unmapped_keys
    assert len(entry["hash"]) == 64 and entry["short"] == "path:/tmp/elsewhere/a.py"
    _record(hooks, mapping_key="path:/tmp/elsewhere/a.py", external_id="e2")
    counter.refresh_from_db()
    assert counter.dropped_unmapped == 2 and len(counter.unmapped_keys) == 1


def test_unmapped_keys_are_capped(hooks):
    for i in range(IngestionCounter.UNMAPPED_KEYS_CAP + 5):
        _record(hooks, mapping_key=f"path:/u/{i}", external_id=f"e{i}")
    assert len(_counter(hooks).unmapped_keys) == IngestionCounter.UNMAPPED_KEYS_CAP


def test_firm_rule_lands_in_the_firm_workspace_only(hooks, personal, firm):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    add_rule(hooks, PATH, "/Users/o/code/acme", firm)
    result = _record(hooks, mapping_key="path:/Users/o/code/acme/api", owner_text="acme work")
    episode = Episode.objects.get(id=result.episode_id)
    assert episode.workspace == firm
    assert not Episode.objects.filter(workspace=personal).exists()


@pytest.mark.parametrize("state", ["disabled", "paused"])
def test_disabled_or_paused_source_drops(hooks, personal, state):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    if state == "disabled":
        hooks.enabled = False
    else:
        hooks.paused_at = timezone.now()
    hooks.save()
    assert _record(hooks).status == ports.DROPPED_PAUSED
    assert not Episode.all_objects.exists()
    assert _counter(hooks).dropped_paused == 1


def test_unregistered_source_kind_drops(db):
    assert _record("git").status == ports.DROPPED_PAUSED


def test_source_by_kind_resolves_the_owners_source(sources, personal):
    git = sources["git"]
    git.enabled = True
    git.save()
    add_rule(git, REPO, "7", personal)
    result = _record("git", mapping_key="repo:7", kind="git.commit")
    assert result.stored
    assert Episode.objects.get(id=result.episode_id).source == git


def test_duplicate_external_id_is_deduped(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    assert _record(hooks).stored
    assert _record(hooks, owner_text="changed").status == ports.DUPLICATE
    assert Episode.all_objects.count() == 1
    assert _counter(hooks).duplicate == 1


def test_duplicate_of_a_trashed_episode_stays_duplicate(owner, hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    _record(hooks)
    services.forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    services.update_source(hooks, actor=owner, channel=CHANNEL_WEB, paused=False)
    assert _record(hooks).status == ports.DUPLICATE


def test_text_over_the_cap_is_truncated_after_scrubbing(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    # The secret sits across the cap boundary: scrubbing first means no
    # partial secret survives the cut.
    text = "a " * 1995 + FAKE_KEY + " tail"
    result = _record(hooks, owner_text=text, context_text="b" * 9000)
    episode = Episode.objects.get(id=result.episode_id)
    assert len(episode.owner_text) == Episode.MAX_TEXT_CHARS
    assert len(episode.context_text) == Episode.MAX_TEXT_CHARS
    assert "sk-" not in episode.owner_text and "NEVERSTORED" not in episode.owner_text


def test_scrub_terms_of_the_target_workspace_apply(owner, hooks, personal, firm):
    add_rule(hooks, PATH, "/p", personal)
    add_rule(hooks, PATH, "/f", firm)
    term = services.create_scrub_term(
        workspace=firm, actor=owner, channel=CHANNEL_WEB, term="Globex Corp"
    )
    firm_ep = Episode.objects.get(
        id=_record(hooks, mapping_key="path:/f/x", owner_text="call GLOBEX corp").episode_id
    )
    personal_ep = Episode.objects.get(
        id=_record(
            hooks, mapping_key="path:/p/x", owner_text="call Globex Corp", external_id="e2"
        ).episode_id
    )
    assert firm_ep.owner_text == f"call [TERM:{str(term.id).replace('-', '')[:12]}]"
    assert personal_ep.owner_text == "call Globex Corp"


def test_external_id_with_a_secret_is_hashed(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    result = _record(hooks, external_id=f"x:{FAKE_KEY}")
    episode = Episode.objects.get(id=result.episode_id)
    assert episode.external_id.startswith("sha256:")
    assert _record(hooks, external_id=f"x:{FAKE_KEY}").status == ports.DUPLICATE


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "Bad Kind"},
        {"occurred_at": WHEN.replace(tzinfo=None)},
        {"sensitivity": "secret"},
        {"metadata": ["not", "a", "dict"]},
        {"external_id": ""},
    ],
)
def test_producer_bugs_raise(hooks, overrides):
    with pytest.raises(InvalidEpisode):
        _record(hooks, **overrides)


def test_private_sensitivity_is_kept(hooks, personal):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    result = _record(hooks, sensitivity="private")
    assert Episode.objects.get(id=result.episode_id).sensitivity == "private"


# --- TTL ---------------------------------------------------------------------


def test_ttl_is_30_days_personal_and_14_days_firm(hooks, personal, firm):
    add_rule(hooks, PATH, "/p", personal)
    add_rule(hooks, PATH, "/f", firm)
    before = timezone.now()
    p = Episode.objects.get(id=_record(hooks, mapping_key="path:/p/1").episode_id)
    f = Episode.objects.get(id=_record(hooks, mapping_key="path:/f/1", external_id="e2").episode_id)
    assert timedelta(days=30) <= p.expires_at - before < timedelta(days=30, minutes=1)
    assert timedelta(days=14) <= f.expires_at - before < timedelta(days=14, minutes=1)


def test_ttl_days_follow_settings(settings, hooks, firm):
    settings.MEMORY_EPISODE_TTL_DAYS_FIRM = 3
    add_rule(hooks, PATH, "/f", firm)
    f = Episode.objects.get(id=_record(hooks, mapping_key="path:/f/1").episode_id)
    assert f.expires_at - timezone.now() < timedelta(days=3, minutes=1)


def test_beat_purges_only_expired_rows(owner, hooks, personal):
    add_rule(hooks, PATH, "/p", personal)
    keep = _record(hooks, mapping_key="path:/p/1", external_id="keep").episode_id
    old = _record(hooks, mapping_key="path:/p/1", external_id="old").episode_id
    trashed_old = _record(hooks, mapping_key="path:/p/1", external_id="trashed").episode_id
    Episode.all_objects.filter(id__in=[old, trashed_old]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    Episode.all_objects.filter(id=trashed_old).update(deleted_at=timezone.now())
    # Expired rows are already invisible to the default manager.
    assert set(Episode.objects.values_list("id", flat=True)) == {
        Episode.all_objects.get(id=keep).id
    }
    assert purge_expired_episodes_task() == 2
    assert list(Episode.all_objects.values_list("external_id", flat=True)) == ["keep"]


# --- forget / restore ----------------------------------------------------------


def _three_episodes(hooks, personal, firm):
    add_rule(hooks, PATH, "/p", personal)
    add_rule(hooks, PATH, "/f", firm)
    _record(hooks, mapping_key="path:/p/1", external_id="a")
    _record(hooks, mapping_key="path:/p/1", external_id="b")
    _record(hooks, mapping_key="path:/f/1", external_id="c")


def test_forget_pauses_trashes_and_is_audited_with_counts_only(owner, hooks, personal, firm):
    _three_episodes(hooks, personal, firm)
    result = services.forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    assert result["episodes"] == 3
    hooks.refresh_from_db()
    assert hooks.paused_at is not None and hooks.forgotten_at is not None
    assert not Episode.objects.exists()
    assert Episode.all_objects.filter(deleted_at__isnull=False).count() == 3
    audit = AuditLog.objects.get(action="memory.source_forgotten")
    assert audit.metadata == {
        "kind": "claude_code_hooks",
        "operation_id": result["operation_id"],
        "episodes": 3,
    }
    # Paused: new items are dropped.
    assert _record(hooks, mapping_key="path:/p/1", external_id="d").status == ports.DROPPED_PAUSED


def test_restore_undoes_exactly_the_forget_operation(owner, hooks, personal, firm):
    _three_episodes(hooks, personal, firm)
    # An episode trashed by something else (not this operation) stays trashed.
    other = Episode.all_objects.get(external_id="a")
    Episode.all_objects.filter(pk=other.pk).update(deleted_at=timezone.now())
    services.forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    result = services.restore_source(hooks, actor=owner, channel=CHANNEL_WEB)
    assert result["episodes"] == 2
    assert set(Episode.objects.values_list("external_id", flat=True)) == {"b", "c"}
    hooks.refresh_from_db()
    assert hooks.forgotten_at is None and hooks.forget_operation_id is None
    assert hooks.paused_at is not None  # resumed explicitly by the owner
    assert AuditLog.objects.get(action="memory.source_restored").metadata["episodes"] == 2
    with pytest.raises(services.IngestionInvalid):
        services.restore_source(hooks, actor=owner, channel=CHANNEL_WEB)


@pytest.mark.parametrize("channel", [CHANNEL_CLI, CHANNEL_OPENAI, CHANNEL_SYSTEM, "telegram"])
def test_forget_requires_the_web_channel(owner, hooks, personal, firm, channel):
    _three_episodes(hooks, personal, firm)
    with pytest.raises(services.IngestionPermissionDenied):
        services.forget_source(hooks, actor=owner, channel=channel)
    assert Episode.objects.count() == 3


def test_forget_and_restore_refuse_an_agent_and_a_non_owner(owner, hooks, personal, firm):
    from accounts.models import User

    _three_episodes(hooks, personal, firm)
    agent = User.objects.create_user(email="agent@test.dev", is_agent=True)
    WorkspaceMember.objects.create(workspace=personal, user=agent, role=WorkspaceMember.Role.OWNER)
    admin = register_user(email="admin@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=personal, user=admin, role=WorkspaceMember.Role.ADMIN)
    for actor in (agent, admin):
        with pytest.raises(services.IngestionPermissionDenied):
            services.forget_source(hooks, actor=actor, channel=CHANNEL_WEB)
    services.forget_source(hooks, actor=owner, channel=CHANNEL_WEB)
    for actor in (agent, admin):
        with pytest.raises(services.IngestionPermissionDenied):
            services.restore_source(hooks, actor=actor, channel=CHANNEL_WEB)


def test_update_source_toggles_and_audits(owner, sources):
    git = sources["git"]
    services.update_source(git, actor=owner, channel=CHANNEL_WEB, enabled=True, paused=True)
    git.refresh_from_db()
    assert git.enabled and git.paused_at is not None and not git.is_accepting
    services.update_source(git, actor=owner, channel=CHANNEL_WEB, paused=False)
    git.refresh_from_db()
    assert git.is_accepting
    services.update_source(git, actor=owner, channel=CHANNEL_WEB, enabled=True)  # no-op
    assert AuditLog.objects.filter(action="memory.ingestion.source_updated").count() == 2
    with pytest.raises(services.IngestionPermissionDenied):
        services.update_source(git, actor=owner, channel=CHANNEL_CLI, enabled=False)


def test_scrub_term_audit_never_carries_the_term(owner, personal):
    services.create_scrub_term(workspace=personal, actor=owner, channel=CHANNEL_WEB, term="Initech")
    with pytest.raises(services.IngestionInvalid):
        services.create_scrub_term(
            workspace=personal, actor=owner, channel=CHANNEL_WEB, term="initech"
        )
    audit = AuditLog.objects.get(action="memory.ingestion.scrub_term_created")
    assert "Initech" not in json.dumps(audit.metadata)
    term = ScrubTerm.objects.get()
    services.delete_scrub_term(term, actor=owner, channel=CHANNEL_WEB)
    assert not ScrubTerm.objects.exists()


# --- isolation -----------------------------------------------------------------


def test_episode_for_actor_isolation(owner, hooks, personal, firm):
    add_rule(hooks, PATH, "/p", personal)
    add_rule(hooks, PATH, "/f", firm)
    _record(hooks, mapping_key="path:/p/1", external_id="personal")
    _record(hooks, mapping_key="path:/f/1", external_id="firm")
    firm_only = register_user(email="firm-only@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(workspace=firm, user=firm_only, role=WorkspaceMember.Role.MEMBER)
    seen = set(Episode.objects.for_actor(firm_only).values_list("external_id", flat=True))
    assert seen == {"firm"}
    personal_only = register_user(email="p-only@test.dev", password=PASSWORD)
    WorkspaceMember.objects.create(
        workspace=personal, user=personal_only, role=WorkspaceMember.Role.MEMBER
    )
    seen = set(Episode.objects.for_actor(personal_only).values_list("external_id", flat=True))
    assert seen == {"personal"}
    assert set(Episode.objects.for_actor(owner).values_list("external_id", flat=True)) == {
        "personal",
        "firm",
    }
