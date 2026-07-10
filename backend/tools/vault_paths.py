"""Vault filesystem safety (MVP 5 branch 3, brief §3).

Two-tier vault-root validation, split by which container can actually check
what: the web container never has the vault mounted, so it can only ever
validate `OBSIDIAN_VAULT_PATH`'s *string* shape (tier 1); only the worker
container -- where the vault is actually bind-mounted -- can check whether
the configured path really exists as a directory (tier 2).

Once tier 2 passes, every individual filesystem interaction re-validates its
immediate parent directory's realpath immediately before that operation, not
once at run start -- narrowing the window in which a directory component
could be swapped to a symlink (by Obsidian, a sync client, or another local
process) from "the whole run" to "one operation". Where the platform
supports it (`os.supports_dir_fd`; true for both the Linux deployment target
and this repo's macOS dev environment for `open`/`mkdir`/`unlink`/`rename`),
every one of these operations is performed `dir_fd`-relative to an
already-opened, already-validated directory descriptor opened with
`O_NOFOLLOW` -- closing the gap further by construction, since a descriptor
opened non-following a symlink keeps referring to the same inode regardless
of what happens to the path afterward. This narrows the race window; it does
not claim to eliminate it on a platform lacking this support (a residual
risk that would require an adversarial *local* process racing this job's own
operations, not a remote attacker -- a materially different threat model
than the SSRF case `knowledge.url_fetch` hardens against).
"""

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path


class VaultPathError(Exception):
    """Base class for every vault path-safety failure."""


class VaultRootConfigError(VaultPathError):
    """Tier 1: `OBSIDIAN_VAULT_PATH`'s string value is unsafe -- checked with
    zero filesystem access, so it can run in the web container at request
    time."""


class VaultRootUnavailableError(VaultPathError):
    """Tier 2: the configured path doesn't exist as a real directory where
    it's supposed to be mounted -- only checkable in the worker container,
    at execution time."""


class VaultPathEscapeError(VaultPathError):
    """A path's realpath, checked immediately before use, is no longer
    inside the validated vault root -- a symlink swap was detected
    mid-run."""


_DIR_FD_SUPPORTED = (
    os.mkdir in os.supports_dir_fd
    and os.open in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
)
_NOFOLLOW_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def validate_vault_root_string(vault_path: str) -> None:
    """Tier 1 (brief §3): non-empty, absolute, not `/` (or any OS root), and
    not equal to or a string-prefix ancestor of `Path.home()`. A vault
    rooted at or above the home directory could prune arbitrary unrelated
    files far outside the intended vault on every export run. A vault
    *inside* home (the common real-world case, e.g. `~/ObsidianVault`) is
    unaffected -- only the home directory itself or one of its ancestors is
    rejected."""
    if not vault_path:
        raise VaultRootConfigError("OBSIDIAN_VAULT_PATH is not configured.")
    if not os.path.isabs(vault_path):
        raise VaultRootConfigError("OBSIDIAN_VAULT_PATH must be an absolute path.")
    normalized = os.path.normpath(vault_path)
    if normalized == os.path.sep:
        raise VaultRootConfigError("OBSIDIAN_VAULT_PATH must not be a filesystem root.")
    home = os.path.normpath(str(Path.home()))
    if normalized == home or (home + os.sep).startswith(normalized + os.sep):
        raise VaultRootConfigError(
            "OBSIDIAN_VAULT_PATH must not be the home directory or one of its ancestors."
        )


