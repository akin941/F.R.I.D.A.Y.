"""Render the conformance cases and frame vectors to JSON, or `--check`
them (the `build_node_conformance --check` pattern, brief D6).

    friday-voice conformance --build   # write the files
    friday-voice conformance --check   # regenerate nothing; fail on drift

The files are what the Swift host's test target loads, so a definition
change without a rebuild, or a hand edit of a JSON file, fails CI.
"""

import json
from pathlib import Path

from .definitions import child_cases, frame_vectors, host_cases

PACKAGE_DIR = Path(__file__).resolve().parents[1]
CASES_DIR = PACKAGE_DIR / "conformance" / "cases"
VECTORS_FILE = PACKAGE_DIR / "protocol" / "vectors" / "frames.json"


def _dump(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def render() -> dict:
    """{absolute path: file text} for every generated file."""
    files = {VECTORS_FILE: _dump(frame_vectors())}
    for family, cases in (("host", host_cases()), ("child", child_cases())):
        ids = [case["id"] for case in cases]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate {family} case id")
        for case in cases:
            files[CASES_DIR / family / f"{case['id']}.json"] = _dump(case)
    return files


def _on_disk() -> set:
    found = set(CASES_DIR.glob("*/*.json"))
    if VECTORS_FILE.exists():
        found.add(VECTORS_FILE)
    return found


def write() -> list:
    files = render()
    for stale in _on_disk() - set(files):
        stale.unlink()
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


def check() -> list:
    """The relative paths that drifted (changed, missing or unexpected)."""
    files = render()
    drift = []
    for path, text in files.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            drift.append(path)
    drift += sorted(_on_disk() - set(files))
    return [str(path.relative_to(PACKAGE_DIR.parent)) for path in drift]


def load_cases(family, cases_dir=None) -> list:
    """The case files of one family, as the Swift test target reads them."""
    root = Path(cases_dir) if cases_dir else CASES_DIR
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / family).glob("*.json"))
    ]
