# ruff: noqa: F811 -- pytest fixtures imported from the helper module are redefined as arguments
"""Phase 12 D17: the provenance check in code. A step is verified only by a
verbatim quote (after whitespace normalisation) of its own source's
owner-typed goal or test command; paths are unverified hints; context text
(model-written) is never a quote."""

import pytest

from skills import playbooks
from skills.playbooks import (
    DROP_MALFORMED,
    DROP_QUOTE_FROM_CONTEXT,
    DROP_QUOTE_IS_PATH,
    DROP_QUOTE_NOT_FOUND,
    DROP_QUOTE_TOO_SHORT,
    DROP_UNKNOWN_SOURCE,
    FIELD_GOAL,
    FIELD_TEST_COMMAND,
    PlaybookParseError,
    parse_draft,
    verify_steps,
)
from skills.tests.playbook_helpers import owner, record_sources, step, workspace  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def sources(workspace):
    return record_sources(workspace, 3)


def _verify(steps, sources):
    return verify_steps(steps, {str(s.job_id): s for s in sources})


def test_goal_and_test_command_quotes_are_verified(sources):
    result = _verify(
        [
            step(sources[0], "Migration ekle.", "migration ekle"),
            step(sources[1], "Testleri çalıştır.", "make test"),
        ],
        sources,
    )
    assert [s.field for s in result.steps] == [FIELD_GOAL, FIELD_GOAL]
    result = _verify([step(sources[0], "Test.", "make  \n test")], sources)
    assert result.steps[0].quote == "make test"


def test_test_command_field_is_quotable(workspace):
    from skills.tests.playbook_helpers import record_source

    source_id = record_source(workspace, goal="kullanıcı modeline alan ekle", test_command="uv run pytest -q")
    from skills.models import PlaybookSource

    source = PlaybookSource.objects.get(pk=source_id)
    result = _verify([step(source, "pytest ile doğrula.", "uv run pytest -q")], [source])
    assert result.steps[0].field == FIELD_TEST_COMMAND


def test_whitespace_normalisation_applies_to_the_field_too(sources):
    sources[0].goal = "acme-api:   migration\n\nekle"
    result = _verify([step(sources[0], "Ekle.", "migration ekle")], sources)
    assert len(result.steps) == 1


def test_a_quote_from_context_text_is_dropped(sources):
    result = _verify([step(sources[0], "Ekle.", "added a migration")], sources)
    assert result.steps == [] and result.dropped == {DROP_QUOTE_FROM_CONTEXT: 1}


def test_a_quote_not_verbatim_in_its_source_is_dropped(sources):
    result = _verify([step(sources[0], "Ekle.", "migration eklemeyi unutma")], sources)
    assert result.steps == [] and result.dropped == {DROP_QUOTE_NOT_FOUND: 1}


def test_case_matters(sources):
    result = _verify([step(sources[0], "Ekle.", "MIGRATION EKLE")], sources)
    assert result.dropped == {DROP_QUOTE_NOT_FOUND: 1}


def test_a_quote_from_another_sources_goal_does_not_verify(sources):
    # Only source 1's goal contains "yeni bir migration".
    result = _verify([step(sources[0], "Ekle.", "yeni bir migration")], sources)
    assert result.dropped == {DROP_QUOTE_NOT_FOUND: 1}
    assert len(_verify([step(sources[1], "Ekle.", "yeni bir migration")], sources).steps) == 1


def test_a_path_quote_is_only_an_unverified_hint(sources):
    result = _verify(
        [step(sources[0], "Dosyaya dokun.", "backend/app/models.py")],
        sources,
    )
    assert result.steps == []
    assert result.dropped == {DROP_QUOTE_IS_PATH: 1}
    assert result.unverified_paths == ["backend/app/models.py"]


def test_step_paths_are_filtered_to_the_sources_changed_paths(sources):
    result = _verify(
        [
            step(
                sources[0],
                "Ekle.",
                "migration ekle",
                ["backend/app/models.py", "/etc/passwd", ".claude/skills/x/SKILL.md"],
            )
        ],
        sources,
    )
    assert result.steps[0].paths == ("backend/app/models.py",)


def test_unknown_source_and_malformed_steps(sources):
    result = _verify(
        [
            {"text": "x", "source": "00000000-0000-0000-0000-000000000000", "quote": "make test"},
            {"text": "x", "source": str(sources[0].job_id)},
            {"text": "x", "source": str(sources[0].job_id), "quote": "make test", "run": "rm"},
            {"text": 1, "source": str(sources[0].job_id), "quote": "make test"},
            {"text": "x", "source": str(sources[0].job_id), "quote": "make test", "paths": "a"},
            "not a dict",
        ],
        sources,
    )
    assert result.steps == []
    assert result.dropped == {DROP_UNKNOWN_SOURCE: 1, DROP_MALFORMED: 5}


def test_too_short_quotes_are_not_verified(sources, settings):
    settings.SKILLS_PLAYBOOK_MIN_QUOTE_CHARS = 8
    result = _verify([step(sources[0], "Ekle.", "ekle")], sources)
    assert result.dropped == {DROP_QUOTE_TOO_SHORT: 1}
    # The whole field is always enough, however short ("make test" is 9).
    sources[0].test_command = "tox"
    assert len(_verify([step(sources[0], "Tox.", "tox")], sources).steps) == 1


def test_steps_are_capped(sources, settings):
    settings.SKILLS_PLAYBOOK_MAX_STEPS = 2
    result = _verify([step(sources[0], f"Adım {i}.", "make test") for i in range(4)], sources)
    assert len(result.steps) == 2
    assert result.dropped == {"over_limit": 2}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        '{"playbook": "x"}',
        '{"name": "a", "description": "b"}',
        '{"name": "a", "description": "b", "steps": {}}',
        '{"name": 1, "description": "b", "steps": []}',
        '{"name": "a", "description": "b", "steps": [], "approve": true}',
    ],
)
def test_parse_draft_refuses(raw):
    with pytest.raises(PlaybookParseError):
        parse_draft(raw)


def test_parse_draft_accepts_null_and_a_draft():
    assert parse_draft('{"playbook": null}') is None
    assert parse_draft('{"name": "a", "description": "b", "steps": []}')["steps"] == []


def test_skill_name_falls_back_and_never_reuses_an_owner_name():
    assert playbooks.skill_name("acme-api-migration", "acme") == "acme-api-migration"
    assert playbooks.skill_name("Bad Name!", "Acme API") == "playbook-acme-api"
    assert playbooks.skill_name("pr-yaz", "Acme", reserved={"pr-yaz"}) == "playbook-acme"
    assert playbooks.skill_name("", "ğüş") == "playbook-gus"
