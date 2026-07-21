"""Deterministic, model-free chunking (MVP 3 §3): fixed-size character
windows with overlap, snapped to a paragraph or sentence boundary where one
exists nearby, with a hard character cap as the fallback. Shared by branch 2's
synchronous `pasted_text`/`file_upload` path and branch 3's `url` worker —
one implementation, called from both places.
"""

from django.conf import settings

_PARAGRAPH_BREAK = "\n\n"
_SENTENCE_ENDS = (". ", ".\n", "! ", "!\n", "? ", "?\n")


def _find_boundary(window):
    """Search backwards in `window` for a paragraph break, then a sentence
    end. Returns the offset (within `window`) just past the break, or -1 if
    neither exists."""
    para = window.rfind(_PARAGRAPH_BREAK)
    if para > 0:
        return para + len(_PARAGRAPH_BREAK)
    best_pos = -1
    best_len = 0
    for marker in _SENTENCE_ENDS:
        pos = window.rfind(marker)
        if pos > best_pos:
            best_pos = pos
            best_len = len(marker)
    if best_pos > 0:
        return best_pos + best_len
    return -1


def chunk_text(text, *, chunk_size=None, overlap=None):
    """Split `text` into a list of non-empty, stripped chunk strings.
    Deterministic and unit-testable without a model."""
    chunk_size = chunk_size if chunk_size is not None else settings.KNOWLEDGE_CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.KNOWLEDGE_CHUNK_OVERLAP
    # Clamp so overlap can never eat all of a chunk's forward progress — an
    # env-misconfigured overlap >= chunk_size previously advanced `start` by
    # only 1 char per iteration, turning a 5000-char document into ~4000
    # near-duplicate chunks (review finding). Half of chunk_size always
    # leaves at least half a chunk's worth of new content per step.
    overlap = max(0, min(overlap, chunk_size // 2))
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            # Only search the tail of the window, not the whole thing from
            # `start` — a boundary found near `start` would shrink the chunk
            # far below chunk_size, and (combined with overlap) could even
            # walk `start` backwards on the next iteration, producing many
            # tiny near-duplicate chunks (round-2 Codex review finding).
            tail = max(overlap, chunk_size // 4)
            search_start = max(start, end - tail)
            boundary = _find_boundary(text[search_start:end])
            if boundary > 0:
                end = search_start + boundary
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)  # always progress, even if overlap >= chunk
    return chunks


def estimate_token_count(text):
    """Crude fallback estimate (~4 chars/token), same formula as
    `ai_core.adapters.base.BaseAdapter.estimate_tokens`, duplicated here
    rather than imported — chunking has no need for the adapter machinery,
    and this keeps `knowledge` decoupled from ai_core internals it doesn't
    otherwise use in this branch."""
    return max(1, len(text) // 4)
