"""The node's bounded event spool (Phase 9 brief D10, D13 item 7; the Swift
port is Mac session M3).

Hook events that cannot be sent (the socket is down, or an upload is already
in flight) are written here and uploaded after reconnect, in order, one
batch in flight, each deleted only after its `event.ack`.

Limits (normative):

- **events only**: a batch is a list of 1-50 hook events (JSON objects);
  nothing else is ever written;
- **<= 10 MiB** in total (`MAX_SPOOL_BYTES`): after a write, the oldest
  batches are dropped first until the spool fits; a single batch larger
  than the whole budget is refused;
- **<= 24 h** (`MAX_SPOOL_AGE_SECONDS`): a batch older than that is dropped
  (the server's 8A `MAX_EVENT_AGE` would drop it anyway);
- the directory is mode 0700 and every batch file mode 0600, created with
  `O_EXCL` (never through an existing path or symlink);
- a batch is deleted only by `ack(batch_id)` (or by the age and size caps):
  an upload that is never acked stays spooled and is sent again after the
  next reconnect (the server dedupes on `event_id`).

The spool survives a node restart: batches are files named
`<seq:012d>.json` holding `{"created_at": <unix seconds>, "events": [...]}`.
Standard library only.
"""

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_SPOOL_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_SPOOL_AGE_SECONDS = 24 * 3600
MAX_BATCH_EVENTS = 50
_NAME_DIGITS = 12


class SpoolError(Exception):
    """The spool directory is unsafe (wrong owner, a symlink, not a
    directory)."""


@dataclass(frozen=True)
class SpoolBatch:
    batch_id: str
    created_at: int
    events: list
    size: int


class Spool:
    def __init__(
        self, directory, clock, *, max_bytes=MAX_SPOOL_BYTES, max_age=MAX_SPOOL_AGE_SECONDS
    ):
        self.directory = Path(directory)
        self.clock = clock
        self.max_bytes = max_bytes
        self.max_age = max_age
        self.dropped = 0
        self._prepare()
        existing = [int(p.stem) for p in self._paths()]
        self._next_seq = max(existing, default=0) + 1

    # --- the directory -----------------------------------------------------

    def _prepare(self):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise SpoolError("spool path is not a directory") from exc
        info = os.lstat(self.directory)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SpoolError("spool path is not a directory")
        if info.st_uid != os.getuid():
            raise SpoolError("spool directory owned by another user")
        os.chmod(self.directory, 0o700)

    def _paths(self):
        paths = []
        for path in self.directory.iterdir():
            if (
                path.suffix == ".json"
                and path.stem.isdigit()
                and len(path.stem) == _NAME_DIGITS
                and not path.is_symlink()
                and path.is_file()
            ):
                paths.append(path)
        return sorted(paths)

    def _load(self, path):
        try:
            size = path.stat().st_size
            document = json.loads(path.read_text(encoding="utf-8"))
            created_at = document["created_at"]
            events = document["events"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(created_at, int) or not _valid_events(events):
            return None
        return SpoolBatch(path.stem, created_at, events, size)

    # --- writing -----------------------------------------------------------

    def append(self, events) -> str | None:
        """Spool one batch; returns its id, or None when it was refused
        (not a list of 1-50 objects, or larger than the whole budget)."""
        if not _valid_events(events):
            self.dropped += 1
            return None
        body = json.dumps(
            {"created_at": self.clock.now(), "events": list(events)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > self.max_bytes:
            self.dropped += 1
            return None
        batch_id = f"{self._next_seq:0{_NAME_DIGITS}d}"
        self._next_seq += 1
        path = self.directory / f"{batch_id}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, body)
        finally:
            os.close(fd)
        self.enforce()
        return batch_id

    def enforce(self) -> None:
        """Apply the age and size caps (oldest dropped first)."""
        now = self.clock.now()
        batches = []
        for path in self._paths():
            batch = self._load(path)
            if batch is None or now - batch.created_at > self.max_age:
                self._remove(path)
                continue
            batches.append((path, batch))
        total = sum(batch.size for _, batch in batches)
        while batches and total > self.max_bytes:
            path, batch = batches.pop(0)
            self._remove(path)
            total -= batch.size

    def _remove(self, path):
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self.dropped += 1

    # --- reading and acking --------------------------------------------------

    def pending(self) -> list:
        """The spooled batches, oldest first (after the caps)."""
        self.enforce()
        return [b for b in (self._load(p) for p in self._paths()) if b is not None]

    def first(self) -> SpoolBatch | None:
        batches = self.pending()
        return batches[0] if batches else None

    def ack(self, batch_id) -> bool:
        """Delete a batch after the server acked it."""
        if not (isinstance(batch_id, str) and batch_id.isdigit() and len(batch_id) == _NAME_DIGITS):
            return False
        path = self.directory / f"{batch_id}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def depth(self) -> tuple:
        """`(count, bytes)` for the heartbeat."""
        paths = self._paths()
        return len(paths), sum(p.stat().st_size for p in paths)


def _valid_events(events) -> bool:
    return (
        isinstance(events, list)
        and 1 <= len(events) <= MAX_BATCH_EVENTS
        and all(isinstance(event, dict) for event in events)
    )
