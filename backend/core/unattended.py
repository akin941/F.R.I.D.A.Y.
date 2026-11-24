"""Unattended runs: deny by default (Phase 12 D2, ADR-016 decision 1).

A proactive run (an owner schedule firing while the owner is away), the
skill curator and the playbook distiller execute inside `unattended_run`.
While a context is set, every effect's owning service calls
`deny_if_unattended(effect)` on its first line -- approval requests,
gated dispatch, read-tool execution, connector reads and writes, task /
memory / fact writes, node invocations, harness jobs -- and the gateway
refuses any purpose outside the context's `allowed_purposes`. There is no
allow list of unattended actions: nothing switches one on.

Who writes the audit row: `core` may not import `audit`, so the guard
cannot. `unattended_run` takes a **required** `on_denied` callback
(`audit.unattended.DenialRecorder`); `deny_if_unattended` builds an
`UnattendedDenial` (no content), calls `on_denied(denial)` and only then
raises.

Why `UnattendedDenied` is a `BaseException`: a denial must end the step,
whatever the service it fires in does with ordinary errors. Several
services wrap best-effort work in `except Exception` (dispatch, cleanup),
and Celery's own signal dispatch swallows `Exception` from a handler, so
an `Exception` subclass raised from the `before_task_publish` guard would
be logged and the task published anyway. Like `asyncio.CancelledError`,
it is control flow that must not be swallowed; the runners catch it by
name around each step.

The context is a `ContextVar`: it does not cross a Celery hop, which is
why dispatch itself is guarded (`proactive.apps`).

Imports only the standard library.
"""

import contextvars
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UnattendedDenial:
    """What a denial records: the effect, the run and the step (a probe or
    step name). Never content."""

    effect: str
    run_id: str
    step: str = ""


class UnattendedDenied(BaseException):  # noqa: N818 -- the brief's name
    """Raised by `deny_if_unattended`. See the module docstring for why it
    is a `BaseException`."""

    def __init__(self, effect):
        self.effect = effect
        super().__init__(effect)


@dataclass
class UnattendedContext:
    run_id: str
    allowed_purposes: frozenset
    on_denied: Callable[[UnattendedDenial], None]
    step: str = ""
    denials: list = field(default_factory=list)


_current: contextvars.ContextVar = contextvars.ContextVar("friday_unattended_run", default=None)


@contextmanager
def unattended_run(
    run_id: str,
    *,
    allowed_purposes: frozenset,
    on_denied: Callable[[UnattendedDenial], None],
):
    """Open an unattended context. `on_denied` is required (keyword-only,
    no default: omitting it is a `TypeError`, so no run can be opened
    without a recorder). Nested contexts are refused."""
    if not callable(on_denied):
        raise TypeError("unattended_run needs a callable on_denied recorder.")
    if _current.get() is not None:
        raise RuntimeError("An unattended run is already open in this context.")
    context = UnattendedContext(
        run_id=str(run_id),
        allowed_purposes=frozenset(allowed_purposes),
        on_denied=on_denied,
    )
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def current() -> UnattendedContext | None:
    return _current.get()


@contextmanager
def unattended_step(name: str):
    """Name the current step (a probe) for denials raised inside it."""
    context = _current.get()
    if context is None:
        yield
        return
    previous = context.step
    context.step = str(name)
    try:
        yield
    finally:
        context.step = previous


def deny_if_unattended(effect: str) -> None:
    """No-op outside a run. Inside one: record the denial through the
    run's `on_denied`, then raise `UnattendedDenied(effect)`."""
    context = _current.get()
    if context is None:
        return
    denial = UnattendedDenial(effect=str(effect), run_id=context.run_id, step=context.step)
    context.denials.append(denial)
    context.on_denied(denial)
    raise UnattendedDenied(effect)


def deny_purpose_if_unattended(purpose: str) -> None:
    """The gateway's check: inside a run, a purpose outside the context's
    `allowed_purposes` is denied (effect `gateway_call`)."""
    context = _current.get()
    if context is None or purpose in context.allowed_purposes:
        return
    deny_if_unattended("gateway_call")


def deny_task_if_unattended(task_name: str, allowed_tasks) -> None:
    """The Celery dispatch check: inside a run, a task outside
    `allowed_tasks` is denied (effect `celery_dispatch`)."""
    context = _current.get()
    if context is None or task_name in frozenset(allowed_tasks or ()):
        return
    deny_if_unattended("celery_dispatch")
