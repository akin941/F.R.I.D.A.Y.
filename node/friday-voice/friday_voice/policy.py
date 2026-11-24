"""Reading the always-on policy (Phase 11 brief D11).

`GET /api/v1/auth/nodes/voice-policy?model_sha256=&threshold=&refractory_ms=`
for the wake model the child loaded, at the threshold and refractory period
the detector runs at -- read from `config` at call time, never from a copy
(D8; `tests/test_config.py`). The pipeline reads it when wake mode could
arm (mode `always_on`, a detector loaded): on the first credential, when
the mode becomes `always_on`, at every live-session start and every
`config.POLICY_POLL_S` (10 minutes). A `false` disarms at once; a failed
read is a `false` (fail closed).

The read is a worker job like every other call to Friday: the protocol
loop never blocks on the network.
"""

from . import config
from .workers import Delay


def request_policy(client, model_sha256):
    """The policy request for `model_sha256` at the package's threshold and
    refractory period."""
    return client.voice_policy(model_sha256, config.WAKE_THRESHOLD, config.WAKE_REFRACTORY_MS)


def policy_job(client, model_sha256):
    """A worker job: one policy read, no retry (the next poll is the
    retry, and until then the answer is "no")."""
    reply = request_policy(client, model_sha256)
    yield Delay(reply.latency_ms)
    yield {"kind": "policy", "data": dict(reply.data or {})}


def poll_due(last_ms, now_ms) -> bool:
    return last_ms is None or now_ms - last_ms >= config.POLICY_POLL_S * 1000
