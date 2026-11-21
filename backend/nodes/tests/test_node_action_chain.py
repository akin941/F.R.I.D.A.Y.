"""Chain rows v2 and the closed terminal vocabulary (Phase 10 brief D7)."""

import uuid

import pytest

from nodes import audit_chain
from nodes.audit_chain import ChainWriteError, compute_row_hash, verify_chain
from nodes.models import (
    EXECUTION_TERMINAL_STATUSES,
    NOT_SENT_STATUSES,
    TERMINAL_VOCABULARY,
    NodeActionExecution,
    NodeInvocation,
)
from nodes.protocol.messages import NODE_REFUSAL_CODES_V2

ARGS_HASH = "a" * 64


def _append(node, workspace, **overrides):
    fields = {
        "workspace": workspace,
        "node": node,
        "capability": "repo.command.run",
        "capability_version": 1,
        "args_sha256": ARGS_HASH,
        "status": "ok",
        "channel": "web",
        "trace_id": uuid.uuid4().hex,
        "nonce": "n" * 43,
        "invocation_class": "gate",
        "approval_id": uuid.uuid4(),
        "grant_id": uuid.uuid4(),
        "exit_code": 0,
        "confirm_state": "approved",
    }
    fields.update(overrides)
    return audit_chain.append(**fields)


@pytest.mark.django_db
def test_an_action_row_is_hash_version_2_and_hashes_its_new_fields(personal, make_node):
    node = make_node().node
    read = audit_chain.append(
        workspace=personal,
        node=node,
        capability="git.status",
        capability_version=1,
        args_sha256=ARGS_HASH,
        status="ok",
        channel="web",
        trace_id="t1",
    )
    action = _append(node, personal)
    assert (read.hash_version, action.hash_version) == (1, 2)
    payload = audit_chain.hash_payload(action)
    assert set(payload) == set(audit_chain.HASHED_FIELDS_V2)
    assert payload["approval_id"] == str(action.approval_id)
    assert set(audit_chain.hash_payload(read)) == set(audit_chain.HASHED_FIELDS)
    stored = NodeInvocation.objects.get(pk=action.pk)
    assert compute_row_hash(stored) == action.row_hash
    assert verify_chain().ok


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["exit_code", "confirm_state", "grant_id", "approval_id"])
def test_tampering_with_a_v2_field_breaks_the_hash(personal, make_node, field):
    row = _append(make_node().node, personal)
    original = row.row_hash
    setattr(
        row,
        field,
        {"exit_code": 1, "confirm_state": "denied"}.get(field, uuid.uuid4()),
    )
    assert compute_row_hash(row) != original


@pytest.mark.django_db
def test_an_unknown_hash_version_never_verifies(personal, make_node):
    row = _append(make_node().node, personal)
    row.hash_version = 9
    assert compute_row_hash(row) == ""


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"invocation_class": "read"}, "unknown invocation class"),
        ({"confirm_state": "maybe"}, "unknown confirm state"),
        ({"invocation_class": "", "exit_code": 0}, "action fields on a read row"),
        ({"status": "denied", "refusal_code": "replay"}, "refusal code not allowed"),
        ({"status": "stale_dispatch", "nonce": "n" * 43}, "a not-sent call"),
    ],
)
def test_the_chain_refuses_values_outside_its_vocabulary(personal, make_node, overrides, message):
    if overrides.get("invocation_class") == "":
        overrides = {**overrides, "approval_id": None, "grant_id": None, "confirm_state": ""}
    with pytest.raises(ChainWriteError, match=message):
        _append(make_node().node, personal, **overrides)


def test_the_closed_terminal_vocabulary_is_pinned():
    """D7: the whole (status, refusal_code) set, Phase 9 plus Phase 10."""
    action = {
        "failed": {"", "node_error"},
        "denied": {"owner_denied"},
        "confirm_timeout": {"confirm_timeout"},
        "stale_dispatch": {""},
    }
    for status, codes in action.items():
        assert TERMINAL_VOCABULARY[status] == frozenset(codes)
    assert {
        "actor_not_owner",
        "approval_not_approved",
        "wrong_tool",
        "args_changed",
        "args_contract",
        "not_invocable",
        "kill_switch",
        "path_not_in_workspace",
        "workspace_not_allowed",
        "signing_unavailable",
        "node_mismatch",
        "bus_unavailable",
        "session_not_found",
    } == set(TERMINAL_VOCABULARY["server_refused"])
    node_codes = set(NODE_REFUSAL_CODES_V2) - {
        "too_large",
        "malformed",
        "unknown_type",
        "schema",
        "owner_denied",
        "confirm_timeout",
    }
    assert node_codes <= set(TERMINAL_VOCABULARY["refused"])
    assert "stale_dispatch" in NOT_SENT_STATUSES
    terminal = {s for s in NodeActionExecution.Status.values} - {
        "approved",
        "granted",
        "sent",
        "confirming",
        "running",
    }
    assert terminal == set(EXECUTION_TERMINAL_STATUSES)
    assert terminal <= set(TERMINAL_VOCABULARY)
