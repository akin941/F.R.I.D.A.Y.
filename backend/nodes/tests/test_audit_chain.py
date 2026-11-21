"""The hash-chained `NodeInvocation` audit (Phase 9 brief D8): contiguous
appends under concurrency, the append-only trigger, the PROTECT deletion
policy, tamper detection by `verify_node_audit_chain`, and the writer's
vocabulary checks."""

import io
import threading
import uuid

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.models import ProtectedError

from accounts.models import User
from nodes import audit_chain
from nodes.audit_chain import ChainWriteError, append, compute_row_hash, verify_chain
from nodes.models import GENESIS_HASH, TERMINAL_VOCABULARY, NodeAuditHead, NodeInvocation
from nodes.protocol import sha256_hex
from workspaces.models import Workspace, WorkspaceMember

ARGS_HASH = sha256_hex({"repo_path": "~/work/acme"})


def _append(node, workspace, actor=None, **overrides):
    fields = {
        "workspace": workspace,
        "node": node,
        "capability": "git.status",
        "capability_version": 1,
        "args_sha256": ARGS_HASH,
        "status": NodeInvocation.Status.OK,
        "channel": "web",
        "trace_id": "trace-1",
        "actor": actor,
        "nonce": "A" * 43,
        "output_sha256": sha256_hex({"branch": "main"}),
        "output_bytes": 17,
        "duration_ms": 12,
    }
    fields.update(overrides)
    return append(**fields)


# --- append ---


@pytest.mark.django_db
def test_genesis_and_links(owner, personal, make_node):
    node = make_node().node
    first = _append(node, personal, actor=owner)
    second = _append(
        node,
        personal,
        status=NodeInvocation.Status.NODE_OFFLINE,
        nonce="",
        output_sha256="",
        output_bytes=0,
    )
    assert (first.seq, second.seq) == (1, 2)
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.row_hash
    assert first.row_hash == compute_row_hash(first)
    head = NodeAuditHead.objects.get()
    assert (head.last_seq, head.last_hash) == (2, second.row_hash)
    assert verify_chain().ok


@pytest.mark.django_db
def test_the_hash_covers_every_field_and_fks_as_ids(owner, personal, make_node):
    row = _append(make_node().node, personal, actor=owner)
    payload = audit_chain.hash_payload(row)
    assert set(payload) == set(audit_chain.HASHED_FIELDS)
    assert payload["workspace_id"] == str(personal.pk)
    assert payload["node_id"] == str(row.node_id)
    assert payload["actor_id"] == str(owner.pk)
    assert payload["created_at"].endswith("Z")
    stored = NodeInvocation.objects.get(pk=row.pk)  # round trip through Postgres
    assert compute_row_hash(stored) == row.row_hash
    model_fields = {f.attname for f in NodeInvocation._meta.concrete_fields}
    assert model_fields - set(audit_chain.HASHED_FIELDS) == {"row_hash"}


@pytest.mark.django_db
def test_a_system_call_has_a_null_actor(personal, make_node):
    row = _append(make_node().node, personal, actor=None, channel="system")
    assert audit_chain.hash_payload(row)["actor_id"] is None
    assert verify_chain().ok


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "exploded"}, "unknown status"),
        ({"status": "ok", "refusal_code": "replay"}, "refusal code"),
        ({"status": "refused", "refusal_code": ""}, "refusal code"),
        ({"status": "refused", "refusal_code": "args_contract"}, "refusal code"),
        ({"status": "server_refused", "refusal_code": "not_invocable"}, "not-sent"),
        ({"args_sha256": "nothex"}, "args_sha256"),
        ({"output_sha256": "abc"}, "output_sha256"),
        ({"trace_id": ""}, "trace_id"),
    ],
)
def test_the_writer_refuses_values_outside_the_vocabulary(personal, make_node, overrides, message):
    node = make_node().node
    with pytest.raises(ChainWriteError, match=message):
        _append(node, personal, **overrides)
    assert not NodeInvocation.objects.exists()


@pytest.mark.django_db
def test_every_vocabulary_pair_appends(personal, make_node):
    node = make_node().node
    for status, codes in TERMINAL_VOCABULARY.items():
        for code in sorted(codes):
            not_sent = status in audit_chain.NOT_SENT_STATUSES
            _append(
                node,
                personal,
                status=status,
                refusal_code=code,
                nonce="" if not_sent else "B" * 43,
                output_sha256="",
                output_bytes=0,
            )
    assert verify_chain().ok
    assert verify_chain().rows == sum(len(codes) for codes in TERMINAL_VOCABULARY.values())


@pytest.mark.django_db(transaction=True)
def test_append_under_concurrency_is_contiguous(owner, personal, make_node):
    node = make_node().node
    per_thread, errors = 15, []

    def worker():
        try:
            for _ in range(per_thread):
                _append(node, personal)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    close_old_connections()
    assert errors == []
    seqs = list(NodeInvocation.objects.order_by("seq").values_list("seq", flat=True))
    assert seqs == list(range(1, 2 * per_thread + 1))
    result = verify_chain()
    assert result.ok and result.rows == 2 * per_thread


@pytest.mark.django_db(transaction=True)
def test_a_flushed_head_restarts_from_the_tip(owner, personal, make_node):
    # A transactional test database is flushed (the migration's seeded head
    # row is gone): the first append re-creates it, and never restarts seq.
    NodeAuditHead.objects.all().delete()
    node = make_node().node
    first = _append(node, personal)
    assert first.seq == 1 and first.prev_hash == GENESIS_HASH
    NodeAuditHead.objects.all().delete()
    second = _append(node, personal)
    assert second.seq == 2 and second.prev_hash == first.row_hash
    assert verify_chain().ok


