"""`friday-voice/v1` framing (brief D6).

    frame = length:u32 big-endian (1 .. 1 MiB) || body
    body  = header (canonical UTF-8 JSON object, no raw newline) || "\\n" || payload

Only the audio types (`audio` host->child, `play` child->host) carry a
payload: 16 kHz mono PCM16 little-endian, `pcm_bytes` long (<= 64 KiB, even).
Every other type has an empty payload. The canonical header encoding is
sorted keys, no whitespace, UTF-8 (non-ASCII kept), so a decoded vector
re-encodes to the same bytes.

Any violation is a ProtocolError with a code (`messages.FRAMING_ERRORS`);
the receiver logs the code, drops the link and, on the host, releases the
mic. Header schema checks run when a direction is given.
"""

import json
import struct

from .messages import (
    AUDIO_TYPES,
    MAX_FRAME_BYTES,
    MAX_HEADER_BYTES,
    MAX_PCM_BYTES,
    ProtocolError,
    validate,
)

LENGTH = struct.Struct(">I")


def canonical_header(header) -> bytes:
    return json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def encode_frame(header, payload=b"", *, direction=None) -> bytes:
    """Encode one frame. For an audio type `pcm_bytes` is set from the
    payload. With `direction`, the header is schema-checked first."""
    header = dict(header)
    payload = bytes(payload)
    if header.get("type") in AUDIO_TYPES:
        header["pcm_bytes"] = len(payload)
    _check_payload(header, payload)
    if direction is not None:
        validate(header, direction)
    body = canonical_header(header) + b"\n" + payload
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return LENGTH.pack(len(body)) + body


def raw_frame(header, payload=b"", *, length=None) -> bytes:
    """A frame with NO checks (hostile peers, invalid vectors): `header` is
    a dict (canonically encoded) or raw bytes; `length` overrides the
    prefix."""
    head = header if isinstance(header, bytes | bytearray) else canonical_header(header)
    body = bytes(head) + b"\n" + bytes(payload)
    return LENGTH.pack(len(body) if length is None else length) + body


def _reject_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ProtocolError("duplicate_key")
        seen[key] = value
    return seen


def _reject_float(_text):
    raise ProtocolError("number_not_integer")


def _reject_constant(_text):
    raise ProtocolError("invalid_json")


def _check_payload(header, payload):
    message_type = header.get("type")
    if message_type in AUDIO_TYPES:
        declared = header.get("pcm_bytes")
        if type(declared) is not int or declared != len(payload):
            raise ProtocolError("payload_length_mismatch")
        if len(payload) > MAX_PCM_BYTES:
            raise ProtocolError("pcm_too_large")
        if len(payload) % 2:
            raise ProtocolError("pcm_odd_length")
    elif payload:
        raise ProtocolError("payload_unexpected")


def check_length(length) -> int:
    if length == 0:
        raise ProtocolError("frame_empty")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    return length


def decode_body(body, *, direction=None):
    """Decode one frame body into (header, payload)."""
    newline = body.find(b"\n", 0, MAX_HEADER_BYTES + 1)
    if newline < 0:
        if len(body) > MAX_HEADER_BYTES:
            raise ProtocolError("header_too_large")
        raise ProtocolError("header_unterminated")
    raw_header, payload = body[:newline], body[newline + 1 :]
    try:
        text = raw_header.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8") from exc
    try:
        header = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("invalid_json") from exc
    if not isinstance(header, dict):
        raise ProtocolError("header_not_object")
    _check_payload(header, payload)
    if direction is not None:
        validate(header, direction)
    return header, bytes(payload)


def decode_frame(data, *, direction=None):
    """Decode exactly one complete frame (prefix included)."""
    if len(data) < LENGTH.size:
        raise ProtocolError("short_read")
    (length,) = LENGTH.unpack_from(data)
    check_length(length)
    body = data[LENGTH.size :]
    if len(body) != length:
        raise ProtocolError("short_read" if len(body) < length else "payload_length_mismatch")
    return decode_body(body, direction=direction)


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream, *, direction=None):
    """Read one frame from a blocking binary stream. Returns None on a clean
    EOF at a frame boundary; raises ProtocolError(`short_read`) when the
    stream ends inside a frame. An over-length prefix is refused before a
    byte of its body is read."""
    prefix = _read_exact(stream, LENGTH.size)
    if not prefix:
        return None
    if len(prefix) < LENGTH.size:
        raise ProtocolError("short_read")
    (length,) = LENGTH.unpack(prefix)
    check_length(length)
    body = _read_exact(stream, length)
    if len(body) < length:
        raise ProtocolError("short_read")
    return decode_body(body, direction=direction)


class FrameDecoder:
    """Incremental decoder for a byte stream (the reference host's side).
    `feed` returns `(frames, error_code)`: the complete frames decoded before
    any error, then the error (or None). After an error the decoder is dead
    (the link is dropped) and every later feed returns that error."""

    def __init__(self, *, direction=None):
        self.direction = direction
        self._buffer = bytearray()
        self.failed = None

    def feed(self, data):
        if self.failed:
            return [], self.failed
        self._buffer += data
        frames = []
        try:
            while len(self._buffer) >= LENGTH.size:
                (length,) = LENGTH.unpack_from(self._buffer)
                check_length(length)
                if len(self._buffer) < LENGTH.size + length:
                    break
                body = bytes(self._buffer[LENGTH.size : LENGTH.size + length])
                del self._buffer[: LENGTH.size + length]
                frames.append(decode_body(body, direction=self.direction))
        except ProtocolError as exc:
            self.failed = exc.code
            self._buffer.clear()
        return frames, self.failed

    def close(self):
        """End of stream: returns `short_read` when bytes were left over
        inside a frame, else the earlier error or None."""
        if self._buffer and not self.failed:
            self.failed = "short_read"
            self._buffer.clear()
        return self.failed
