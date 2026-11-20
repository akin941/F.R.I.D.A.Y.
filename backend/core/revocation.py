"""Device revocation fan-out (Phase 9 D1, D6).

Revoking a device, or engaging an owner's node kill switch, is announced on
the Redis pub/sub channel `friday:device-revoked`, so the node gateway can
close the matching live sockets within ~2 s instead of at the next
heartbeat re-check (<= 15 s). The payload is always
`{"device_id": "<uuid>", "user_id": "<uuid>"}`; the kill switch publishes
`device_id` "*" and is ALWAYS scoped by `user_id` -- a wildcard without a
user is never published (and a subscriber ignores one).

Best effort by design: the guarantee does not depend on Redis (the device
row is the source of truth and every heartbeat re-checks it), so a publish
never raises -- a Redis error is logged and the revocation still stands.
An explicit call from `accounts.devices`, not a Django signal
("explicit over signals", accounts/services.py).
"""

import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

REVOCATION_CHANNEL = "friday:device-revoked"
ALL_DEVICES = "*"
_TIMEOUT_SECONDS = 1.0


def _client():
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=_TIMEOUT_SECONDS,
        socket_connect_timeout=_TIMEOUT_SECONDS,
    )


def _publish(device_id, user_id):
    if not user_id or not device_id:
        logger.warning("device revocation publish skipped: missing device_id or user_id")
        return False
    payload = json.dumps({"device_id": str(device_id), "user_id": str(user_id)})
    try:
        client = _client()
        try:
            client.publish(REVOCATION_CHANNEL, payload)
        finally:
            client.close()
    except Exception:  # best effort: never fails the revoke
        logger.warning("device revocation publish failed", exc_info=True)
        return False
    return True


def publish_device_revoked(device_id, user_id):
    """Announce that one device of `user_id` was revoked. Returns whether
    the publish reached Redis; never raises."""
    return _publish(device_id, user_id)


def publish_kill_switch_engaged(user_id):
    """Announce that `user_id`'s node kill switch was engaged: every live
    node socket OF THAT OWNER closes. Never raises; refuses (False) without
    a user id, so a bare wildcard is never published."""
    return _publish(ALL_DEVICES, user_id)


def parse_revocation(raw):
    """The subscriber side's parser (the gateway, Phase 9 branch 4):
    `(device_id, user_id)` for a well-formed payload, else None. A payload
    that fails to parse, lacks `user_id`, or carries "*" without a user id
    matches nothing (fail safe: the heartbeat re-check still applies)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    device_id, user_id = data.get("device_id"), data.get("user_id")
    if not isinstance(device_id, str) or not isinstance(user_id, str):
        return None
    if not device_id or not user_id:
        return None
    return device_id, user_id
