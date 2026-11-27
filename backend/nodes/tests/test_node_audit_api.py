"""Phase 13 D8: `GET /api/v1/nodes/<id>/audit` -- the node's chain rows
merged with its AuditLog rows (paired, relabelled, revoked, kill switch,
credential rotation, refusals, voice mutes), newest first, cursor paging,
that node only, codes and keys only (never args, output or a client
address); role x endpoint; another user's node is 404; every view is
audited `nodes.audit.viewed` and such rows are never listed."""

import json

import pytest

from accounts import devices
from accounts.models import PersonalAccessToken, User
from accounts.tests.node_voice_helpers import bearer, make_pat, mint_token
from audit.models import AuditLog
from audit.services import record_audit
from core.channels import CHANNEL_WEB
from nodes import selectors, services
from nodes.services import pair_node, relabel_node
from nodes.tests.conftest import node_code
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

SECRET_ARG = "~/work/secret-project"


def url(node, before=None):
    base = f"/api/v1/nodes/{node.pk}/audit"
    return base if before is None else f"{base}?before={before}"


@pytest.fixture
def two(owner, personal):
    mac = pair_node(
        raw_code=node_code(owner),
        node_name="MacBook",
        protocol_versions=[1],
        client_ip="10.0.0.9",
    ).node
    desk = pair_node(raw_code=node_code(owner), node_name="Masaüstü", protocol_versions=[1]).node
    return mac, desk


def refuse(node, workspace, n=1):
    for i in range(n):
        services.refuse_read(
            node=node,
            capability="git.status",
            args={"repo_path": f"{SECRET_ARG}/{i}"},
            workspace=workspace,
            actor=node.owner,
            trace_id=f"trace-{node.name_key}-{i}",
            code="path_not_in_workspace",
        )


def test_chain_rows_and_events_of_that_node_only_newest_first(
    owner, personal, two, web_client, fake_revocation_redis
):
    mac, desk = two
    refuse(mac, personal)
    refuse(desk, personal)
    relabel_node(actor=owner, node=mac, channel=CHANNEL_WEB, name="Dizüstü", role="laptop")
    services.record_actor_refusal(actor=owner, node_id=mac.pk, capability="git.status")
    devices.engage_node_kill_switch(owner=owner, via="test")
    devices.release_node_kill_switch(owner=owner, via="test")
    devices.revoke_device(device=mac.device, actor=owner)

    response = web_client(owner).get(url(mac))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["next_cursor"] is None
    kinds = [(row["kind"], row.get("action") or row.get("capability")) for row in body["results"]]
    assert kinds == [
        ("event", "accounts.device.revoked"),
        ("event", "accounts.node.kill_switch.released"),
        ("event", "accounts.node.kill_switch.engaged"),
        ("event", "nodes.refused"),
        ("event", "nodes.relabelled"),
        ("invocation", "git.status"),
        ("event", "nodes.node.paired"),
        ("event", "accounts.node.enrolled"),
    ]
    stamps = [row["created_at"] for row in body["results"]]
    assert stamps == sorted(stamps, reverse=True)
    relabelled = body["results"][4]
    assert relabelled["details"] == {
        "old_key": "macbook",
        "new_key": "dizüstü",
        "old_role": "other",
        "new_role": "laptop",
        "fields": ["name", "role"],
    }
    paired = body["results"][-2]
    assert paired["details"]["platform"] == "macos"
    invocation = body["results"][5]
    assert invocation["status"] == "server_refused" and len(invocation["args_sha256"]) == 64
    text = json.dumps(body)
    # Nothing of the other node, no args, no client address, no viewed rows.
    assert "masaüstü" not in text and str(desk.pk) not in text
    assert "secret-project" not in text and "10.0.0.9" not in text
    assert 'nodes.invoke"' not in text and "audit.viewed" not in text


def test_paging_with_a_cursor(owner, personal, two, web_client):
    mac, _ = two
    refuse(mac, personal, n=55)
    client = web_client(owner)
    first = client.get(url(mac)).json()
    assert len(first["results"]) == 50 and first["next_cursor"]
    second = client.get(url(mac, first["next_cursor"])).json()
    assert second["next_cursor"] is None
    ids = [row["id"] for row in first["results"]] + [row["id"] for row in second["results"]]
    assert len(ids) == len(set(ids)) == 57  # 55 chain rows + enrolled + paired
    assert second["results"][-1]["action"] == "accounts.node.enrolled"


@pytest.mark.parametrize(
    "cursor", ["x", "12.not-a-uuid", "-1.00000000-0000-4000-8000-000000000000"]
)
def test_an_invalid_cursor_is_400(owner, two, web_client, cursor):
    mac, _ = two
    response = web_client(owner).get(url(mac, cursor))
    assert response.status_code == 400 and response.json()["code"] == "invalid_cursor"


def test_cursor_round_trip():
    from django.utils import timezone

    now = timezone.now()
    row_id = "00000000-0000-4000-8000-000000000001"
    assert selectors.decode_audit_cursor(selectors.encode_audit_cursor(now, row_id)) == (
        now,
        row_id,
    )


def test_every_view_is_audited_and_not_listed(owner, two, web_client):
    mac, _ = two
    client = web_client(owner)
    client.get(url(mac))
    second = client.get(url(mac)).json()
    assert AuditLog.objects.filter(action="nodes.audit.viewed").count() == 2
    assert all(row.get("action") != "nodes.audit.viewed" for row in second["results"])


