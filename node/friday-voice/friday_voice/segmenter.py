"""Reply text -> speakable sentences: a Python port of the browser's
`frontend/src/lib/voice/segmenter.ts` (Phase 6 D7, Phase 11 brief D8).

The reply streams as Markdown chunks; `SentenceSegmenter` turns it into
plain sentences of 20-300 characters as soon as each one is complete, so
the first sentence can be synthesized while the rest still streams. The
rules are the browser's, line for line (code blocks -> "Kod ekranda.",
tables -> "Tablo ekranda.", links -> labels, bare URLs -> "bağlantı",
no boundary after a Turkish/English abbreviation, an initial or inside a
decimal, never at the very end of the streamed text).

The shared vectors `node/friday-voice/vectors/segmenter.json` run in pytest
(tests/test_segmenter.py) **and** in vitest
(`frontend/src/lib/voice/segmenter.vectors.test.ts`), so the two
implementations can never drift.
"""

import re

MIN_SENTENCE_CHARS = 20
MAX_SENTENCE_CHARS = 300

CODE_PLACEHOLDER = "Kod ekranda."
TABLE_PLACEHOLDER = "Tablo ekranda."

ABBREVIATIONS = frozenset(
    {
        "dr",
        "prof",
        "doç",
        "doc",
        "yrd",
        "av",
        "sn",
        "bkz",
        "örn",
        "orn",
        "vb",
        "vs",
        "yy",
        "no",
        "tel",
        "mah",
        "cad",
        "sok",
        "apt",
        "st",
        "mr",
        "mrs",
        "ms",
        "jr",
        "sr",
        "etc",
        "vol",
        "fig",
        "ör",
    }
)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE = re.compile(r"^\s*>\s?")
_LIST = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC = re.compile(r"(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,!?:;]|$)")
_STRIKE = re.compile(r"~~(.+?)~~")
_URL = re.compile(r"<https?://[^>\s]+>|https?://\S+")
_SPACES = re.compile(r"\s+")
_BOUNDARY = re.compile(r"([.!?…]+)[\"')\]]*(?=\s)|\n")
_WORD_AT_END = re.compile(r"([^\W\d_]+)$")
_TABLE_ROW = re.compile(r"^\s*\|")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_MAYBE_BLOCK = re.compile(r"^\s*[`~|]")
_BLANK = re.compile(r"^\s*$")


