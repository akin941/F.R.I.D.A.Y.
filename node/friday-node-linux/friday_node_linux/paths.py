"""Where the Linux node keeps its files, and the private-file rules
(Phase 13 brief D10).

- `~/.config/friday-node/policy.json` (`$XDG_CONFIG_HOME`): the local policy,
  0600, written only by the policy CLI at a TTY (`policy.py`);
- `~/.local/state/friday-node/` (`$XDG_STATE_HOME`, 0700): `credential`
  (0600), `spool/` (the bounded hook-event spool), `daemon.pid`;
- `$XDG_RUNTIME_DIR/friday-node/hook.sock` (0600 in a 0700 directory): the
  Claude Code hook relay socket (falls back to the state directory).

A private file is refused when it is a symlink, not a regular file, owned by
another uid, or readable/writable by group or other. A private directory is
refused when it is a symlink or owned by another uid. Writes are atomic: a
0600 temporary file in the same directory, fsync, rename, fsync of the
directory.
"""

import os
import stat
import tempfile
from pathlib import Path

APP_DIR = "friday-node"


class UnsafePath(Exception):
    """A private file or directory has the wrong owner, mode or type."""


def _xdg(name, fallback, env=None):
    env = os.environ if env is None else env
    value = env.get(name, "")
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def config_dir(env=None) -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config", env) / APP_DIR


def state_dir(env=None) -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state", env) / APP_DIR


def runtime_dir(env=None) -> Path:
    env = os.environ if env is None else env
    value = env.get("XDG_RUNTIME_DIR", "")
    if value and os.path.isabs(value):
        return Path(value) / APP_DIR
    return state_dir(env) / "run"


def policy_path(env=None) -> Path:
    return config_dir(env) / "policy.json"


def credential_path(env=None) -> Path:
    return state_dir(env) / "credential"


def spool_dir(env=None) -> Path:
    return state_dir(env) / "spool"


def pid_path(env=None) -> Path:
    return state_dir(env) / "daemon.pid"


def hook_socket_path(env=None) -> Path:
    return runtime_dir(env) / "hook.sock"


def ensure_private_dir(path) -> Path:
    """Create `path` (0700) or check an existing one: a real directory owned
    by this uid; its mode is narrowed to 0700."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UnsafePath(f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise UnsafePath(f"{path} is owned by another user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def check_private_file(path) -> os.stat_result:
    """The `lstat` of a private file, or UnsafePath."""
    path = Path(path)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise UnsafePath(f"{path} is not a regular file")
    if info.st_uid != os.getuid():
        raise UnsafePath(f"{path} is owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise UnsafePath(f"{path} is readable or writable by others (needs 0600)")
    return info


def read_private(path) -> bytes:
    """Read a private file, refusing it when unsafe (checked on the open
    descriptor too, so a swap between the check and the read is caught)."""
    check_private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise UnsafePath(f"{path} changed while it was opened")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_private_atomic(path, data: bytes) -> None:
    """Write `data` to `path` atomically, mode 0600 (temp file + rename)."""
    path = Path(path)
    ensure_private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
