"""Phase 12 D6/D20 (branch 2): role x endpoint for the digest endpoints
(`latest`, detail, `ack`) over the same principals as the branch 1
surfaces; the ack's idempotence, web-only rule and workspace isolation;
and `proactive.api` (`latest_digest`, `mark_digest_spoken`)."""

import pytest

from audit.unattended import recorded_unattended_run
from core.unattended import UnattendedDenied
from proactive import api
from proactive.models import DigestRun, ProactiveRun
from proactive.tests.conftest import make_schedule
from proactive.tests.test_views import MATRIX, _query, principals  # noqa: F401

pytestmark = pytest.mark.django_db

BASE = "/api/v1/proactive/digests"


def make_digest(workspace, owner, *, text="Günaydın.", speak_on_open=False):
    schedule = make_schedule(workspace, owner, kind="morning_digest", speak_on_open=speak_on_open)
    run = ProactiveRun.objects.create(
        workspace=workspace,
        schedule=schedule,
        kind="morning_digest",
        started_at=schedule.created_at,
    )
    return DigestRun.objects.create(
        workspace=workspace,
        run=run,
        schedule=schedule,
        sections=[],
        spoken_text=text,
        composed_by=DigestRun.ComposedBy.TEMPLATE,
        data_class="personal" if workspace.type == "personal" else "firm",
    )


@pytest.fixture
def digests(owner, workspace, firm):
    return {
        "personal": make_digest(workspace, owner, text="Kişisel brifing.", speak_on_open=True),
        "firm": make_digest(firm, owner, text="Firma brifingi."),
    }


def _target(principal, digests):
    return digests["firm"] if principal in ("owner-firm", "firm-member") else digests["personal"]


ENDPOINTS = {
    "latest": lambda c, q, d: c.get(f"{BASE}/latest{q}"),
    "detail": lambda c, q, d: c.get(f"{BASE}/{d.pk}"),
    "ack": lambda c, q, d: c.post(f"{BASE}/{d.pk}/ack", {"event": "opened"}, format="json"),
}


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_role_matrix(principals, workspace, firm, digests, endpoint, principal, expected):  # noqa: F811
    client = principals(principal)
    digest = _target(principal, digests)
    response = ENDPOINTS[endpoint](client, _query(principal, workspace, firm), digest)
    assert response.status_code == expected, response.content
    digest.refresh_from_db()
    if expected != 200:
        assert digest.opened_at is None
    elif endpoint == "latest":
        assert response.json()["digest"]["id"] == str(digest.pk)
    elif endpoint == "ack":
        assert digest.opened_at is not None


def test_latest_returns_the_newest_digest_with_speak_on_open(owner, workspace, web_client, digests):
    client = web_client(owner)
    body = client.get(f"{BASE}/latest").json()
    assert body["workspace"] == str(workspace.pk)
    digest = body["digest"]
    assert digest["spoken_text"] == "Kişisel brifing."
    assert digest["speak_on_open"] is True
    assert digest["opened_at"] is None and digest["spoken_at"] is None


def test_latest_is_null_without_a_digest(owner, web_client):
    assert web_client(owner).get(f"{BASE}/latest").json()["digest"] is None


def test_ack_is_idempotent_and_records_the_web_channel(owner, web_client, digests):
    client = web_client(owner)
    digest = digests["personal"]
    first = client.post(f"{BASE}/{digest.pk}/ack", {"event": "spoken"}, format="json").json()
    assert first["changed"] is True
    assert first["spoken_channel"] == "web"
    digest.refresh_from_db()
    spoken_at = digest.spoken_at
    again = client.post(f"{BASE}/{digest.pk}/ack", {"event": "spoken"}, format="json").json()
    assert again["changed"] is False
    digest.refresh_from_db()
    assert digest.spoken_at == spoken_at
    opened = client.post(f"{BASE}/{digest.pk}/ack", {"event": "opened"}, format="json").json()
    assert opened["changed"] is True and opened["opened_at"]


def test_ack_refuses_an_unknown_event(owner, web_client, digests):
    response = web_client(owner).post(
        f"{BASE}/{digests['personal'].pk}/ack", {"event": "approved"}, format="json"
    )
    assert response.status_code == 400


def test_another_users_digest_is_404(member, web_client, owner):
    from workspaces.models import Workspace

    other = Workspace.objects.get(owner=member, type=Workspace.Type.PERSONAL)
    foreign = make_digest(other, member)
    client = web_client(owner)
    assert client.get(f"{BASE}/{foreign.pk}").status_code == 404
    assert (
        client.post(f"{BASE}/{foreign.pk}/ack", {"event": "opened"}, format="json").status_code
        == 404
    )
    foreign.refresh_from_db()
    assert foreign.opened_at is None


def test_api_latest_digest_is_workspace_scoped(owner, workspace, firm, digests):
    assert api.latest_digest(actor=owner, workspace=workspace)["id"] == str(digests["personal"].pk)
    assert api.latest_digest(actor=owner, workspace=firm)["id"] == str(digests["firm"].pk)
    card = api.digest_section(actor=owner, workspace=workspace)
    assert card["spoken_text"] == "Kişisel brifing." and card["speak_on_open"] is True


def test_api_mark_digest_spoken_sets_once_and_refuses_inside_a_run(owner, workspace, firm, digests):
    personal = digests["personal"]
    assert (
        api.mark_digest_spoken(
            actor=owner, workspace=workspace, digest_id=personal.pk, channel="node_voice"
        )
        is True
    )
    assert (
        api.mark_digest_spoken(
            actor=owner, workspace=workspace, digest_id=personal.pk, channel="web"
        )
        is False
    )
    personal.refresh_from_db()
    assert personal.spoken_channel == "node_voice"
    # A firm digest never answers through the personal workspace.
    assert (
        api.mark_digest_spoken(
            actor=owner, workspace=workspace, digest_id=digests["firm"].pk, channel="web"
        )
        is None
    )
    with pytest.raises(ValueError):
        api.mark_digest_spoken(
            actor=owner, workspace=firm, digest_id=digests["firm"].pk, channel="telegram"
        )
    with recorded_unattended_run("run-1", allowed_purposes=frozenset()):
        with pytest.raises(UnattendedDenied):
            api.mark_digest_spoken(
                actor=owner, workspace=firm, digest_id=digests["firm"].pk, channel="web"
            )
    digests["firm"].refresh_from_db()
    assert digests["firm"].spoken_at is None
