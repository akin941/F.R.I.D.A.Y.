"""Date-boundary regressions (fix/tz-fact-learning-test).

`TIME_ZONE` is Europe/Istanbul (UTC+3) and `USE_TZ` is on, so a row read
back from Postgres carries UTC-aware datetimes. A fact whose `valid_from`
came from a date (`as_instant(date)` -> local midnight) is stored as 21:00
UTC the day before: rendering it with `value.date()` printed the wrong
day, at any hour. Every rendered day is the local calendar day
(`timezone.localdate(value)`); these tests read the row back from the
database and pin the clock at a midday and a late-evening UTC instant."""

from datetime import UTC, date, datetime

import pytest

from memory.context import render_fact_line
from memory.graph.services import as_instant
from memory.ingestion.reconciliation import build_reconciliation_messages
from memory.models import MemoryFact
from memory.tests.graph_helpers import entity, fact

pytestmark = pytest.mark.django_db

CLOCKS = [
    pytest.param(datetime(2026, 11, 20, 12, 0, tzinfo=UTC), id="12:00Z"),
    pytest.param(datetime(2026, 11, 20, 22, 30, tzinfo=UTC), id="22:30Z"),
]


def stored_local_midnight_fact(workspace, **kwargs):
    api = entity(workspace, "billing api")
    row = fact(workspace, api, text="hetzner", valid_from=as_instant(date(2026, 11, 20)), **kwargs)
    return MemoryFact.all_objects.get(pk=row.pk)  # UTC-aware, as production reads it


@pytest.mark.parametrize("now", CLOCKS)
def test_a_fact_line_shows_the_local_days_of_its_window(personal, frozen_now, now):
    frozen_now(now)
    row = stored_local_midnight_fact(personal, valid_to=as_instant(date(2026, 11, 21)))
    assert row.valid_from.date() == date(2026, 11, 19)  # the trap: the UTC day
    assert render_fact_line(row).endswith("(2026-11-20–2026-11-21)")


@pytest.mark.parametrize("now", CLOCKS)
def test_an_open_window_keeps_an_empty_end(personal, frozen_now, now):
    frozen_now(now)
    row = stored_local_midnight_fact(personal)
    assert render_fact_line(row).endswith("(2026-11-20–)")


@pytest.mark.parametrize("now", CLOCKS)
def test_the_reconciliation_payload_dates_an_existing_fact_by_its_local_day(
    personal, frozen_now, now
):
    frozen_now(now)
    row = stored_local_midnight_fact(personal)
    candidate = {
        "content": "Billing api Hetzner'da.",
        "fact": {
            "subject": {"kind": "service", "name": "billing api"},
            "predicate": MemoryFact.Predicate.DEPLOYED_TO,
            "object": {"text": "aws"},
            "valid_from": "2026-11-20",
        },
    }
    messages = build_reconciliation_messages("prompt", [candidate], {0: [row]})
    payload = "\n".join(m["content"] for m in messages)
    assert f'id="{row.pk}" candidate_index="0" valid_from="2026-11-20"' in payload
    assert 'valid_from="2026-11-19"' not in payload
