"""The Redis side of the node gateway (Phase 9 brief D6; Phase 10 brief D7):
presence, the per-node invoke inbox, the read result keys and the shared
in-flight counter.

- **Presence** is the key `nodes:presence:<node_id>` with a TTL of 45 s
  (three missed heartbeats), set by the gateway session on `hello` and
  refreshed on every `heartbeat` (`nodes.gateway.NodeSession`). No key = the
  node is offline: a gated dispatch then ends `node_offline` and nothing is
  queued (ADR-013 decision 6).
- **Inbox** is the Redis list `nodes:inbox:<node_id>`. `push_invoke` appends
  a signed envelope and sets the list's TTL to the envelope's remaining
  lifetime in the same `MULTI`, so an envelope never outlives its expiry in
  Redis; the gateway session that holds the node's socket drains it.

- **Result keys** (Phase 9 branch 4) are the Redis lists
  `nodes:result:<invocation_id>`. The gateway `LPUSH`es a read's result (or
  its own verdict: `node_disconnected`, `node_mismatch`) and sets the key's
  TTL (`NODE_RESULT_TTL_SECONDS`, default 30) in the same `MULTI`, so a
  result key never exists without one. The caller (`invoke_read`) `BLPOP`s
  it for at most `NODE_INVOKE_TIMEOUT_SECONDS` and `DEL`s it at once: a
  result lives in Redis for milliseconds, and at most the TTL when nobody
  waits (a late result). This transit is the only copy of node output
  outside the caller's memory.
- **In-flight counter** `nodes:inflight:<node_id>` (brief follow-up 3): an
  `INCR`/`DECR` counter with a TTL, shared by every process that calls
  `invoke_read` (web, worker, gateway), so `NODE_MAX_INFLIGHT` holds across
  processes, not per process.

Phase 9 branch 4 (`feat/p9-node-gateway`) owns the socket process itself
(`nodes.gateway.NodeSocketApp`: handshake, the inbox loop, revocation
fan-out); it reuses these helpers. Every function raises `BusUnavailable`
when Redis cannot be reached; callers fail closed on it.
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


def result_key(invocation_id) -> str:
    return f"nodes:result:{invocation_id}"


def inflight_key(node_id) -> str:
    return f"nodes:inflight:{node_id}"


def result_ttl_seconds() -> int:
    return max(1, int(getattr(settings, "NODE_RESULT_TTL_SECONDS", 30)))


def _client(socket_timeout=_TIMEOUT_SECONDS):
    import redis

    return redis.Redis.from_url(
        settings.REDIS_URL,
        socket_timeout=socket_timeout,
        socket_connect_timeout=_TIMEOUT_SECONDS,
    )


def _run(operation, socket_timeout=_TIMEOUT_SECONDS):
    import redis

    try:
        client = _client(socket_timeout)
        try:
            return operation(client)
        finally:
            client.close()
    except redis.RedisError as exc:
        raise BusUnavailable(str(type(exc).__name__)) from exc


def ping() -> bool:
    return bool(_run(lambda client: client.ping()))


def mark_present(node_id, ttl_seconds=PRESENCE_TTL_SECONDS, version=1) -> None:
    """Set (or refresh) the presence key. Its value is the session's
    negotiated protocol version, so `invoke_read` signs an envelope of the
    version the live socket speaks."""
    _run(lambda client: client.set(presence_key(node_id), str(int(version)), ex=int(ttl_seconds)))


def clear_presence(node_id) -> None:
    _run(lambda client: client.delete(presence_key(node_id)))


def is_present(node_id) -> bool:
    return bool(_run(lambda client: client.exists(presence_key(node_id))))


def present_version(node_id) -> int | None:
    """The protocol version of the node's live session, or None when the
    node is not present."""
    value = _run(lambda client: client.get(presence_key(node_id)))
    if value is None:
        return None
    try:
        version = int(value)
    except (TypeError, ValueError):
        return 1
    return version if version in (1, 2) else 1


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


# --- read results (Phase 9 branch 4) -------------------------------------


def push_result(invocation_id, payload: dict) -> None:
    """`LPUSH` one read result (or gateway verdict) to its result key, with
    the key's TTL set in the same `MULTI`."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    key = result_key(invocation_id)
    ttl = result_ttl_seconds()

    def operation(client):
        pipe = client.pipeline(transaction=True)
        pipe.lpush(key, body)
        pipe.expire(key, ttl)
        pipe.execute()

    _run(operation)


def wait_result(invocation_id, timeout_seconds) -> dict | None:
    """`BLPOP` the result key for at most `timeout_seconds`, then `DEL` it.
    Returns the payload, or None on timeout (or an unparsable body)."""
    key = result_key(invocation_id)
    timeout = max(1, int(timeout_seconds))

    def operation(client):
        try:
            popped = client.blpop([key], timeout=timeout)
        finally:
            client.delete(key)
        return popped

    popped = _run(operation, socket_timeout=timeout + 2)
    if popped is None:
        return None
    try:
        value = json.loads(popped[1])
    except (TypeError, ValueError, IndexError):
        return None
    return value if isinstance(value, dict) else None


# --- the shared in-flight cap (brief follow-up 3) ------------------------


def acquire_inflight(node_id, limit, ttl_seconds) -> bool:
    """Take one of the node's `limit` in-flight slots; False when all are
    taken (the caller answers `busy`). The counter carries a TTL refreshed
    on every acquire, so a caller that died without releasing frees its
    slot after `ttl_seconds` at worst."""
    key = inflight_key(node_id)

    def operation(client):
        pipe = client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, max(1, int(ttl_seconds)))
        value, _ = pipe.execute()
        if int(value) > int(limit):
            client.decr(key)
            return False
        return True

    return bool(_run(operation))


_RELEASE = """
local value = tonumber(redis.call('GET', KEYS[1]) or '0')
if value > 0 then
  return redis.call('DECR', KEYS[1])
end
return 0
"""


def release_inflight(node_id) -> None:
    """Give a slot back (never below zero)."""
    _run(lambda client: client.eval(_RELEASE, 1, inflight_key(node_id)))


def inflight_count(node_id) -> int:
    value = _run(lambda client: client.get(inflight_key(node_id)))
    return int(value or 0)


def clear_inflight(node_id) -> None:
    _run(lambda client: client.delete(inflight_key(node_id)))
