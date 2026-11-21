"""Capability snapshots by class and protocol version (Phase 10 brief D1,
D3): a confirm or gate entry from a v1 session is
`not_invocable:protocol_v1`; on v2 it is invocable only with matching
class and schema hashes; a reconnect on another version with the same
catalogue writes a fresh snapshot."""

import pytest

from nodes.capabilities import get_action_contract, get_contract, load_action_contracts
from nodes.models import CapabilitySnapshot
from nodes.protocol.messages import catalogue_sha256
from nodes.services import (
    UnsupportedProtocolVersion,
    classify_catalogue,
    invocable_entry,
    record_capability_snapshot,
    snapshot_protocol_version,
)

pytestmark = pytest.mark.django_db


def _catalogue():
    entries = [get_contract("git.status").catalogue_entry()]
    entries += [contract.catalogue_entry() for contract in load_action_contracts().values()]
    return entries


def _by_name(entries):
    return {entry["name"]: entry for entry in entries}


def test_a_v1_session_marks_every_action_entry_protocol_v1():
    entries = _by_name(classify_catalogue(_catalogue(), protocol_version=1))
    assert entries["git.status"]["invocable"] is True
    assert entries["git.status"]["reason"] is None
    for name in load_action_contracts():
        assert entries[name]["invocable"] is False
        assert entries[name]["reason"] == "protocol_v1"
        assert entries[name]["protocol_version"] == 1


def test_a_v2_session_makes_matching_action_entries_invocable():
    entries = _by_name(classify_catalogue(_catalogue(), protocol_version=2))
    for name in [*load_action_contracts(), "git.status"]:
        assert entries[name]["invocable"] is True, name
        assert entries[name]["protocol_version"] == 2


def test_class_and_schema_hash_mismatches_are_not_invocable_with_a_reason():
    open_as_gate = {**get_action_contract("apps.open").catalogue_entry(), "class": "gate"}
    wider = get_action_contract("ax.press").catalogue_entry()
    wider["args_schema"]["properties"]["title"]["maxLength"] = 10_000
    junk_schema = {**get_action_contract("apps.focus").catalogue_entry(), "args_schema": {"a": 1.5}}
    entries = _by_name(classify_catalogue([open_as_gate, wider, junk_schema], protocol_version=2))
    assert entries["apps.open"]["reason"] == "class_mismatch"
    assert entries["ax.press"]["reason"] == "args_schema_mismatch"
    assert entries["apps.focus"]["reason"] == "args_schema_mismatch"
    assert entries["apps.focus"]["args_schema_sha256"] is None
    assert not any(entry["invocable"] for entry in entries.values())


def test_entries_never_store_schema_content():
    for entry in classify_catalogue(_catalogue(), protocol_version=2):
        assert set(entry) == {
            "name",
            "version",
            "class",
            "availability",
            "args_schema_sha256",
            "result_schema_sha256",
            "invocable",
            "reason",
            "protocol_version",
        }


def test_unknown_protocol_versions_are_refused():
    with pytest.raises(UnsupportedProtocolVersion):
        classify_catalogue(_catalogue(), protocol_version=3)


def test_a_reconnect_on_another_version_writes_a_fresh_snapshot(make_node):
    node = make_node().node
    catalogue = _catalogue()
    sha = catalogue_sha256(catalogue)
    first = record_capability_snapshot(
        node=node, catalogue=catalogue, catalogue_sha256=sha, protocol_version=1
    )
    assert snapshot_protocol_version(first) == 1
    again = record_capability_snapshot(
        node=node, catalogue=catalogue, catalogue_sha256=sha, protocol_version=1
    )
    assert again.pk == first.pk
    upgraded = record_capability_snapshot(
        node=node, catalogue=catalogue, catalogue_sha256=sha, protocol_version=2
    )
    assert upgraded.pk != first.pk
    assert snapshot_protocol_version(upgraded) == 2
    assert upgraded.workspace_id == node.workspace_id
    assert CapabilitySnapshot.objects.filter(node=node).count() == 2
    downgraded = record_capability_snapshot(
        node=node, catalogue=catalogue, catalogue_sha256=sha, protocol_version=1
    )
    assert downgraded.pk not in (first.pk, upgraded.pk)
    run = get_action_contract("repo.command.run")
    assert invocable_entry(upgraded, run) is not None
    assert invocable_entry(downgraded, run) is None


def test_a_changed_catalogue_writes_a_fresh_snapshot(make_node):
    node = make_node().node
    catalogue = _catalogue()
    first = record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=2,
    )
    smaller = catalogue[:2]
    second = record_capability_snapshot(
        node=node,
        catalogue=smaller,
        catalogue_sha256=catalogue_sha256(smaller),
        protocol_version=2,
    )
    assert second.pk != first.pk
    empty = record_capability_snapshot(
        node=node, catalogue=[], catalogue_sha256=catalogue_sha256([]), protocol_version=2
    )
    assert snapshot_protocol_version(empty) is None
    assert (
        record_capability_snapshot(
            node=node, catalogue=[], catalogue_sha256=catalogue_sha256([]), protocol_version=1
        ).pk
        == empty.pk
    )


def test_invocable_entry_requires_name_version_class_and_both_hashes(make_node):
    node = make_node().node
    catalogue = _catalogue()
    snapshot = record_capability_snapshot(
        node=node,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256(catalogue),
        protocol_version=2,
    )
    run = get_action_contract("repo.command.run")
    assert invocable_entry(snapshot, run)["name"] == "repo.command.run"
    assert invocable_entry(None, run) is None
    for change in (
        {"class": "confirm"},
        {"version": 2},
        {"args_schema_sha256": "0" * 64},
        {"result_schema_sha256": "0" * 64},
        {"invocable": False},
        {"protocol_version": 1},
    ):
        tampered = CapabilitySnapshot(
            workspace=node.workspace,
            node=node,
            catalogue_sha256="0" * 64,
            entries=[{**invocable_entry(snapshot, run), **change}],
        )
        assert invocable_entry(tampered, run) is None, change
    git = get_contract("git.status")
    assert invocable_entry(snapshot, git) is not None
