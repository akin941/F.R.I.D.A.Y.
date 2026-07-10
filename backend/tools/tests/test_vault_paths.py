import os
from pathlib import Path
from unittest import mock

import pytest

from tools.vault_paths import (
    VaultPathEscapeError,
    VaultRootConfigError,
    VaultRootUnavailableError,
    atomic_write_validated,
    dir_fd_scope,
    ensure_validated_subdir,
    list_validated,
    open_validated_dir,
    read_validated,
    unlink_validated,
    validate_vault_root_exists,
    validate_vault_root_string,
)


class TestTier1ConfigValidation:
    def test_empty_is_rejected(self):
        with pytest.raises(VaultRootConfigError):
            validate_vault_root_string("")

    def test_relative_path_is_rejected(self):
        with pytest.raises(VaultRootConfigError):
            validate_vault_root_string("relative/vault")

    def test_filesystem_root_is_rejected(self):
        with pytest.raises(VaultRootConfigError):
            validate_vault_root_string("/")

    def test_home_directory_itself_is_rejected(self):
        with pytest.raises(VaultRootConfigError):
            validate_vault_root_string(str(Path.home()))

    def test_ancestor_of_home_is_rejected(self):
        with pytest.raises(VaultRootConfigError):
            validate_vault_root_string(str(Path.home().parent))

    def test_subdirectory_of_home_is_allowed(self):
        # The common real-world case (e.g. ~/ObsidianVault) must NOT be
        # rejected -- only home itself or an ancestor is unsafe.
        validate_vault_root_string(str(Path.home() / "ObsidianVault"))

    def test_ordinary_absolute_path_is_allowed(self, tmp_path):
        validate_vault_root_string(str(tmp_path))


