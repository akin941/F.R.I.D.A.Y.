#!/usr/bin/env bash
# Corpus recall gate for the Swift node's scrubber (Phase 9 brief M2,
# contract test 2): run `friday-node scrub` over every item of
# backend/memory/tests/scrubber_corpus/ and compare with the labels, the
# same gates as backend/memory/tests/test_scrubber.py:
#   - secrets: 100% recall (no needle survives) and the typed placeholder;
#   - PII: 100% recall and the typed placeholder;
#   - benign: unchanged.
#
#   node/scripts/scrubber-corpus.sh            # builds the CLI if needed
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PKG="$ROOT/node/friday-node"
(cd "$PKG" && swift build --product friday-node >/dev/null)
CLI="$PKG/.build/debug/friday-node"
python3 -I - "$CLI" "$ROOT/backend/memory/tests/scrubber_corpus" <<'PY'
import json, subprocess, sys
from pathlib import Path

cli, corpus = sys.argv[1], Path(sys.argv[2])
rows = []
for path in sorted(corpus.glob("*.jsonl")):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["text"] = row["text"].replace("<<>>", "")
            row["needles"] = [n.replace("<<>>", "") for n in row.get("needles", [])]
            row["file"] = path.name
            rows.append(row)

def scrub(text):
    return subprocess.run([cli, "scrub", "--stdin"], input=text.encode(), capture_output=True, check=True).stdout.decode()

failures = []
counts = {"secret": 0, "pii": 0, "benign": 0}
for row in rows:
    out = scrub(row["text"])
    kind = row["class"]
    counts[kind] += 1
    if kind == "benign":
        if out != row["text"]:
            failures.append(f"benign changed: {row['file']}:{row['id']}")
        continue
    if any(needle in out for needle in row["needles"]):
        failures.append(f"{kind} missed: {row['file']}:{row['id']}")
    tag = "SECRET" if kind == "secret" else "PII"
    if f"[{tag}:{row['type']}]" not in out:
        failures.append(f"{kind} without [{tag}:{row['type']}]: {row['file']}:{row['id']}")

print(f"scrubber corpus via friday-node: {counts['secret']} secret, {counts['pii']} pii, {counts['benign']} benign")
for failure in failures:
    print("  FAIL", failure)
print("secrets recall 100%, pii recall 100%, benign unchanged" if not failures else f"FAILED: {len(failures)}")
sys.exit(1 if failures else 0)
PY
