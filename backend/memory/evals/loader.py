"""Case loaders (brief D9).

- **Public seed**: `memory/evals/cases/public/*.jsonl`, committed, synthetic.
- **Private set**: the owner's directory `MEMORY_EVAL_PRIVATE_DIR` (default
  `~/friday-evals/`, outside the repo). It is refused unless it is a real
  directory (not a symlink), owned by the current user, with no group or
  other permission bits (mode 0700). Inside it, only `*.jsonl` case files
  and the `results/` directory may exist: any other entry (a `.json`, a
  `.txt`, a symlink, another directory) refuses the whole set, so a stray
  file is noticed rather than silently skipped.

`stray_case_files()` is the "never commit private cases" check: any file
under `memory/evals/cases/` outside `public/` is a stray (a CI test fails on
one).

Errors name the file and line number, never a line's content.
"""

import json
import os
import stat
from pathlib import Path

from memory.evals.schema import CaseError, validate_case

CASES_DIR = Path(__file__).resolve().parent / "cases"
PUBLIC_DIR = CASES_DIR / "public"
CASE_SUFFIX = ".jsonl"
RESULTS_DIRNAME = "results"
MAX_CASE_FILE_BYTES = 4 * 1024 * 1024
MAX_CASES = 1000


class LoaderError(ValueError):
    """The case set cannot be loaded. The message never quotes case text."""


def _read_regular_file(path):
    """Read `path` without following a symlink, refusing non-regular files
    and oversized ones (size checked before the read)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise LoaderError(f"{path.name}: cannot be opened ({exc.strerror})") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise LoaderError(f"{path.name}: not a regular file")
        if info.st_size > MAX_CASE_FILE_BYTES:
            raise LoaderError(f"{path.name}: larger than {MAX_CASE_FILE_BYTES} bytes")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_CASE_FILE_BYTES + 1)
    finally:
        if fd is not None:
            os.close(fd)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise LoaderError(f"{path.name}: not UTF-8") from None


def parse_case_file(path):
    """The validated cases of one JSONL file. Blank lines are skipped."""
    cases = []
    for number, line in enumerate(_read_regular_file(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            raise LoaderError(f"{path.name}:{number}: not valid JSON") from None
        try:
            cases.append(validate_case(raw))
        except CaseError as exc:
            raise LoaderError(f"{path.name}:{number}: {exc}") from None
    return cases


def _collect(paths):
    cases, seen = [], set()
    for path in paths:
        for case in parse_case_file(path):
            if case.id in seen:
                raise LoaderError(f"{path.name}: duplicate case id {case.id!r}")
            seen.add(case.id)
            cases.append(case)
            if len(cases) > MAX_CASES:
                raise LoaderError(f"more than {MAX_CASES} cases")
    if not cases:
        raise LoaderError("the case set is empty")
    return cases


def load_public_cases(directory=PUBLIC_DIR):
    """The committed synthetic seed (CI runs it on FakeAdapter)."""
    directory = Path(directory)
    return _collect(sorted(directory.glob(f"*{CASE_SUFFIX}")))


def private_dir(setting_value):
    return Path(os.path.expanduser(str(setting_value)))


def check_private_dir(directory):
    """Refuse a private directory that others could read, or that holds
    anything but case files and `results/`. Returns the case file paths."""
    directory = Path(directory)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        raise LoaderError(f"private eval directory {directory} does not exist") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise LoaderError(f"private eval directory {directory} is not a real directory")
    if info.st_mode & 0o077:
        raise LoaderError(
            f"private eval directory {directory} is readable by others "
            f"(mode {stat.S_IMODE(info.st_mode):04o}); run `chmod 700` on it"
        )
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise LoaderError(f"private eval directory {directory} is not owned by this user")
    case_files = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if entry.is_symlink():
                raise LoaderError(f"{entry.name}: symlinks are not allowed in the private set")
            if entry.is_dir(follow_symlinks=False):
                if entry.name == RESULTS_DIRNAME:
                    continue
                raise LoaderError(f"{entry.name}: only a '{RESULTS_DIRNAME}/' directory is allowed")
            if not entry.name.endswith(CASE_SUFFIX):
                raise LoaderError(f"{entry.name}: only *{CASE_SUFFIX} case files are allowed")
            case_files.append(directory / entry.name)
    return case_files


def load_private_cases(directory):
    """The owner's private set (D9), after `check_private_dir`."""
    return _collect(check_private_dir(directory))


def stray_case_files(root=CASES_DIR):
    """Every file under `root` outside `public/` (D9: private cases are never
    committed; a test fails when this is non-empty)."""
    root = Path(root)
    public = root / "public"
    strays = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        if public in path.parents:
            continue
        strays.append(path.relative_to(root))
    return strays
