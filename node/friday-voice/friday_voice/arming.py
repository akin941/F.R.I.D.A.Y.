"""The always-on arming policy (Phase 11 brief D11, roadmap d3).

The child arms wake mode only when ALL of these hold:

(a) the host's local mode is `always_on` (set in the node's own UI,
    ADR-013 decision 5 -- the server can never turn it on);
(b) `GET /api/v1/auth/nodes/voice-policy` said `always_on_permitted: true`
    for the sha256 of the wake model the child loaded, at the threshold and
    refractory period it runs at (`config.WAKE_THRESHOLD`,
    `config.WAKE_REFRACTORY_MS`; `friday_voice.policy`);
(c) `link: up` (the node's WebSocket to the server);
(d) mute is readable and off;
(e) no storm mute (the child's own wake-storm rule or the server's 423);
(f) every wake model hash verified (`models.wake_armable`; a real
    `OpenWakeWordDetector` refuses to load otherwise).

Otherwise the child is `armed_ptt` (hotkey only) or `off`. A `false` from
the server, a failed policy read or the server's 403
`voice.always_on_not_permitted` disarms at once: the server can only say
no, and an unknown answer is a no.

`reasons()` is pure: the pipeline feeds it the shell's and its own state,
so every condition is testable alone.
"""

REASON_MODE = "mode"
REASON_POLICY = "policy"
REASON_LINK = "link_down"
REASON_MUTE = "muted"
REASON_STORM = "storm_muted"
REASON_MODELS = "models"


class ArmingPolicy:
    """The real arming policy: (b) from the server's last answer, (f) from
    the detector that was loaded; (a), (c), (d), (e) from the caller."""

    polls = True

    def __init__(self, *, models_verified: bool):
        self.models_verified = bool(models_verified)
        self.server_permitted = False
        self.server_reasons = ("not_checked",)
        self.checked_at_ms = None  # monotonic ms of the last answer (or failure)

    def permitted(self) -> bool:
        """(b) and (f): what the server and the model files allow."""
        return self.server_permitted and self.models_verified

    def record(self, data, now_ms) -> bool:
        """The policy endpoint's answer; returns whether it permits. Only a
        literal `True` permits: anything else is a no."""
        self.checked_at_ms = now_ms
        self.server_permitted = data.get("always_on_permitted") is True
        reasons = data.get("reasons")
        if isinstance(reasons, list):
            self.server_reasons = tuple(str(r)[:40] for r in reasons[:10])
        else:
            self.server_reasons = ()
        return self.server_permitted

    def refuse(self, reason, now_ms) -> None:
        """A failed read or the server's 403: fail closed until the next
        permitting answer."""
        self.checked_at_ms = now_ms
        self.server_permitted = False
        self.server_reasons = (str(reason)[:40],)

    def reasons(self, *, mode, link_up, muted, storm_muted) -> list:
        out = []
        if mode != "always_on":
            out.append(REASON_MODE)
        if not self.server_permitted:
            out.append(REASON_POLICY)
        if not link_up:
            out.append(REASON_LINK)
        if muted is not False:
            out.append(REASON_MUTE)
        if storm_muted:
            out.append(REASON_STORM)
        if not self.models_verified:
            out.append(REASON_MODELS)
        return out


class RefuseAlwaysOn:
    """Never arms (no wake detector loaded, or a test of push-to-talk)."""

    polls = False

    def permitted(self) -> bool:
        return False

    def refuse(self, reason, now_ms) -> None:
        pass


class AllowAlwaysOn:
    """Test double for the wake path: (b) and (f) always hold, nothing is
    polled. Never used by `friday-voice run`."""

    polls = False

    def permitted(self) -> bool:
        return True

    def refuse(self, reason, now_ms) -> None:
        pass
