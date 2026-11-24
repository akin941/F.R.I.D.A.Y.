"""Where the pipeline's network calls run (Phase 11 brief D8).

The protocol loop never blocks on the network: every call to Friday is a
*job*, a generator that yields `Delay(ms)` markers, `Closer(fn)` markers
and event dicts. A worker runs the job and delivers each event to the
pipeline as `(job_id, event)`:

- `ThreadWorker` (the stdio child): one daemon thread per job; events are
  posted to the child's inbox, so the pipeline still runs on the protocol
  loop only. A `Delay` sleeps (only `FakeFriday` scripts one).
  `cancel()` marks the job dead and calls its closers (the chat stream's
  `close`), so a blocked read ends at once; late events are dropped.
- `SimWorker` (tests and `friday-voice replay`): a discrete-event
  scheduler on a `ManualClock`. A `Delay` schedules the job's next step at
  `now + ms`; `advance(ms)` / `run_until_idle()` move the clock to each due
  step in order. Several jobs (the chat stream and the speech calls) run
  "concurrently" in manual time, exactly reproducibly.

A job that raises delivers `{"kind": "error", "error": exc}` and ends.
"""

import itertools
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Delay:
    ms: int


@dataclass(frozen=True)
class Closer:
    close: object


def _failure(exc):
    return {"kind": "error", "error": exc}


class ThreadWorker:
    def __init__(self, post):
        self._post = post
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._live = {}  # job_id -> list of closers

    def submit(self, job) -> int:
        job_id = next(self._ids)
        with self._lock:
            self._live[job_id] = []
        threading.Thread(target=self._run, args=(job_id, job), daemon=True).start()
        return job_id

    def _alive(self, job_id):
        with self._lock:
            return job_id in self._live

    def _run(self, job_id, job):
        try:
            for item in job:
                if not self._alive(job_id):
                    break
                if isinstance(item, Delay):
                    if item.ms > 0:
                        time.sleep(item.ms / 1000)
                elif isinstance(item, Closer):
                    with self._lock:
                        if job_id in self._live:
                            self._live[job_id].append(item.close)
                else:
                    self._post(job_id, item)
        except Exception as exc:  # noqa: BLE001 -- every failure becomes an event
            if self._alive(job_id):
                self._post(job_id, _failure(exc))
        finally:
            job.close()
            with self._lock:
                self._live.pop(job_id, None)

    def cancel(self, job_id) -> None:
        with self._lock:
            closers = self._live.pop(job_id, [])
        for close in closers:
            try:
                close()
            except Exception:  # noqa: BLE001,S110 -- closing a dead stream never fails the child
                pass

    def busy(self) -> bool:
        with self._lock:
            return bool(self._live)


class SimWorker:
    """Discrete-event worker on a ManualClock (see the module docstring)."""

    def __init__(self, clock):
        self.clock = clock
        self.deliver = None  # set by the pipeline: deliver(job_id, event)
        self._ids = itertools.count(1)
        self._jobs = {}  # job_id -> [due_ms, order, generator]
        self._order = itertools.count()
        self.cancelled = []
        self.closed = []

    def submit(self, job) -> int:
        job_id = next(self._ids)
        self._jobs[job_id] = [self.clock.monotonic_ms(), next(self._order), job, []]
        return job_id

    def cancel(self, job_id) -> None:
        entry = self._jobs.pop(job_id, None)
        if entry is None:
            return
        self.cancelled.append(job_id)
        for close in entry[3]:
            close()
            self.closed.append(job_id)
        entry[2].close()

    def busy(self) -> bool:
        return bool(self._jobs)

    def next_due(self):
        if not self._jobs:
            return None
        return min(entry[0] for entry in self._jobs.values())

    def _step(self, job_id):
        entry = self._jobs[job_id]
        job = entry[2]
        while job_id in self._jobs:
            try:
                item = next(job)
            except StopIteration:
                self._jobs.pop(job_id, None)
                return
            except Exception as exc:  # noqa: BLE001 -- every failure becomes an event
                self._jobs.pop(job_id, None)
                self.deliver(job_id, _failure(exc))
                return
            if isinstance(item, Delay):
                if item.ms > 0:
                    entry[0] = self.clock.monotonic_ms() + item.ms
                    entry[1] = next(self._order)
                    return
            elif isinstance(item, Closer):
                entry[3].append(item.close)
            else:
                self.deliver(job_id, item)

    def run_due(self) -> bool:
        """Run every job step due now. Returns whether anything ran."""
        ran = False
        while True:
            now = self.clock.monotonic_ms()
            due = [
                (entry[0], entry[1], job_id)
                for job_id, entry in self._jobs.items()
                if entry[0] <= now
            ]
            if not due:
                return ran
            _due, _order, job_id = min(due)
            self._step(job_id)
            ran = True

    def _settle(self, on_tick):
        """Run what is due now and let the caller react (`on_tick`) until
        nothing more is due at this instant."""
        for _ in range(100_000):
            if not self.run_due():
                return
            if on_tick:
                on_tick()
        raise RuntimeError("jobs keep becoming due at the same instant")

    def advance(self, ms, on_tick=None) -> None:
        """Move the clock `ms` forward, running each job step at its due
        time. `on_tick()` runs after every clock move and after every batch
        of job steps (the child's tick and the host's turn)."""
        target = self.clock.monotonic_ms() + int(ms)
        self._settle(on_tick)
        while True:
            due = self.next_due()
            if due is None or due > target:
                break
            self.clock.advance(due - self.clock.monotonic_ms())
            if on_tick:
                on_tick()
            self._settle(on_tick)
        remaining = target - self.clock.monotonic_ms()
        if remaining > 0:
            self.clock.advance(remaining)
            if on_tick:
                on_tick()
        self._settle(on_tick)

    def run_until_idle(self, *, limit_ms=600_000, on_tick=None) -> None:
        start = self.clock.monotonic_ms()
        self.run_due()
        while self._jobs:
            due = self.next_due()
            if due - start > limit_ms:
                raise RuntimeError("a job never finished")
            self.advance(max(due - self.clock.monotonic_ms(), 0), on_tick=on_tick)
