"""Canonical JSON for protocol v1 (brief D5): the RFC 8785 (JCS) subset the
v1 schemas allow.

- UTF-8 output, no insignificant whitespace;
- object keys sorted by their UTF-16 code units (JCS 3.2.3);
- integers only, within the I-JSON safe range (+-(2**53 - 1)), written in
  plain decimal; floats, NaN and infinities are refused (the schemas forbid
  non-integer numbers, so JCS's ECMAScript number formatting never applies);
- strings escaped per JCS 3.2.2.2: `"` and `\\` backslash-escaped, the
  control characters U+0008, U+0009, U+000A, U+000C, U+000D as `\\b \\t \\n
  \\f \\r`, every other control character below U+0020 as `\\u00xx` (lower
  case hex); everything else, including non-ASCII and U+007F, literal;
- lone surrogates are refused (they have no UTF-8 encoding).

`parse_strict` is the matching parser for frames received off the wire: it
refuses duplicate keys, non-integer numbers and the NaN/Infinity extensions
Python's `json` module would otherwise accept.

The test vectors in `vectors/canonical.json` are shared with the Swift
implementation (Mac session M1).
"""

import hashlib
import json

MAX_SAFE_INTEGER = 2**53 - 1

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class CanonicalizationError(ValueError):
    """The value is outside the canonical-JSON subset (a float, a non-string
    key, an out-of-range integer, a lone surrogate, an unsupported type),
    or a received frame is not strict JSON."""


def _escape(text):
    out = ['"']
    for char in text:
        escaped = _SHORT_ESCAPES.get(char)
        if escaped is not None:
            out.append(escaped)
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        elif 0xD800 <= ord(char) <= 0xDFFF:
            raise CanonicalizationError("lone surrogate in string")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _utf16_key(key):
    return key.encode("utf-16-be", "surrogatepass")


def _serialize(value, out):
    # bool before int: bool is an int subclass.
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer outside the I-JSON safe range")
        out.append(str(int(value)))
    elif isinstance(value, float):
        raise CanonicalizationError("floats are not allowed in canonical JSON v1")
    elif isinstance(value, str):
        out.append(_escape(value))
    elif isinstance(value, list | tuple):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError("object keys must be strings")
        out.append("{")
        for index, key in enumerate(sorted(value, key=_utf16_key)):
            if index:
                out.append(",")
            out.append(_escape(key))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
    else:
        raise CanonicalizationError(f"unsupported type {type(value).__name__}")


def canonicalize(value) -> bytes:
    """The canonical JSON bytes of `value` (UTF-8)."""
    out = []
    _serialize(value, out)
    try:
        return "".join(out).encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - _escape refuses first
        raise CanonicalizationError("string has no UTF-8 encoding") from exc


def sha256_hex(value) -> str:
    """sha256 (hex) of the canonical JSON of `value` -- the `args_sha256`,
    `output_sha256` and `catalogue_sha256` rule."""
    return hashlib.sha256(canonicalize(value)).hexdigest()


def _no_float(text):
    raise CanonicalizationError("non-integer numbers are not allowed")


def _no_constant(name):
    raise CanonicalizationError(f"{name} is not JSON")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalizationError("duplicate object key")
        result[key] = value
    return result


def _check_ints(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalizationError("integer outside the I-JSON safe range")
    elif isinstance(value, list):
        for item in value:
            _check_ints(item)
    elif isinstance(value, dict):
        for item in value.values():
            _check_ints(item)


def parse_strict(raw):
    """Parse one received frame (`bytes` or `str`) as strict JSON: UTF-8,
    no duplicate keys, integers only (in the safe range), no NaN/Infinity.
    Raises CanonicalizationError on anything else."""
    if isinstance(raw, bytes | bytearray):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CanonicalizationError("frame is not UTF-8") from exc
    try:
        value = json.loads(
            raw,
            parse_float=_no_float,
            parse_constant=_no_constant,
            object_pairs_hook=_unique_keys,
        )
    except CanonicalizationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise CanonicalizationError("frame is not valid JSON") from exc
    _check_ints(value)
    return value