class TestTier2ExistenceValidation:
    def test_nonexistent_path_is_rejected(self, tmp_path):
        with pytest.raises(VaultRootUnavailableError):
            validate_vault_root_exists(str(tmp_path / "does-not-exist"))

    def test_existing_directory_returns_its_realpath(self, tmp_path):
        real = validate_vault_root_exists(str(tmp_path))
        assert real == os.path.realpath(str(tmp_path))

    def test_a_file_is_not_a_valid_root(self, tmp_path):
        file_path = tmp_path / "not-a-dir"
        file_path.write_text("x")
        with pytest.raises(VaultRootUnavailableError):
            validate_vault_root_exists(str(file_path))

    def test_a_symlinked_root_resolving_to_home_is_rejected(self, tmp_path):
        """Codex review finding: a safe-looking absolute path can itself be
        a symlink to something catastrophic (home, or an ancestor of it).
        Tier 1's string check can't see through it (no filesystem access);
        tier 2 must re-apply the same rules to the RESOLVED path."""
        link = tmp_path / "vault-link"
        link.symlink_to(Path.home())
        validate_vault_root_string(str(link))  # tier 1 alone doesn't catch it
        with pytest.raises(VaultRootUnavailableError):
            validate_vault_root_exists(str(link))

    def test_a_symlinked_root_resolving_to_a_safe_directory_is_accepted(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link = tmp_path / "vault-link"
        link.symlink_to(real_dir)
        real = validate_vault_root_exists(str(link))
        assert real == os.path.realpath(str(real_dir))

    def test_a_relative_path_is_rejected_even_if_it_exists(self, tmp_path, monkeypatch):
        """Codex review finding: `os.path.realpath` always returns an
        absolute path even for a relative input (e.g. "." resolves to the
        worker's cwd) -- checking only the resolved path would silently
        accept a relative `OBSIDIAN_VAULT_PATH` that tier 1 (web) never
        actually saw in that form, exporting into whatever directory the
        worker happens to be running in."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(VaultRootUnavailableError):
            validate_vault_root_exists(".")


class TestSymlinkEscapeDetection:
    def test_symlink_pointing_outside_root_is_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        escape_link = root / "escape"
        escape_link.symlink_to(outside)

        root_real = os.path.realpath(str(root))
        with pytest.raises(VaultPathEscapeError):
            open_validated_dir(root_real, escape_link)

    def test_symlink_pointing_inside_root_still_rejected_via_nofollow(self, tmp_path):
        """The realpath check alone would pass here (the symlink's target
        realpath IS inside the root) -- this specifically exercises the
        `O_NOFOLLOW` open itself refusing to follow a symlink, regardless
        of where it points."""
        root = tmp_path / "root"
        root.mkdir()
        real_dir = root / "real_dir"
        real_dir.mkdir()
        link = root / "link_to_real_dir"
        link.symlink_to(real_dir)

        root_real = os.path.realpath(str(root))
        with pytest.raises(VaultPathEscapeError):
            open_validated_dir(root_real, link)

    def test_ordinary_directory_opens_cleanly(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        fd = open_validated_dir(root_real, tmp_path)
        os.close(fd)


class TestFilesystemPrimitives:
    def test_ensure_validated_subdir_creates_and_is_idempotent(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as root_fd:
            with dir_fd_scope(ensure_validated_subdir(root_real, root_fd, tmp_path, "sub")):
                pass
            assert (tmp_path / "sub").is_dir()
            # Second call must not raise on an already-existing directory.
            with dir_fd_scope(ensure_validated_subdir(root_real, root_fd, tmp_path, "sub")):
                pass

    def test_atomic_write_then_read_round_trips(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "hello world")
            assert read_validated(root_real, fd, tmp_path, "note.md") == "hello world"

    def test_atomic_write_leaves_no_temp_file_behind(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "content")
        assert set(os.listdir(tmp_path)) == {"note.md"}

    def test_atomic_write_overwrites_existing_file(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "first")
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "second")
            assert read_validated(root_real, fd, tmp_path, "note.md") == "second"

    def test_unlink_removes_the_file(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "content")
            unlink_validated(root_real, fd, tmp_path, "note.md")
        assert not (tmp_path / "note.md").exists()

    def test_list_validated_matches_directory_contents(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            atomic_write_validated(root_real, fd, tmp_path, "a.md", "a")
            atomic_write_validated(root_real, fd, tmp_path, "b.md", "b")
            assert set(list_validated(root_real, fd, tmp_path)) == {"a.md", "b.md"}

    def test_atomic_write_loops_past_a_short_os_write(self, tmp_path):
        """Codex review finding: `os.write` isn't guaranteed to write the
        whole buffer in one call. Simulate a short write (returns fewer
        bytes than given) and confirm the full content still lands."""
        root_real = os.path.realpath(str(tmp_path))
        content = "hello world, this is more than one byte"
        real_write = os.write
        calls = []

        def flaky_write(fd, data):
            calls.append(bytes(data))
            if len(calls) == 1:
                return real_write(fd, bytes(data)[:1])  # short write: 1 byte only
            return real_write(fd, data)

        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            with mock.patch("os.write", side_effect=flaky_write):
                atomic_write_validated(root_real, fd, tmp_path, "note.md", content)
            assert read_validated(root_real, fd, tmp_path, "note.md") == content
        assert len(calls) > 1  # the short write really did require a second call

    def test_atomic_write_raises_on_zero_progress_write(self, tmp_path):
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            with mock.patch("os.write", return_value=0):
                with pytest.raises(OSError, match="no progress"):
                    atomic_write_validated(root_real, fd, tmp_path, "note.md", "content")

    def test_a_failed_write_does_not_block_a_same_pid_retry(self, tmp_path):
        """Codex review finding: the temp filename is deterministic per
        worker pid and O_EXCL is used, so a failed attempt that left the
        temp file behind would make an immediate retry by the SAME process
        fail with FileExistsError -- turning a transient failure into a
        permanent one. The failed attempt must clean up after itself."""
        root_real = os.path.realpath(str(tmp_path))
        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            with mock.patch("os.write", return_value=0):
                with pytest.raises(OSError, match="no progress"):
                    atomic_write_validated(root_real, fd, tmp_path, "note.md", "content")
            # No leftover temp file...
            assert os.listdir(tmp_path) == []
            # ...so a retry (same pid, same filename) succeeds cleanly.
            atomic_write_validated(root_real, fd, tmp_path, "note.md", "content")
            assert read_validated(root_real, fd, tmp_path, "note.md") == "content"


class TestPruneReadNeverFollowsSymlinks:
    def test_a_symlink_planted_as_a_prune_candidate_is_refused_not_followed(self, tmp_path):
        """Codex review finding: the prune pass's read step must never
        follow a symlink planted in the vault (it could point at a FIFO, a
        device, or a file outside the vault). `read_validated` must refuse
        it -- the caller (tools.export) already treats any read failure as
        'unrecognized, leave it alone.'"""
        outside_secret = tmp_path.parent / f"secret-{os.getpid()}.txt"
        outside_secret.write_text("outside vault content")
        root_real = os.path.realpath(str(tmp_path))
        (tmp_path / "planted.md").symlink_to(outside_secret)

        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            with pytest.raises(OSError):
                read_validated(root_real, fd, tmp_path, "planted.md")

    def test_a_fifo_planted_as_a_prune_candidate_is_refused_not_read(self, tmp_path):
        """Codex review finding: `O_NOFOLLOW` alone only refuses a SYMLINK --
        a FIFO created directly in the vault (no symlink involved) would
        still open "successfully" and then block forever waiting for a
        writer, or read an unbounded device. `read_validated` must reject
        any non-regular file via an `S_ISREG` check, using `O_NONBLOCK` so
        the open itself can never hang in the first place."""
        root_real = os.path.realpath(str(tmp_path))
        os.mkfifo(tmp_path / "planted.md")

        with dir_fd_scope(open_validated_dir(root_real, tmp_path)) as fd:
            with pytest.raises(OSError):
                read_validated(root_real, fd, tmp_path, "planted.md")
