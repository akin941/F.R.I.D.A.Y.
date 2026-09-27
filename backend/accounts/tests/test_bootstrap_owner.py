"""`bootstrap_owner` (phase 0, ADR-008): the one way to create the single
owner account now that signup is off. Refuses to run on an installation
that already has any user -- including under parallel invocation (the
advisory-lock critical section, Codex phase-0 code-review P1)."""

import threading

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from accounts.models import User
from audit.models import AuditLog
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db


def test_creates_owner_with_personal_workspace(monkeypatch):
    monkeypatch.setenv("JARVIS_OWNER_PASSWORD", "s3cure-enough")
    call_command("bootstrap_owner", "--email", "owner@test.dev", "--name", "Owner")

    user = User.objects.get(email="owner@test.dev")
    assert user.check_password("s3cure-enough")
    # The password's only use is the Django-admin break-glass (Q3).
    assert user.is_owner and user.is_staff
    # Same register_user() path signup used: personal workspace + audit row.
    membership = WorkspaceMember.objects.get(user=user)
    assert membership.workspace.type == Workspace.Type.PERSONAL
    assert AuditLog.objects.filter(action="user.signup").exists()


def test_refuses_if_any_user_exists(monkeypatch):
    monkeypatch.setenv("JARVIS_OWNER_PASSWORD", "s3cure-enough")
    User.objects.create_user(email="existing@test.dev", password="x")
    with pytest.raises(CommandError, match="already exists"):
        call_command("bootstrap_owner", "--email", "owner@test.dev")
    assert not User.objects.filter(email="owner@test.dev").exists()


def test_refuses_blank_password(monkeypatch):
    monkeypatch.setenv("JARVIS_OWNER_PASSWORD", "   ")
    with pytest.raises(CommandError, match="password"):
        call_command("bootstrap_owner", "--email", "owner@test.dev")
    assert not User.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_parallel_bootstrap_creates_exactly_one_owner(monkeypatch):
    """Genuine two-connection race (mirrors approvals' concurrency test):
    two bootstrap runs with different emails hit the advisory-lock critical
    section at once. Exactly one may create the owner; the loser must see
    the winner's committed user and refuse -- never two owners."""
    monkeypatch.setenv("JARVIS_OWNER_PASSWORD", "s3cure-enough")
    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(key, email):
        try:
            barrier.wait(timeout=5)
            try:
                call_command("bootstrap_owner", "--email", email)
                results[key] = "created"
            except CommandError:
                results[key] = "refused"
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker, args=("a", "owner-a@test.dev"))
    t2 = threading.Thread(target=worker, args=("b", "owner-b@test.dev"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert sorted(results.values()) == ["created", "refused"]
    assert User.objects.count() == 1
