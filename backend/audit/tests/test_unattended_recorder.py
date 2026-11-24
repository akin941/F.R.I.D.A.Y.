"""Phase 12 D2 ("Who writes the audit row"): the denial recorder buffers,
flushes once, and survives a rolled-back caller transaction."""

import pytest
from django.db import transaction

from audit.models import AuditLog
from audit.unattended import DENIAL_ACTION, DenialRecorder, recorded_unattended_run
from core.unattended import UnattendedDenied, deny_if_unattended, unattended_run

pytestmark = pytest.mark.django_db


def _rows():
    return list(AuditLog.objects.filter(action=DENIAL_ACTION))


def test_calling_the_recorder_buffers_without_writing():
    recorder = DenialRecorder(source="test")
    with unattended_run("r1", allowed_purposes=frozenset(), on_denied=recorder):
        with pytest.raises(UnattendedDenied):
            deny_if_unattended("task_write")
    assert recorder.pending == 1
    assert _rows() == []
    assert recorder.flush() == 1
    assert recorder.flush() == 0
    [row] = _rows()
    assert row.metadata == {"effect": "task_write", "run_id": "r1", "step": "", "source": "test"}
    assert row.actor is None and row.actor_type == AuditLog.ActorType.SYSTEM


def test_a_denial_inside_a_rolled_back_block_is_still_audited_once():
    recorder = DenialRecorder()
    with unattended_run("r2", allowed_purposes=frozenset(), on_denied=recorder):
        with pytest.raises(UnattendedDenied):
            with transaction.atomic():
                AuditLog.objects.create(action="probe.side_effect")
                deny_if_unattended("memory_write")
        recorder.flush()
    assert not AuditLog.objects.filter(action="probe.side_effect").exists()
    assert len(_rows()) == 1


def test_recorded_unattended_run_flushes_on_exit_even_after_a_denial():
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("r3") as recorder:
            deny_if_unattended("approval_request")
    assert recorder.pending == 0
    assert [row.metadata["effect"] for row in _rows()] == ["approval_request"]


def test_the_recorder_stamps_the_workspace(db):
    from accounts.services import register_user
    from workspaces.models import Workspace

    owner = register_user(email="o@test.dev", password="Str0ngDevPass!42", name="O")
    workspace = Workspace.objects.get(owner=owner)
    with pytest.raises(UnattendedDenied):
        with recorded_unattended_run("r4", workspace=workspace, source="proactive.heartbeat"):
            deny_if_unattended("harness_job")
    [row] = _rows()
    assert row.workspace == workspace
    assert row.metadata["source"] == "proactive.heartbeat"


def test_a_failed_write_keeps_the_unwritten_denials_buffered(monkeypatch):
    """A write that raises mid-flush loses nothing: the failed denial and
    the tail stay buffered, and the next flush writes each exactly once."""
    from audit import unattended

    recorder = DenialRecorder(source="test")
    with unattended_run("r5", allowed_purposes=frozenset(), on_denied=recorder):
        for effect in ("task_write", "memory_write", "harness_job"):
            with pytest.raises(UnattendedDenied):
                deny_if_unattended(effect)
    real = unattended.record_audit
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("audit store hiccup")
        return real(**kwargs)

    monkeypatch.setattr(unattended, "record_audit", flaky)
    with pytest.raises(RuntimeError):
        recorder.flush()
    assert recorder.pending == 2
    assert recorder.flush() == 2
    assert recorder.pending == 0
    assert sorted(row.metadata["effect"] for row in _rows()) == [
        "harness_job",
        "memory_write",
        "task_write",
    ]
