"""The ingestion scrubber (ADR-012 decision 3, Phase 8A brief D3).

Pure functions over strings: standard library only, no Django, no network,
no I/O. This module is the source of truth that `friday-hook`'s vendored copy
(Phase 8A branch 3) is generated from and checked identical against, so it
must stay importable on its own.

Three rule classes run in a fixed order, each hit replaced by a **typed
placeholder** so a later reader (the extraction model, the owner's audit
view) still sees *that* something was there, never *what*:

1. secrets -- provider key prefixes, JWTs, PEM blocks, `KEY=value`
   assignments whose key names a secret, URL credentials, bearer tokens and
   a Shannon-entropy check for long base64/hex runs -> `[SECRET:<type>]`;
2. PII -- IBAN (mod-97), card numbers (Luhn), TCKN (official checksum),
   e-mail, Turkish and E.164 phone numbers -> `[PII:<type>]`;
3. the workspace's own `ScrubTerm`s (customer names, project code names),
   case-insensitive literals, longest first -> `[TERM:<id>]`.

`scrub_text` returns the scrubbed text and a report of **counts per
placeholder type** -- never the matched text, so the report is safe to store
(`Episode.scrub_report`) and to show.

Recall over precision: the secrets class is a release gate (100% recall on
`memory/tests/scrubber_corpus/`); over-scrubbing is tolerated and tuned.
Placeholders are idempotent: running the scrubber over its own output
changes nothing (the second scrub before every gateway call relies on it).
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

SECRET = "SECRET"
PII = "PII"
TERM = "TERM"

# A placeholder this module emitted; never re-scrubbed.
PLACEHOLDER_RE = re.compile(r"\[(?:SECRET|PII|TERM):[a-z0-9_]+\]")


def placeholder(kind, name):
    return f"[{kind}:{name}]"


# --- secret rules -----------------------------------------------------------
# (type, pattern, group to replace or None for the whole match). Order
# matters: the more specific prefix first (`sk-ant-` before `sk-`), PEM
# blocks before anything that could match inside them.

_SECRET_WORDS = (
    r"(?:secret|token|password|passwd|pwd|api[_-]?key|private[_-]?key|access[_-]?key|credential)"
)

_SECRET_RULES = [
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
            r"(?:[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|[\s\S]*\Z)"
        ),
        None,
    ),
    (
        "pem_block",
        re.compile(r"-----BEGIN [A-Z0-9 ]+-----[\s\S]*?-----END [A-Z0-9 ]+-----"),
        None,
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), None),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}"), None),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), None),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), None),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), None),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"), None),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), None),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"), None),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        None,
    ),
    # scheme://user:password@host -> keep the scheme and host.
    (
        "url_credentials",
        re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://([^\s/:@\[\]]+:[^\s/@\[\]]+)@"),
        1,
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(?:bearer|basic|token)\s+([A-Za-z0-9._~+/\-]{16,}=*)"),
        1,
    ),
    # KEY=value / "key": "value" / key: value where the key names a secret.
    (
        "assignment",
        re.compile(
            r"(?i)[A-Za-z0-9_.\-]*" + _SECRET_WORDS + r"[A-Za-z0-9_.\-]*"
            r"[\"']?\s*(?:=|:)\s*[\"']?([^\s\"',;]+)"
        ),
        1,
    ),
]

# Entropy rule: long runs of base64/hex alphabet. Thresholds tuned on the
# corpus (`memory/tests/scrubber_corpus/benign.jsonl` keeps paths, git
# object ids and prose untouched).
_B64_RUN = re.compile(r"[A-Za-z0-9+/_\-]{32,}={0,2}")
_HEX_ONLY = re.compile(r"\A[0-9a-fA-F]+\Z")
# A 40/64-hex run is a git object id (sha1/sha256), kept: commit episodes
# cite them. Every other long hex run of high entropy is a secret.
_GIT_OBJECT_ID_LENGTHS = {40, 64}


def shannon_entropy(value):
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _looks_like_secret_run(run):
    if _HEX_ONLY.match(run):
        if len(run) in _GIT_OBJECT_ID_LENGTHS and run == run.lower():
            return False
        return shannon_entropy(run) >= 3.0
    upper = sum(c.isupper() for c in run)
    lower = sum(c.islower() for c in run)
    digits = sum(c.isdigit() for c in run)
    # Paths and identifiers in prose have few digits and few capitals.
    if digits < 2 or upper < 2 or lower < 2:
        return False
    return shannon_entropy(run) >= 4.0


# --- PII rules ---------------------------------------------------------------

_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_CARD_CANDIDATE = re.compile(r"(?<![\d\-])\d(?:[ \-]?\d){12,18}(?![\d])")
_TCKN_CANDIDATE = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}\b")
# Turkish numbers: mobile 5xx and landline 2xx-4xx, with +90 / 0090 / 0
# prefixes and the usual separators; then any other E.164 number.
_PHONE_TR = re.compile(
    r"(?<![\w+])(?:(?:\+|00)90[\s.\-]?|0)?\(?[2-5]\d{2}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)"
)
_PHONE_E164 = re.compile(r"(?<![\w+])\+[1-9](?:[\s.\-]?\d){7,14}(?!\d)")


def iban_is_valid(candidate):
    compact = candidate.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def luhn_is_valid(candidate):
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def tckn_is_valid(candidate):
    if not re.fullmatch(r"[1-9]\d{10}", candidate):
        return False
    d = [int(c) for c in candidate]
    tenth = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    if tenth != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


# --- engine ----------------------------------------------------------------


@dataclass
class ScrubResult:
    text: str
    report: dict = field(default_factory=dict)

    @property
    def hits(self):
        return sum(self.report.values())


class _Scrubber:
    def __init__(self, terms):
        self.counts = Counter()
        # Longest first, so "Acme Holding" wins over "Acme".
        self.terms = sorted(
            ((str(term_id), term) for term_id, term in terms if term and term.strip()),
            key=lambda item: len(item[1]),
            reverse=True,
        )

    def _hit(self, kind, name):
        self.counts[f"{kind}:{name}"] += 1
        return placeholder(kind, name)

    @staticmethod
    def _protected(text, start, end):
        """True when [start, end) overlaps an existing placeholder."""
        for match in PLACEHOLDER_RE.finditer(text):
            if match.start() < end and start < match.end():
                return True
        return False

    def _apply(self, text, pattern, kind, name, group=None, validate=None):
        out = []
        last = 0
        for match in pattern.finditer(text):
            start, end = match.span(group) if group else match.span()
            if start < last or self._protected(text, start, end):
                continue
            value = text[start:end]
            if PLACEHOLDER_RE.fullmatch(value):
                continue
            if validate is not None:
                trimmed = validate(value)
                if trimmed is None:
                    continue
                end = start + len(trimmed)
            out.append(text[last:start])
            out.append(self._hit(kind, name))
            last = end
        out.append(text[last:])
        return "".join(out)

    def secrets(self, text):
        for name, pattern, group in _SECRET_RULES:
            text = self._apply(text, pattern, SECRET, name, group)
        return self._apply(
            text,
            _B64_RUN,
            SECRET,
            "high_entropy",
            validate=lambda run: run if _looks_like_secret_run(run) else None,
        )

    def pii(self, text):
        text = self._apply(text, _IBAN_CANDIDATE, PII, "iban", validate=_longest_valid_iban)
        text = self._apply(
            text, _CARD_CANDIDATE, PII, "card", validate=lambda v: v if luhn_is_valid(v) else None
        )
        text = self._apply(
            text, _TCKN_CANDIDATE, PII, "tckn", validate=lambda v: v if tckn_is_valid(v) else None
        )
        text = self._apply(text, _EMAIL, PII, "email")
        text = self._apply(text, _PHONE_TR, PII, "phone")
        return self._apply(text, _PHONE_E164, PII, "phone")

    def term_rules(self, text):
        for term_id, term in self.terms:
            pattern = re.compile(re.escape(term.strip()), re.IGNORECASE)
            text = self._apply(text, pattern, TERM, _term_name(term_id))
        return text

    def run(self, text):
        text = self.secrets(text)
        text = self.pii(text)
        return self.term_rules(text)


def _term_name(term_id):
    """A short, stable, placeholder-safe name for a ScrubTerm id."""
    name = re.sub(r"[^a-z0-9]", "", str(term_id).lower())[:12]
    return name or "term"


def _longest_valid_iban(value):
    """The IBAN regex may run into a following uppercase word; trim trailing
    space-separated groups until the checksum holds."""
    candidate = value
    while len(candidate.replace(" ", "")) >= 15:
        if iban_is_valid(candidate):
            return candidate
        if " " in candidate:
            candidate = candidate.rsplit(" ", 1)[0]
        else:
            candidate = candidate[:-1]
    return None


def scrub_text(text, *, terms=()):
    """Scrub one string. `terms` is an iterable of `(id, literal)` pairs (the
    workspace's ScrubTerms). Returns a ScrubResult with counts only."""
    if not text:
        return ScrubResult(text or "", {})
    scrubber = _Scrubber(terms)
    return ScrubResult(scrubber.run(str(text)), dict(scrubber.counts))


def merge_reports(*reports):
    total = Counter()
    for report in reports:
        total.update(report or {})
    return dict(total)


# Metadata bounds (D2: "metadata (JSON, scrubbed values, bounded)").
METADATA_MAX_DEPTH = 3
METADATA_MAX_ITEMS = 50
METADATA_MAX_STRING = 500
METADATA_MAX_KEY = 64


def scrub_value(value, *, terms=(), _depth=0):
    """Scrub a JSON-like value recursively (dict keys too) and bound it:
    depth, items per container, string length. Non-JSON scalars become their
    scrubbed `str()`. Returns (value, report)."""
    if value is None or isinstance(value, bool | int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None, {}
        return value, {}
    if isinstance(value, str):
        result = scrub_text(value, terms=terms)
        return result.text[:METADATA_MAX_STRING], result.report
    if _depth >= METADATA_MAX_DEPTH:
        return None, {}
    if isinstance(value, dict):
        out, reports = {}, []
        for key, item in list(value.items())[:METADATA_MAX_ITEMS]:
            key_result = scrub_text(str(key), terms=terms)
            scrubbed, report = scrub_value(item, terms=terms, _depth=_depth + 1)
            out[key_result.text[:METADATA_MAX_KEY]] = scrubbed
            reports.extend([key_result.report, report])
        return out, merge_reports(*reports)
    if isinstance(value, list | tuple):
        out, reports = [], []
        for item in list(value)[:METADATA_MAX_ITEMS]:
            scrubbed, report = scrub_value(item, terms=terms, _depth=_depth + 1)
            out.append(scrubbed)
            reports.append(report)
        return out, merge_reports(*reports)
    return scrub_value(str(value), terms=terms, _depth=_depth)