def turkish_lower(text: str) -> str:
    """`toLocaleLowerCase("tr")`: dotted and dotless I first."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def speakable_line(line: str) -> str:
    """One Markdown line -> speakable text (block markers + inline syntax)."""
    text = _HEADING.sub("", line, count=1)
    text = _QUOTE.sub("", text, count=1)
    text = _LIST.sub("", text, count=1)
    text = _RULE.sub("", text, count=1)
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC.sub(r"\1\2", text)
    text = _STRIKE.sub(r"\1", text)
    text = _URL.sub("bağlantı", text)
    return _collapse(text)


def to_speakable(markdown: str) -> str:
    """A whole Markdown reply -> one speakable string."""
    segmenter = SentenceSegmenter()
    return " ".join([*segmenter.push(markdown), *segmenter.flush()])


def _has_open_inline(text: str) -> bool:
    if text.count("`") % 2 == 1:
        return True
    if text.count("**") % 2 == 1:
        return True
    if text.count("[") > text.count("]"):
        return True
    return re.search(r"\]\([^)]*$", text) is not None


def _boundaries(text: str) -> list:
    """Index just after each sentence boundary in `text` (whitespace follows)."""
    result = []
    for match in _BOUNDARY.finditer(text):
        end = match.end()
        if match.group(0) == "\n":
            result.append(end)
            continue
        if match.group(1) == ".":
            before = text[: match.start()]
            found = _WORD_AT_END.search(before)
            word = found.group(1) if found else ""
            if word and turkish_lower(word) in ABBREVIATIONS:
                continue
            # An initial ("A.") or an ordinal number ("3. madde").
            if len(word) == 1 and word.isupper():
                continue
            if re.search(r"[0-9]$", before):
                after = re.match(r"\s+(.)", text[end:], re.DOTALL)
                if after and after.group(1).islower():
                    continue
        result.append(end)
    return result


def _split_long(sentence: str) -> list:
    """Split an over-long sentence at the last comma/space before the cap."""
    out = []
    rest = sentence
    while len(rest) > MAX_SENTENCE_CHARS:
        window = rest[:MAX_SENTENCE_CHARS]
        cut = max(window.rfind(", "), window.rfind("; "))
        if cut < MIN_SENTENCE_CHARS:
            cut = window.rfind(" ")
        if cut < MIN_SENTENCE_CHARS:
            cut = MAX_SENTENCE_CHARS - 1
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1 :].strip()
    if rest:
        out.append(rest)
    return out


class SentenceSegmenter:
    def __init__(self):
        self._raw = ""  # raw Markdown not yet converted
        self._speak = ""  # speakable text not yet emitted as a sentence
        self._in_fence = False
        self._last_was_table = False

    def push(self, chunk: str) -> list:
        """Feed one streamed chunk; returns the sentences it completed."""
        self._raw += chunk
        self._drain(final=False)
        return self._take(final=False)

    def flush(self) -> list:
        """The stream ended: everything left, as sentences."""
        self._drain(final=True)
        return self._take(final=True)

    def _append(self, text: str) -> None:
        if not text:
            return
        separator = " " if self._speak and not self._speak[-1].isspace() else ""
        self._speak += separator + text

    def _placeholder(self, text: str) -> None:
        self._append(f"{text}\n")  # its own sentence, ended by a newline boundary

    def _drain(self, final: bool) -> None:
        while self._raw:
            newline = self._raw.find("\n")
            if newline == -1 and not final:
                self._drain_partial_line()
                return
            line = self._raw if newline == -1 else self._raw[:newline]
            self._raw = "" if newline == -1 else self._raw[newline + 1 :]
            if self._in_fence:
                if _FENCE.search(line):
                    self._in_fence = False
                    self._placeholder(CODE_PLACEHOLDER)
                continue
            if _FENCE.search(line):
                self._in_fence = True
                self._last_was_table = False
                continue
            if _TABLE_ROW.search(line):
                if not self._last_was_table:
                    self._placeholder(TABLE_PLACEHOLDER)
                self._last_was_table = True
                continue
            self._last_was_table = False
            text = speakable_line(line)
            if text:
                self._append(f"{text}\n")
        if final and self._in_fence:
            # An unclosed fence at the end of the reply is still code.
            self._in_fence = False
            self._placeholder(CODE_PLACEHOLDER)

    def _drain_partial_line(self) -> None:
        """The trailing line without its newline: emit what is surely complete."""
        if self._in_fence:
            return
        line = self._raw
        if _MAYBE_BLOCK.search(line) or _BLANK.search(line):
            return  # could become a fence or a table row: wait for the newline
        cuts = [cut for cut in _boundaries(line) if not _has_open_inline(line[:cut])]
        if not cuts:
            return
        cut = cuts[-1]
        text = speakable_line(line[:cut])
        self._raw = line[cut:].lstrip()
        if text:
            self._append(f"{text} ")

    def _take(self, final: bool) -> list:
        out = []
        pending = ""
        consumed = 0
        for cut in _boundaries(self._speak):
            piece = _collapse(self._speak[consumed:cut])
            consumed = cut
            if not piece:
                continue
            pending = f"{pending} {piece}" if pending else piece
            if len(pending) >= MIN_SENTENCE_CHARS:
                out.extend(_split_long(pending))
                pending = ""
        rest = self._speak[consumed:]
        if pending:
            rest = f"{pending} {rest}"
        if final:
            last = _collapse(rest)
            if last:
                # A short tail joins the previous sentence when it fits.
                if (
                    len(last) < MIN_SENTENCE_CHARS
                    and out
                    and len(out[-1]) + len(last) < MAX_SENTENCE_CHARS
                ):
                    out[-1] = f"{out[-1]} {last}"
                else:
                    out.extend(_split_long(last))
            self._speak = ""
        else:
            self._speak = rest
        return out
