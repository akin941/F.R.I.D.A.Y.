"""The node's Unix-socket hook relay (Phase 9 brief D10, D13 item 7): the
reference for Mac session M3 (`friday-node hook` and the app's listener).

Claude Code runs `friday-node hook` as a command hook; the CLI writes its
stdin to a **user-owned Unix domain socket** and exits 0 within 3 s whatever
happens (`relay_stdin` here). The node listens on that socket
(`HookRelay`):

- the socket is mode 0600 inside a 0700 directory owned by the node's user
  (an existing directory with another owner, or a symlink, is refused);
- every connection's **peer uid** is checked (`SO_PEERCRED` on Linux; the
  Swift app uses `getpeereid`/`LOCAL_PEERCRED`) and a different uid is
  refused before a byte is read;
- input over `MAX_INPUT_BYTES` (256 KiB, friday-hook's bound) is dropped
  whole, as is anything that is not a JSON object;
- the payload is reduced to the 8A hook-event shape exactly as
  `friday-hook` does (`build_hook_event`: the allow-listed event names and
  fields, the free-text `message` scrubbed first and then cut), so an event
  relayed by both carries the same fields and the server dedupes it on
  `event_id`;
- the event goes to `deliver(event)`: the node sends it live or spools it
  (`ReferenceNode.relay_hook_event`). Nothing from the socket ever becomes a
  command.

No TCP listener of any kind (D13 item 1). Standard library only.
"""

import json
import os
import socket
import stat
import struct
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from nodes.reference.stream_scrub import default_scrub

ALLOWED_EVENTS = frozenset({"SessionStart", "Notification", "Stop", "SessionEnd"})
STRING_FIELDS = ("hook_event_name", "session_id", "cwd", "notification_type", "source", "reason")
MESSAGE_FIELD = "message"
MAX_FIELD_CHARS = 1000
MAX_MESSAGE_CHARS = 200
MAX_INPUT_BYTES = 256 * 1024
READ_TIMEOUT_SECONDS = 2.0
CLIENT_DEADLINE_SECONDS = 3.0


class RelayError(Exception):
    """The socket's directory or path is unsafe."""


def so_peercred_uid(conn) -> int:
    """The peer uid of a connected Unix socket (Linux `SO_PEERCRED`). Raises
    OSError where it is unavailable: the relay then refuses (fail closed)."""
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise OSError("SO_PEERCRED is not available on this platform")
    raw = conn.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


def build_hook_event(payload, *, now, event_id=None, scrub=None):
    """The allow-listed, scrubbed hook event (the `friday-hook`
    `build_event` rule), or None when the payload is not one Friday ingests.
    `now` is integer Unix seconds."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in ALLOWED_EVENTS:
        return None
    scrub = scrub or default_scrub()
    event = {}
    for key in STRING_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            event[key] = value[:MAX_FIELD_CHARS]
    message = payload.get(MESSAGE_FIELD)
    if isinstance(message, str) and message:
        # Scrub first, then cut: a cut never leaves half a secret behind.
        event[MESSAGE_FIELD] = scrub(message[: MAX_FIELD_CHARS * 4])[:MAX_MESSAGE_CHARS]
    event["occurred_at"] = datetime.fromtimestamp(now, UTC).isoformat()
    event["event_id"] = event_id or uuid.uuid4().hex
    return event


class HookRelay:
    """The node side of the hook socket."""

    def __init__(
        self,
        socket_path,
        *,
        deliver,
        clock,
        scrub=None,
        peer_uid=so_peercred_uid,
        expected_uid=None,
        id_factory=None,
        max_input=MAX_INPUT_BYTES,
    ):
        self.path = Path(socket_path)
        self.deliver = deliver
        self.clock = clock
        self.scrub = scrub or default_scrub()
        self.peer_uid = peer_uid
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.max_input = max_input
        self.outcomes: dict = {}
        self._sock = None

    # --- lifecycle -----------------------------------------------------------

    def start(self):
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(directory)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RelayError("socket directory is not a directory")
        if info.st_uid != os.getuid():
            raise RelayError("socket directory owned by another user")
        os.chmod(directory, 0o700)
        if os.path.lexists(self.path):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                raise RelayError("socket path exists and is not a socket")
            os.unlink(self.path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            sock.bind(str(self.path))
        finally:
            os.umask(previous)
        os.chmod(self.path, 0o600)
        sock.listen(8)
        self._sock = sock
        return self

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):
                os.unlink(self.path)
        except FileNotFoundError:
            pass

    # --- one connection --------------------------------------------------------

    def _count(self, outcome):
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1
        return outcome

    def handle_one(self, timeout=None) -> str:
        """Accept and handle one connection. Returns the outcome:
        `relayed`, `ignored` (not an allow-listed hook event), `malformed`,
        `oversized`, `peer_refused` or `timeout`."""
        self._sock.settimeout(timeout)
        try:
            conn, _ = self._sock.accept()
        except TimeoutError:
            return "timeout"
        with conn:
            try:
                uid = self.peer_uid(conn)
            except OSError:
                return self._count("peer_refused")
            if uid != self.expected_uid:
                return self._count("peer_refused")
            data = _read_bounded(conn, self.max_input)
            if data is None:
                return self._count("oversized")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._count("malformed")
        event = build_hook_event(
            payload, now=self.clock.now(), event_id=self.id_factory(), scrub=self.scrub
        )
        if event is None:
            return self._count("ignored")
        self.deliver(event)
        return self._count("relayed")


def _read_bounded(conn, limit):
    """At most `limit` bytes until EOF, or None when the peer sends more
    (dropped whole) or stalls past the read timeout."""
    conn.settimeout(READ_TIMEOUT_SECONDS)
    chunks, total = [], 0
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
    except OSError:
        return None
    return b"".join(chunks)


def relay_stdin(socket_path, data: bytes, *, deadline=CLIENT_DEADLINE_SECONDS) -> int:
    """`friday-node hook`: write `data` (the hook's stdin) to the node's
    socket. Never raises, prints nothing and returns 0 within `deadline`
    seconds whatever happens (a down node only loses the event; the next
    session start shows it)."""
    started = time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(deadline - (time.monotonic() - started), 0.01))
            sock.connect(str(socket_path))
            sock.sendall(data[: MAX_INPUT_BYTES + 1])
            sock.shutdown(socket.SHUT_WR)
    except Exception:  # noqa: BLE001 - the CLI never fails Claude Code
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - the reference CLI
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(relay_stdin(path, sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)))
