"""Read one file out of a container-writable directory (Phase 8B branch 5,
brief D10 "Reading `/out` on the host").

The Graphify container can write anything into its `/out` bind, so the
host treats that directory as hostile: `graph.json` could be a symlink to
a host file, a FIFO that blocks the reader forever, a device, a directory,
or a huge file. `read_bounded_regular_file` follows the descriptor-walk
pattern of `tools/vault_paths.py` without importing it (no app imports
`tools` from here for a read):

1. open the directory itself with `O_DIRECTORY | O_NOFOLLOW`;
2. open the name **relative to that descriptor** with
   `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` -- a symlink fails with `ELOOP`
   and a FIFO opens without blocking;
3. `fstat` the descriptor and refuse anything that is not a regular file,
   or whose `st_size` is over the limit, **before reading a byte**;
4. read at most `limit + 1` bytes from that same descriptor, so a file
   grown after the stat is still refused.

A refusal raises `UnsafeOutput` with a stable `reason`. Its message names
the reason only: never a path the container chose, never a byte of
content.
"""

import errno
import os
import stat

# The read seam: tests spy on it to prove a refusal happens before any read.
_read = os.read

_CHUNK = 1024 * 1024


class UnsafeOutput(Exception):
    def __init__(self, reason):
        super().__init__(f"container output refused: {reason}")
        self.reason = reason


def read_bounded_regular_file(directory, name, *, limit):
    """The bytes of `directory/name`, or `UnsafeOutput`."""
    if not name or "/" in name or name in (".", ".."):
        raise UnsafeOutput("bad_name")
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise UnsafeOutput("directory_unavailable") from exc
    try:
        try:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0),
                dir_fd=dir_fd,
            )
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise UnsafeOutput("missing") from exc
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise UnsafeOutput("symlink") from exc
            raise UnsafeOutput("unopenable") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafeOutput("not_regular_file")
            if info.st_size > limit:
                raise UnsafeOutput("oversize")
            chunks, total = [], 0
            while total <= limit:
                chunk = _read(fd, min(_CHUNK, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > limit:
                raise UnsafeOutput("oversize")
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
