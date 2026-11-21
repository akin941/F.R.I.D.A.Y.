"""The confirmation-window double and the node clock (Phase 10 brief D5
check 23, ADR-013 decision 6).

On the Mac the window is a non-activating floating `NSPanel` the node
renders from its **own** validated args (Mac session M1). Here it is a
double with the same rules:

- it shows one invocation at a time (a second gated or confirm invoke while
  one is open is refused `busy_confirming`);
- the text is rendered by the node from the capability's `confirm_template`
  with `{arg}` slots filled from the node's validated args, never from
  server text; a gate-class window also shows the approval id's first 8
  characters, and `repo.command.run` the repo's local name, the exact argv
  and whether network is enabled;
- the owner is **scripted**: a queue of decisions (`approve`, `deny`,
  `close`) each taking effect `after` seconds on the node clock; with no
  decision the window times out after 60 s;
- it closes, with no decision, on disconnect or the kill switch.
"""

from dataclasses import dataclass, field

from nodes.capabilities import template_slots
from nodes.protocol.messages import CONFIRM_WINDOW_SECONDS

DECISIONS = ("approve", "deny", "close")
_STATE_FOR = {"approve": "approved", "deny": "denied", "close": "closed"}


class FakeClock:
    """Integer Unix seconds, advanced only by the test (or the conformance
    hook)."""

    def __init__(self, now: int):
        self._now = int(now)

    def now(self) -> int:
        return self._now

    def advance(self, seconds: int) -> int:
        if seconds < 0:
            raise ValueError("the clock never goes back")
        self._now += int(seconds)
        return self._now

    def set(self, now: int) -> None:
        self._now = int(now)


class WindowBusy(Exception):
    code = "busy_confirming"


@dataclass(frozen=True)
class Decision:
    action: str
    after: int = 0

    def __post_init__(self):
        if self.action not in DECISIONS:
            raise ValueError(f"unknown decision {self.action!r}")
        if isinstance(self.after, bool) or not isinstance(self.after, int) or self.after < 0:
            raise ValueError("after is a non-negative integer of seconds")

    @classmethod
    def from_document(cls, body):
        return cls(body["action"], body.get("after", 0))


def render_confirmation(contract, args, *, recipe=None, approval_id=None, resolved=None):
    """The window text, from the node's own contract and validated args."""
    template = recipe.confirm_template if recipe is not None else contract.confirm_template
    values = (args.get("params") or {}) if recipe is not None else args
    text = template
    for slot in template_slots(template):
        text = text.replace("{" + slot + "}", str(values.get(slot, "")))
    lines = [text]
    if recipe is not None:
        lines.append(f"Tarif: {recipe.label}")
    if resolved is not None and resolved.repo is not None:
        lines.append(f"Repo: {resolved.repo.name}")
    if resolved is not None and resolved.command is not None:
        lines.append("Komut: " + " ".join(resolved.command.argv))
        domains = resolved.command.network_domains
        lines.append("Ağ: " + (", ".join(domains) if domains else "kapalı"))
    if approval_id:
        lines.append(f"Onay: {approval_id[:8]}")
    return "\n".join(lines)


@dataclass
class _Open:
    invocation_id: str
    text: str
    shown_at: int
    decision: Decision | None
    decided_at: int | None = None


@dataclass
class ConfirmationWindowDouble:
    """One window at a time; scripted owner; 60 s timeout on `clock`."""

    clock: FakeClock
    script: list = field(default_factory=list)
    timeout: int = CONFIRM_WINDOW_SECONDS

    def __post_init__(self):
        self.script = [
            d if isinstance(d, Decision) else Decision.from_document(d) for d in self.script
        ]
        self._open: _Open | None = None
        self.shown: list = []  # every text shown, in order (tests)
        self.closed_by: list = []  # "disconnect" / "kill_switch" per forced close

    @property
    def is_open(self) -> bool:
        return self._open is not None

    @property
    def invocation_id(self):
        return self._open.invocation_id if self._open else None

    def open(self, invocation_id: str, text: str) -> None:
        if self._open is not None:
            raise WindowBusy()
        decision = self.script.pop(0) if self.script else None
        now = self.clock.now()
        self._open = _Open(invocation_id, text, now, decision)
        if decision is not None:
            self._open.decided_at = now + decision.after
        self.shown.append(text)

    def poll(self):
        """The window's outcome once due (`approved`, `denied`, `closed`,
        `timed_out`), closing it; None while it is still open and
        undecided."""
        if self._open is None:
            return None
        now = self.clock.now()
        window = self._open
        deadline = window.shown_at + self.timeout
        if window.decided_at is not None and window.decided_at < deadline:
            if now >= window.decided_at:
                self._open = None
                return _STATE_FOR[window.decision.action]
            return None
        if now >= deadline:
            self._open = None
            return "timed_out"
        return None

    def force_close(self, reason: str) -> None:
        """Close with no decision (socket loss, kill switch)."""
        if self._open is not None:
            self._open = None
            self.closed_by.append(reason)
