"""Provenance checks for learned proposals (Phase 8A brief D9).

A model-made candidate survives only when its `quote` is a verbatim
substring of owner-authored text: the cited episode's `owner_text`
(episode extraction) or a `user`-role message of the job's range (chat
extraction v2). Whitespace is normalised on both sides (runs collapse to one
space, ends stripped) so a re-wrapped line still matches; nothing else is
forgiven -- a paraphrase, a change of case or punctuation, or a quote of
fenced context fails. Enforced in code, never trusted to a prompt.
"""

import re

MIN_QUOTE_CHARS = 12
MAX_STORED_QUOTE_CHARS = 300

_WHITESPACE = re.compile(r"\s+")


def normalise(text):
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def quote_is_verbatim(quote, *owner_texts):
    """True when `quote` (>= MIN_QUOTE_CHARS after normalisation) occurs
    verbatim in at least one of `owner_texts`."""
    needle = normalise(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return False
    return any(needle in normalise(text) for text in owner_texts if text)


def stored_quote(quote):
    """The bounded form kept on the proposal and shown on the card."""
    return normalise(quote)[:MAX_STORED_QUOTE_CHARS]