# --- append-only trigger ---


@pytest.mark.django_db
def test_update_and_delete_are_refused_by_the_trigger(owner, personal, make_node):
    row = _append(make_node().node, personal, actor=owner)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        NodeInvocation.objects.filter(pk=row.pk).update(duration_ms=1)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        NodeInvocation.objects.filter(pk=row.pk).delete()
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE nodes_nodeinvocation SET actor_id = NULL WHERE id = %s", [row.pk]
            )
    stored = NodeInvocation.objects.get(pk=row.pk)
    assert (stored.duration_ms, stored.actor_id) == (12, owner.pk)
    assert verify_chain().ok


# --- deletion policy (no SET_NULL, no CASCADE) ---


@pytest.fixture
def chain_with_firm_row(db, owner, personal, make_node):
    firm = Workspace.objects.create(name="Bare", type=Workspace.Type.COMPANY, owner=owner)
    actor = User.objects.create_user(email="actor@test.dev")
    WorkspaceMember.objects.create(workspace=firm, user=actor, role=WorkspaceMember.Role.MEMBER)
    node = make_node().node
    rows = [_append(node, personal, actor=owner), _append(node, firm, actor=actor)]
    return node, firm, actor, rows


def _snapshot():
    return list(NodeInvocation.objects.order_by("seq").values())


@pytest.mark.django_db
def test_deleting_a_referenced_workspace_node_device_or_actor_is_protected(chain_with_firm_row):
    node, firm, actor, _ = chain_with_firm_row
    before = _snapshot()
    for target in (firm, node, node.device, actor):
        with pytest.raises(ProtectedError) as exc, transaction.atomic():
            target.delete()
        protected = {type(obj) for obj in exc.value.protected_objects}
        assert protected & {NodeInvocation, type(node)}, target
    assert _snapshot() == before
    out = io.StringIO()
    call_command("verify_node_audit_chain", stdout=out)
    assert "OK: 2 row(s)" in out.getvalue()


@pytest.mark.django_db
def test_raw_sql_workspace_delete_is_refused_by_the_fk(chain_with_firm_row):
    _, firm, _, _ = chain_with_firm_row
    # The member row would also block it; remove that first so only the
    # chain row references the workspace.
    WorkspaceMember.objects.filter(workspace=firm).delete()
    with pytest.raises(IntegrityError, match="nodes_nodeinvocation"), transaction.atomic():
        with connection.cursor() as cursor:
            # Django's FK constraints are DEFERRABLE INITIALLY DEFERRED:
            # check them now instead of at commit.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("DELETE FROM workspaces_workspace WHERE id = %s", [firm.pk])
    assert Workspace.objects.filter(pk=firm.pk).exists()
    assert verify_chain().ok


# --- verification ---


def _tamper(sql, params):
    with connection.cursor() as cursor:
        # Flush the deferred FK checks of this test's inserts first: ALTER
        # TABLE refuses to run with pending trigger events.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE nodes_nodeinvocation DISABLE TRIGGER USER")
        try:
            cursor.execute(sql, params)
        finally:
            cursor.execute("ALTER TABLE nodes_nodeinvocation ENABLE TRIGGER USER")


@pytest.mark.django_db
def test_verify_detects_a_tampered_row_and_reports_its_seq(personal, make_node):
    node = make_node().node
    rows = [_append(node, personal) for _ in range(4)]
    _tamper("UPDATE nodes_nodeinvocation SET status = 'refused' WHERE id = %s", [rows[2].pk])
    result = verify_chain()
    assert (result.ok, result.broken_seq, result.reason) == (False, 3, "row_hash_mismatch")
    with pytest.raises(CommandError, match="broken at seq 3"):
        call_command("verify_node_audit_chain")


@pytest.mark.django_db
def test_verify_detects_a_relinked_row(personal, make_node):
    node = make_node().node
    rows = [_append(node, personal) for _ in range(3)]
    _tamper(
        "UPDATE nodes_nodeinvocation SET prev_hash = %s WHERE id = %s",
        [GENESIS_HASH, rows[1].pk],
    )
    result = verify_chain()
    assert (result.ok, result.broken_seq, result.reason) == (False, 2, "prev_hash_mismatch")


@pytest.mark.django_db
def test_verify_detects_a_deleted_row(personal, make_node):
    node = make_node().node
    rows = [_append(node, personal) for _ in range(3)]
    _tamper("DELETE FROM nodes_nodeinvocation WHERE id = %s", [rows[1].pk])
    result = verify_chain()
    assert (result.ok, result.broken_seq, result.reason) == (False, 2, "seq_gap")


@pytest.mark.django_db
def test_verify_detects_a_truncated_tail(personal, make_node):
    node = make_node().node
    rows = [_append(node, personal) for _ in range(3)]
    _tamper("DELETE FROM nodes_nodeinvocation WHERE id = %s", [rows[2].pk])
    result = verify_chain()
    assert (result.ok, result.reason) == (False, "head_mismatch")


@pytest.mark.django_db
def test_an_empty_chain_verifies(db):
    out = io.StringIO()
    call_command("verify_node_audit_chain", stdout=out)
    assert "OK: 0 row(s)" in out.getvalue()


@pytest.mark.django_db
def test_rows_hold_no_content(owner, personal, make_node):
    secret_arg = "~/work/acme-" + uuid.uuid4().hex
    row = _append(make_node().node, personal, args_sha256=sha256_hex({"repo_path": secret_arg}))
    stored = NodeInvocation.objects.filter(pk=row.pk).values().get()
    values = " ".join(str(v) for v in stored.values())
    assert secret_arg not in values