def validate_vault_root_exists(vault_path: str) -> str:
    """Tier 2 (brief §3): only meaningful in the worker container, where the
    vault is actually bind-mounted. Returns the root's realpath (the
    reference every later per-operation check is made against).

    Re-applies tier 1's own rules twice (Codex review findings): once to
    the CONFIGURED string itself -- `os.path.realpath` always returns an
    absolute path even for a relative input (e.g. `.` resolves to the
    worker's cwd), so checking only the resolved path would silently accept
    a relative `OBSIDIAN_VAULT_PATH` that tier 1 (web) never actually saw in
    that form, exporting to whatever directory the worker happens to be
    running in -- and again to the RESOLVED path, since `OBSIDIAN_VAULT_PATH`
    could itself be a symlink (e.g. `/tmp/vault -> /` or `-> $HOME`) that
    passes tier 1's zero-filesystem-access string check while resolving to
    something catastrophic. Either failure is reported the same way as
    "doesn't exist," since all three mean tier 2 could not establish a
    safely usable root."""
    try:
        validate_vault_root_string(vault_path)
    except VaultRootConfigError as exc:
        raise VaultRootUnavailableError(str(exc)) from exc
    if not os.path.isdir(vault_path):
        raise VaultRootUnavailableError(
            f"OBSIDIAN_VAULT_PATH {vault_path!r} does not exist as a directory."
        )
    real = os.path.realpath(vault_path)
    try:
        validate_vault_root_string(real)
    except VaultRootConfigError as exc:
        raise VaultRootUnavailableError(str(exc)) from exc
    return real


def _check_inside_root(vault_root_real: str, candidate_real: str, what: str) -> None:
    if not (
        candidate_real == vault_root_real or candidate_real.startswith(vault_root_real + os.sep)
    ):
        raise VaultPathEscapeError(f"{what} escaped the vault root via symlink.")


def _write_all(fd: int, data: bytes) -> None:
    """`os.write` is a thin wrapper over the raw `write(2)` syscall -- it is
    NOT guaranteed to write the whole buffer in one call (Codex review
    finding). On a near-full filesystem, a short write would otherwise
    leave a truncated temp file that still gets renamed into place and
    reported as a successful export. Loop until every byte is written;
    raise if a write ever returns 0 (no forward progress possible)."""
    view = memoryview(data)
    total = 0
    while total < len(view):
        written = os.write(fd, view[total:])
        if written == 0:
            raise OSError("os.write() made no progress (disk full?)")
        total += written


@contextmanager
def dir_fd_scope(fd: int):
    """`os.close(fd)` guaranteed on the way out, however the block exits."""
    try:
        yield fd
    finally:
        os.close(fd)


def open_validated_dir(vault_root_real: str, path) -> int:
    """Re-validate `path`'s realpath is still inside the vault root, then
    open it as a directory file descriptor with `O_NOFOLLOW` -- the fd keeps
    referring to this exact inode regardless of what happens to the path
    afterward. Raises `VaultPathEscapeError` if the realpath check fails or
    if `path` turns out to be a symlink (the O_NOFOLLOW open itself fails --
    Linux reports this as `ELOOP`; this repo's macOS dev environment reports
    the same refusal as `ENOTDIR`, since a symlink isn't a directory when
    not dereferenced). Caller must `os.close()` the returned fd."""
    real = os.path.realpath(path)
    _check_inside_root(vault_root_real, real, str(path))
    try:
        return os.open(path, _NOFOLLOW_DIR_FLAGS)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            # O_NOFOLLOW's own rejection of a symlink -- a genuine escape
            # attempt/race, not an ordinary filesystem error.
            raise VaultPathEscapeError(f"{path} is a symlink, refusing to follow.") from exc
        raise  # PermissionError etc. -- let the caller's OSError handling classify it


def ensure_validated_subdir(vault_root_real: str, parent_fd: int, parent_path, name: str) -> int:
    """`mkdir` `name` under the already-validated `parent_fd` if missing
    (dir_fd-relative when supported, so the mkdir itself can't be tricked by
    a symlink swap of `parent_path` after it was opened), then open and
    return a validated fd for it."""
    child_path = Path(parent_path) / name
    if _DIR_FD_SUPPORTED:
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            pass
    else:
        child_path.mkdir(exist_ok=True)
    return open_validated_dir(vault_root_real, child_path)


