"""Phase 12 D2: the unattended context itself (no database)."""

import pytest

from core import unattended
from core.unattended import (
    UnattendedDenial,
    UnattendedDenied,
    deny_if_unattended,
    deny_purpose_if_unattended,
    deny_task_if_unattended,
    unattended_run,
    unattended_step,
)


def test_outside_a_run_every_check_is_a_no_op():
    assert unattended.current() is None
    deny_if_unattended("task_write")
    deny_purpose_if_unattended("chat")
    deny_task_if_unattended("memory.tasks.x", ())


def test_a_run_without_on_denied_is_a_type_error():
    with pytest.raises(TypeError):
        with unattended_run("r1", allowed_purposes=frozenset()):
            pass
    with pytest.raises(TypeError):
        with unattended_run("r1", allowed_purposes=frozenset(), on_denied=None):
            pass
    assert unattended.current() is None


def test_a_denial_calls_on_denied_first_then_raises():
    seen = []
    with unattended_run("run-1", allowed_purposes=frozenset(), on_denied=seen.append) as context:
        with unattended_step("tasks.due_today"):
            with pytest.raises(UnattendedDenied) as excinfo:
                deny_if_unattended("task_write")
        assert context.step == ""
    assert excinfo.value.effect == "task_write"
    assert seen == [UnattendedDenial(effect="task_write", run_id="run-1", step="tasks.due_today")]
    assert unattended.current() is None


def test_the_denial_is_not_an_exception_so_broad_handlers_cannot_swallow_it():
    def service():
        try:
            deny_if_unattended("connector_write")
        except Exception:  # noqa: BLE001 -- what a best-effort wrapper does
            return "swallowed"
        return "ran"

    with unattended_run("r", allowed_purposes=frozenset(), on_denied=lambda d: None):
        with pytest.raises(UnattendedDenied):
            service()
    assert not issubclass(UnattendedDenied, Exception)


def test_purposes_outside_the_allowlist_are_denied():
    seen = []
    with unattended_run(
        "r", allowed_purposes=frozenset({"proactive.digest"}), on_denied=seen.append
    ):
        deny_purpose_if_unattended("proactive.digest")
        with pytest.raises(UnattendedDenied):
            deny_purpose_if_unattended("chat")
    assert [d.effect for d in seen] == ["gateway_call"]


def test_tasks_outside_the_allowlist_are_denied():
    seen = []
    with unattended_run("r", allowed_purposes=frozenset(), on_denied=seen.append):
        deny_task_if_unattended("proactive.tasks.heartbeat", ("proactive.tasks.heartbeat",))
        with pytest.raises(UnattendedDenied):
            deny_task_if_unattended("memory.tasks.run", ("proactive.tasks.heartbeat",))
    assert [d.effect for d in seen] == ["celery_dispatch"]


def test_runs_do_not_nest():
    with unattended_run("outer", allowed_purposes=frozenset(), on_denied=lambda d: None):
        with pytest.raises(RuntimeError):
            with unattended_run("inner", allowed_purposes=frozenset(), on_denied=lambda d: None):
                pass
        assert unattended.current().run_id == "outer"


def test_the_context_is_reset_after_an_error():
    with pytest.raises(ValueError):
        with unattended_run("r", allowed_purposes=frozenset(), on_denied=lambda d: None):
            raise ValueError("boom")
    assert unattended.current() is None
