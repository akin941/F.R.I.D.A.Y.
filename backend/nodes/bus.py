"""The Redis side of the node gateway that Phase 10 branch 3 needs (Phase 9
brief D6): presence and the per-node invoke inbox.

- **Presence** is the key `nodes:presence:<node_id>` with a TTL of 45 s
  (three missed heartbeats), set by the gateway session on `hello` and
  refreshed on every `heartbeat` (`nodes.gateway.NodeSession`). No key = the
  node is offline: a gated dispatch then ends `node_offline` and nothing is
  queued (ADR-013 decision 6).
- **Inbox** is the Redis list `nodes:inbox:<node_id>`. `push_invoke` appends
  a signed envelope and sets the list's TTL to the envelope's remaining
  lifetime in the same `MULTI`, so an envelope never outlives its expiry in
  Redis; the gateway session that holds the node's socket drains it.

Phase 9 branch 4 (`feat/p9-node-gateway`) owns the socket process itself
(handshake, the `BLPOP` loop, revocation fan-out); it reuses these helpers.
Every function raises `BusUnavailable` when Redis cannot be reached; callers
fail closed on it.
"""

import json
import time

from django.conf import settings

PRESENCE_TTL_SECONDS = 45
_TIMEOUT_SECONDS = 1.0


class BusUnavailable(Exception):
    """Redis could not be reached."""


def presence_key(node_id) -> str:
    return f"nodes:presence:{node_id}"


def inbox_key(node_id) -> str:
    return f"nodes:inbox:{node_id}"


def _client():
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=_TIMEOUT_SECONDS,
        socket_connect_timeout=_TIMEOUT_SECONDS,
    )


def _run(operation):
    import redis

    try:
        client = _client()
        try:
            return operation(client)
        finally:
            client.close()
    except redis.RedisError as exc:
        raise BusUnavailable(str(type(exc).__name__)) from exc


def ping() -> bool:
    return bool(_run(lambda client: client.ping()))


def mark_present(node_id, ttl_seconds=PRESENCE_TTL_SECONDS) -> None:
    _run(lambda client: client.set(presence_key(node_id), "1", ex=int(ttl_seconds)))


def clear_presence(node_id) -> None:
    _run(lambda client: client.delete(presence_key(node_id)))


def is_present(node_id) -> bool:
    return bool(_run(lambda client: client.exists(presence_key(node_id))))


def push_invoke(node_id, envelope: dict, *, now=None) -> None:
    """Append `envelope` to the node's inbox; the list expires with it."""
    now = int(now if now is not None else time.time())
    ttl = max(int(envelope["expires_at"]) - now, 1)
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)

    def operation(client):
        pipe = client.pipeline(transaction=True)
        pipe.rpush(inbox_key(node_id), body)
        pipe.expire(inbox_key(node_id), ttl)
        pipe.execute()

    _run(operation)


def drain_inbox(node_id, limit=64) -> list:
    """Pop up to `limit` envelopes from the node's inbox (oldest first). A
    body that does not parse is dropped (it was not written by us)."""

    def operation(client):
        bodies = []
        for _ in range(limit):
            body = client.lpop(inbox_key(node_id))
            if body is None:
                break
            bodies.append(body)
        return bodies

    envelopes = []
    for body in _run(operation):
        try:
            value = json.loads(body)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            envelopes.append(value)
    return envelopes


def clear_inbox(node_id) -> None:
    _run(lambda client: client.delete(inbox_key(node_id)))
