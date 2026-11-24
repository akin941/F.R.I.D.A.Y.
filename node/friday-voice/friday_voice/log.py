"""The child's log (Phase 11 brief D16): stderr only, one line per event,
a code plus a short detail scrubbed with the vendored ADR-012 scrubber.

Never a transcript, reply text, audio or token: callers pass a code and,
at most, a short technical detail (an HTTP status, an error class name).
The scrubber is the second line of defence, not the first. The host writes
these lines (and the `log` frames built by `log_message`) to its rotated
local log (ADR-013 decision 12).
"""

import re
import sys

from ._scrubber_vendored import scrub_text, truncate_scrubbed

CODE_RE = re.compile(r"[a-z][a-z0-9_.]{0,47}")
MAX_DETAIL_CHARS = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_detail(detail) -> str:
    """Scrub, flatten to one line and cut `detail` to MAX_DETAIL_CHARS."""
    if not detail:
        return ""
    text = _CONTROL.sub(" ", str(detail))
    return truncate_scrubbed(scrub_text(text).text, MAX_DETAIL_CHARS).strip()


def check_code(code) -> str:
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ValueError("a log code is lower-case [a-z0-9_.], at most 48 characters")
    return code


def format_line(code, detail="") -> str:
    detail = clean_detail(detail)
    return f"friday-voice {check_code(code)}" + (f" {detail}" if detail else "")


def emit(code, detail="", *, stream=None) -> None:
    stream = sys.stderr if stream is None else stream
    try:
        stream.write(format_line(code, detail) + "\n")
        stream.flush()
    except (OSError, ValueError):  # a closed stderr never takes the child down
        pass


def log_message(code, detail="") -> dict:
    """A `log` frame header for the host (child -> host)."""
    return {"type": "log", "code": check_code(code), "detail": clean_detail(detail)}