def atomic_write_validated(
    vault_root_real: str, parent_fd: int, parent_path, filename: str, content: str
) -> None:
    """Write `content` to `filename` under the validated `parent_fd` via a
    temp file + atomic rename -- a reader never observes a partially
    written file at the final path. `parent_path`'s realpath is re-checked
    immediately before the rename (the operation `dir_fd` can't cover: the
    dir_fd protects *where* the temp file and final name live, but the
    final "is this parent still the vault we think it is" check is cheap
    insurance against a swap that raced the write itself).

    The temp file is cleaned up on any failure after creation (Codex review
    finding): its name is deterministic per worker pid, and `O_EXCL` means a
    leftover from a failed attempt would make an immediate retry by the same
    worker process fail with `FileExistsError` -- turning a transient
    failure (disk full, a detected path escape) into a permanent one for
    that filename until manually removed."""
    tmp_name = f".{filename}.tmp-{os.getpid()}"
    data = content.encode("utf-8")
    if _DIR_FD_SUPPORTED:
        # O_EXCL, not O_TRUNC: this name is always freshly computed (unique
        # per pid), so we only ever mean to CREATE it -- O_EXCL fails safely
        # rather than silently opening through a pre-planted symlink/file if
        # the name is somehow already taken.
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=parent_fd)
        try:
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
            _check_inside_root(vault_root_real, os.path.realpath(parent_path), str(parent_path))
            os.rename(tmp_name, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except Exception:
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    else:
        tmp_path = Path(parent_path) / tmp_name
        final_path = Path(parent_path) / filename
        try:
            tmp_path.write_bytes(data)
            _check_inside_root(vault_root_real, os.path.realpath(parent_path), str(parent_path))
            os.rename(tmp_path, final_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


def unlink_validated(vault_root_real: str, parent_fd: int, parent_path, filename: str) -> None:
    _check_inside_root(vault_root_real, os.path.realpath(parent_path), str(parent_path))
    if _DIR_FD_SUPPORTED:
        os.unlink(filename, dir_fd=parent_fd)
    else:
        os.unlink(Path(parent_path) / filename)


def read_validated(vault_root_real: str, parent_fd: int, parent_path, filename: str) -> str:
    """The prune pass's read-path operation (brief §3): reads a candidate
    file's frontmatter before deciding whether it's safe to prune. `O_NOFOLLOW`
    (Codex review finding) means a symlink planted in the vault -- pointing
    at a FIFO, a device, or a file outside the vault entirely -- is refused
    rather than followed; the caller (`tools.export._export_memory_dir`)
    already treats any read failure as "unrecognized, leave it alone,"
    so a refused symlink is simply skipped, never destroyed or read
    through.

    `O_NOFOLLOW` alone only refuses a SYMLINK -- a FIFO or other special
    file created directly in the vault (no symlink involved) would still
    open "successfully" and then block forever (a FIFO with no writer) or
    read an unbounded device (Codex review finding). `O_NONBLOCK` makes the
    open itself non-blocking (a no-op for a genuine regular file, so this
    never changes read behavior for the files this job actually writes),
    then an explicit `S_ISREG` check on the opened fd rejects anything that
    isn't a plain regular file before a single byte is ever read.
    """
    _check_inside_root(vault_root_real, os.path.realpath(parent_path), str(parent_path))
    if _DIR_FD_SUPPORTED:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(filename, flags, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"{filename} is not a regular file, refusing to read.")
            fh = os.fdopen(fd, "r", encoding="utf-8")
        except Exception:
            # fdopen (or the S_ISREG check) failed before fdopen took
            # ownership of fd -- close it ourselves. If fdopen succeeds,
            # `fh` owns fd from here on: the `with` block below closes it
            # exactly once, success or not.
            os.close(fd)
            raise
        with fh:
            return fh.read()
    file_path = Path(parent_path) / filename
    if file_path.is_symlink() or not file_path.is_file():
        raise OSError(f"{file_path} is not a plain regular file, refusing to read.")
    return file_path.read_text(encoding="utf-8")


def list_validated(vault_root_real: str, parent_fd: int, parent_path) -> list[str]:
    """Directory scan (the prune pass's read-path operation). Re-validates
    `parent_path` immediately before listing; lists via the already-open fd
    itself (`os.listdir` accepts an int fd directly) rather than the path
    string, so the listing is bound to the fd's inode, not to whatever the
    path currently resolves to."""
    _check_inside_root(vault_root_real, os.path.realpath(parent_path), str(parent_path))
    return os.listdir(parent_fd)
