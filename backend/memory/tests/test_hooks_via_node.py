"""The memory side of the node hook relay (Phase 9 brief D10; branch 6).

The gateway's `claude_code.hook` sink is `ingest_hook_events` itself,
registered by the composition root (`config.asgi_nodes`); the end-to-end
tests through the reference node live in
`config/tests/test_hooks_via_node.py`, because `memory` may not import
`nodes` (ADR-013 decision 9, the "only tools, hud, ops may import nodes"
contract). Here: the sink contract `nodes.events` relies on, under the node
principal binding (the owner and the owner's personal workspace, exactly the
`hook` PAT's binding).
"""

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from memory.ingestion import hooks
from memory.ingestion.hooks import ingest_hook_events
from memory.models import CodingSession, Episode, WorkspaceMappingRule
from memory.tests.ingestion_helpers import add_rule

pytestmark = pytest.mark.django_db

PATH = WorkspaceMappingRule.RuleType.PATH_PREFIX
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MEMORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def mapped(hooks, personal, firm):
    add_rule(hooks, PATH, "/Users/o/code", personal)
    add_rule(hooks, PATH, "/Users/o/code/acme", firm)
    return hooks


def _event(n, *, cwd="/Users/o/code/acme/api", name="SessionStart", **extra):
    return {
        "hook_event_name": name,
        "session_id": f"cc-{n}",
        "cwd": cwd,
        "event_id": f"{n:032x}",
        "occurred_at": NOW.isoformat(),
        **extra,
    }


def _ingest(owner, personal, events):
    """Exactly how `nodes.events` calls the sink."""
    return ingest_hook_events(user=owner, bound_workspace_id=personal.id, events=events, now=NOW)


def test_the_sink_signature_is_keyword_only():
    params = inspect.signature(ingest_hook_events).parameters
    assert {"user", "bound_workspace_id", "events"} <= set(params)
    assert all(
        params[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("user", "bound_workspace_id", "events")
    )


def test_the_counts_carry_what_the_ack_maps(owner, personal, mapped):
    counts = _ingest(owner, personal, [_event(1)])
    # `event.ack` = stored, duplicate, and everything else as dropped.
    assert {"received", "stored", "duplicate"} <= set(counts)
    assert all(isinstance(value, int) for value in counts.values())
    assert counts["stored"] == 1


def test_the_mapping_rules_alone_decide_the_workspace(owner, personal, firm, mapped):
    counts = _ingest(
        owner,
        personal,
        [_event(1), _event(2, cwd="/Users/o/code/friday"), _event(3, cwd="/elsewhere")],
    )
    assert (counts["stored"], counts["dropped_unmapped"]) == (2, 1)
    assert {s.session_id: s.workspace_id for s in CodingSession.objects.all()} == {
        "cc-1": firm.id,
        "cc-2": personal.id,
    }


def test_a_replayed_event_is_a_duplicate(owner, personal, mapped):
    assert _ingest(owner, personal, [_event(1)])["stored"] == 1
    again = _ingest(owner, personal, [_event(1)])
    assert (again["stored"], again["duplicate"]) == (0, 1)
    assert Episode.all_objects.count() == 1


def test_unknown_names_and_stale_events_never_store(owner, personal, mapped):
    old = (NOW - hooks.MAX_EVENT_AGE - timedelta(minutes=1)).isoformat()
    counts = _ingest(owner, personal, [_event(1, name="PreToolUse"), _event(2, occurred_at=old)])
    assert (counts["ignored"], counts["invalid"], counts["stored"]) == (1, 1, 0)
    assert not Episode.all_objects.exists()


def test_memory_never_imports_nodes():
    """D10: `config` wires the two; `memory` stays unaware of `nodes`
    (import-linter enforces it too)."""
    offenders = [
        path.relative_to(MEMORY).as_posix()
        for path in MEMORY.rglob("*.py")
        if "tests" not in path.parts
        and any(
            line.lstrip().startswith(("import nodes", "from nodes"))
            for line in path.read_text("utf-8").splitlines()
        )
    ]
    assert offenders == []
