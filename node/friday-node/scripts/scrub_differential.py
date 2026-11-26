#!/usr/bin/env python3
"""Differential check: the Swift scrubber against the Python one.

Generates N inputs by mutating the scrubber corpus and the reader fixtures
(Turkish dotted/dotless i, long s, Kelvin sign, sharp s, Python-only
whitespace such as U+001C/U+000B/U+0085, non-ASCII decimal digits, marks,
emoji, case flips, separators), scrubs each with
backend/memory/ingestion/scrubber.py and with `friday-node scrub --jsonl`,
and reports every input whose outputs differ.

    cd backend && uv run python ../node/friday-node/scripts/scrub_differential.py [N] [SEED]
"""

import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
from memory.ingestion.scrubber import scrub_text  # noqa: E402

CLI = REPO / "node/friday-node/.build/debug/friday-node"
POOL = list("İıſKßŞşĞğÜüÖöÇç\u088f\uf882\U00010d40\U0001e5f1\u001c\u001d\u000b\u0085  　٣٤٥۷०१２３́‍😀_-.:=@/+ \t\n\"'")
SEEDS = [
    "token: abcdefghijklmnopqrstuvwxyz0123",
    "API_KEY=sk-proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAB7CD0",
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
    "https://user:p4ss@host.example.com/x",
    "mail ayse.yilmaz@firma.com.tr now",
    "+90 532 123 45 67",
    "TR33 0006 1005 1978 6457 8413 26",
    "4111 1111 1111 1111",
    "10000000146",
    "ghp_" + "A1b2C3d4" * 4,
    "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
    'password = "correct horse battery"',
    "AIzaSyA1234567890abcdefghijklmnopqrstu",
    "xoxb-1234567890-abc",
    "Q2xhdWRlIENvZGUgc2Vzc2lvbiBpbiAvVXNlcnMvb3du",
    "/Users/owner/code/friday",
    "commit 3f2a9c1e7b2d4f6a0e5c9b8d7a6f4e3c2b1a0f9e",
]


def corpus_texts():
    texts = list(SEEDS)
    for path in sorted((REPO / "backend/memory/tests/scrubber_corpus").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["text"].replace("<<>>", ""))
    vectors = json.loads((REPO / "backend/nodes/conformance/vectors/scrub.json").read_text())
    import base64

    texts += [base64.b64decode(item["input_b64"]).decode("utf-8") for item in vectors["items"]]
    return texts


def mutate(rng, text):
    chars = list(text)
    for _ in range(rng.randint(1, 6)):
        op = rng.random()
        index = rng.randint(0, len(chars)) if chars else 0
        if op < 0.35:
            chars.insert(index, rng.choice(POOL))
        elif op < 0.55 and chars:
            i = min(index, len(chars) - 1)
            c = chars[i]
            chars[i] = (
                c.swapcase()
                if rng.random() < 0.5
                else {"i": "ı", "I": "İ", "s": "ſ", "k": "K"}.get(c, c)
            )
        elif op < 0.7 and chars:
            del chars[min(index, len(chars) - 1)]
        elif op < 0.85:
            chars[index:index] = list(rng.choice(SEEDS))
        else:
            chars[index:index] = [rng.choice("0123456789٣०２")] * rng.randint(1, 14)
    return "".join(chars)


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    base = corpus_texts()
    inputs = base + [mutate(rng, rng.choice(base)) for _ in range(count)]
    payload = "".join(json.dumps(text) + "\n" for text in inputs)
    swift = subprocess.run(
        [str(CLI), "scrub", "--jsonl"], input=payload.encode(), capture_output=True, check=True
    )
    outputs = [json.loads(line) for line in swift.stdout.decode().split("\n") if line]
    assert len(outputs) == len(inputs)
    diffs = [(i, t) for i, t in enumerate(inputs) if scrub_text(t).text != outputs[i]]
    print(f"{len(inputs)} inputs, {len(diffs)} differ")
    for index, text in diffs[:15]:
        print(
            json.dumps(
                {"input": text, "python": scrub_text(text).text, "swift": outputs[index]},
                ensure_ascii=False,
            )
        )
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
