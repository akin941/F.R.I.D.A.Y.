"""A fixture home for the reference node (tests, the conformance suite and
the escape suite).

`build_fixture_home(base)` creates, under `base/home`:

- `work/acme` and `code/friday`: git repos with one commit each (the two
  repos `local_policy.json` maps);
- `work/other`: a directory no mapping names;
- `work/link`: a symlink to `work/other` (the `not_configured` symlink
  case: a path that looks like a repo but resolves elsewhere);
- `work/acme-link`: a symlink to `work/acme` (resolves to a mapped root).

Every case file names repos as `~/work/acme`, so the suites run on any
machine. Git runs with hooks disabled and a fixed identity.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from nodes.reference.local_policy import LocalPolicy

FIXTURES_DIR = Path(__file__).resolve().parent
POLICY_PATH = FIXTURES_DIR / "local_policy.json"
_GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "GIT_AUTHOR_NAME": "Friday Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Friday Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-11-20T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-11-20T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1",
    "LANG": "C",
}


def policy_document() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _git(repo, *args):
    git = shutil.which("git", path=_GIT_ENV["PATH"])
    if git is None:
        raise RuntimeError("git is required for the fixture repos")
    subprocess.run(  # noqa: S603 - argv list, no shell
        [git, "-c", "core.hooksPath=/dev/null", "-c", "init.defaultBranch=main", *args],
        cwd=repo,
        env={**_GIT_ENV, "HOME": str(repo)},
        check=True,
        capture_output=True,
        timeout=60,
    )


def make_repo(path: Path, name: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    (path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "fixture")
    return path


def build_fixture_home(base) -> Path:
    """Create the fixture home under `base` and return its realpath."""
    home = Path(base) / "home"
    make_repo(home / "work" / "acme", "acme")
    make_repo(home / "code" / "friday", "friday")
    (home / "work" / "other").mkdir(parents=True, exist_ok=True)
    os.symlink(home / "work" / "other", home / "work" / "link")
    os.symlink(home / "work" / "acme", home / "work" / "acme-link")
    return Path(os.path.realpath(home))


def fixture_policy(home) -> LocalPolicy:
    return LocalPolicy.from_document(policy_document(), home=str(home))