def test_voice_mutes_and_tokens_of_the_device_are_listed(owner, personal, two, web_client):
    mac, desk = two
    record_audit(
        action="accounts.voice_live_mute.engaged",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=personal,
        metadata={"node_device_id": str(mac.pk), "reason": "stt_storm"},
    )
    record_audit(
        action="accounts.voice_live_mute.engaged",
        actor=None,
        actor_type=AuditLog.ActorType.SYSTEM,
        workspace=personal,
        metadata={"node_device_id": str(desk.pk), "reason": "stt_storm"},
    )
    rows = web_client(owner).get(url(mac)).json()["results"]
    mutes = [row for row in rows if row.get("action") == "accounts.voice_live_mute.engaged"]
    assert len(mutes) == 1 and mutes[0]["details"] == {"reason": "stt_storm"}
    assert mutes[0]["actor_type"] == "system"


def test_another_users_probe_and_another_workspace_are_not_listed(owner, two, web_client):
    from accounts.tests.node_voice_helpers import other_user

    mac, _ = two
    other, _ = other_user()
    other_ws = Workspace.objects.get(owner=other)
    services.record_actor_refusal(actor=other, node_id=mac.pk, capability="git.status")
    record_audit(
        action="nodes.refused",
        actor=other,
        workspace=other_ws,
        metadata={"node_id": str(mac.pk), "refusal_code": "actor_not_owner"},
    )
    rows = web_client(owner).get(url(mac)).json()["results"]
    assert [row.get("action") for row in rows] == ["nodes.node.paired", "accounts.node.enrolled"]


def test_chain_rows_of_a_workspace_the_actor_cannot_see_are_not_listed(owner, personal, two):
    mac, _ = two
    refuse(mac, personal)
    stranger = User.objects.create_user(email="s@test.dev")
    rows, _ = selectors.node_audit_page(stranger, mac)
    assert rows == []


def test_another_users_node_is_404(owner, two, web_client):
    from accounts.tests.node_voice_helpers import other_user
    from nodes.models import Node

    other, device = other_user()
    theirs = Node.objects.create(
        id=device.pk,
        device=device,
        owner=other,
        workspace=Workspace.objects.get(owner=other),
        name="Other",
        name_key="other",
        pinned_key_id="k",
    )
    assert web_client(owner).get(url(theirs)).status_code == 404
    assert not AuditLog.objects.filter(action="nodes.audit.viewed").exists()


@pytest.fixture
def principals(owner, personal, two, web_client):
    voice_pair = pair_node(raw_code=node_code(owner), node_name="Voice", protocol_versions=[1])
    voice = mint_token(voice_pair.node_secret)

    def make(principal):
        if principal == "owner-web":
            return web_client(owner)
        if principal == "kill-switched-owner":
            devices.engage_node_kill_switch(owner=owner, via="test")
            return web_client(owner)
        if principal.startswith("owner-pat-"):
            return bearer(make_pat(owner, personal, principal.removeprefix("owner-pat-"))[0])
        if principal == "node-secret":
            return bearer(voice_pair.node_secret)
        if principal == "node-voice":
            return bearer(voice)
        if principal == "non-owner":
            from accounts.services import register_user

            return web_client(register_user(email="member@test.dev", password="Str0ngDevPass!42"))
        if principal == "agent":
            return web_client(User.objects.create_user(email="agent@test.dev", is_agent=True))
        from rest_framework.test import APIClient

        return APIClient()

    return make


MATRIX = [
    ("owner-web", 200),
    ("kill-switched-owner", 200),
    ("owner-pat-" + PersonalAccessToken.Kind.CLI, (401, 403)),
    ("owner-pat-" + PersonalAccessToken.Kind.OPENAI, (401, 403)),
    ("owner-pat-" + PersonalAccessToken.Kind.HOOK, (401, 403)),
    ("node-secret", (401, 403)),
    ("node-voice", (401, 403)),
    ("non-owner", 403),
    ("agent", 403),
    ("anonymous", 401),
]


@pytest.mark.parametrize(("principal", "expected"), MATRIX)
def test_role_matrix(principals, personal, two, principal, expected):
    mac, _ = two
    refuse(mac, personal)
    response = principals(principal).get(url(mac))
    allowed = expected if isinstance(expected, tuple) else (expected,)
    assert response.status_code in allowed, (principal, response.status_code)
    if expected != 200:
        assert "trace-" not in response.content.decode()
        assert not AuditLog.objects.filter(action="nodes.audit.viewed").exists()


def test_small_pages_skip_and_repeat_nothing(owner, personal, two):
    """Chain rows and events interleave across page boundaries; every row
    appears exactly once (ordering by time, then id)."""
    mac, _ = two
    for _ in range(3):
        refuse(mac, personal)
        record_audit(
            action="nodes.refused",
            actor=owner,
            workspace=personal,
            metadata={"node_id": str(mac.pk), "refusal_code": "actor_not_owner"},
        )
    seen, before = [], None
    while True:
        page, cursor = selectors.node_audit_page(owner, mac, before=before, limit=2)
        seen += [row["id"] for row in page]
        if cursor is None:
            break
        before = selectors.decode_audit_cursor(cursor)
    assert len(seen) == len(set(seen)) == 8  # 3 chain rows + 3 refusals + paired + enrolled
