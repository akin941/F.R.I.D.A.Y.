"""Boot-time checks of the Phase 10 node action settings (brief D2, D12).

Error-level, so a server configured to issue a grant longer than the
protocol ceiling (`MAX_GRANT_TTL_SECONDS`, 120 s) never boots: the node
would refuse every such grant anyway (D5 checks 10/18), and a longer grant
would mean a longer replay window."""

from django.conf import settings
from django.core.checks import Error, register

from nodes.output import SCRUB_CARRY_BYTES, SCRUB_TERM_MAX_CHARS
from nodes.protocol.messages import MAX_GRANT_TTL_SECONDS


def _int(name):
    value = getattr(settings, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


@register()
def check_node_action_settings(app_configs=None, **kwargs):
    errors = []
    ttl = _int("NODE_GRANT_TTL_SECONDS")
    if ttl is None or not 1 <= ttl <= MAX_GRANT_TTL_SECONDS:
        errors.append(
            Error(
                f"NODE_GRANT_TTL_SECONDS must be an integer in 1..{MAX_GRANT_TTL_SECONDS}.",
                id="nodes.E001",
            )
        )
    delay = _int("NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS")
    if delay is None or not 1 <= delay <= 600:
        errors.append(
            Error(
                "NODE_ACTION_DISPATCH_MAX_DELAY_SECONDS must be an integer in 1..600.",
                id="nodes.E002",
            )
        )
    retention = _int("NODE_ACTION_TAIL_RETENTION_DAYS")
    if retention is None or retention < 1:
        errors.append(
            Error("NODE_ACTION_TAIL_RETENTION_DAYS must be a positive integer.", id="nodes.E003")
        )
    if SCRUB_CARRY_BYTES < 2 * SCRUB_TERM_MAX_CHARS:
        errors.append(
            Error("The output scrub carry must hold two scrub terms (D12).", id="nodes.E004")
        )
    return errors
