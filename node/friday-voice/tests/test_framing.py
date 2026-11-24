"""friday-voice/v1 framing (brief D6): vectors, refusal codes, streaming."""

import base64
import io
import json

import pytest

from friday_voice.protocol.framing import (
    LENGTH,
    FrameDecoder,
    canonical_header,
    decode_frame,
    encode_frame,
    raw_frame,
    read_frame,
)
from friday_voice.protocol.messages import (
    CHILD_TO_HOST,
    HOST_TO_CHILD,
    MAX_FRAME_BYTES,
    VECTORS_PATH,
    ProtocolError,
)

VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def _b(text):
    return base64.b64decode(text)


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda v: v["name"])
def test_every_valid_vector_round_trips(vector):
    frame = _b(vector["frame_b64"])
    header, payload = decode_frame(frame, direction=vector["direction"])
    assert header == vector["header"]
    assert payload == _b(vector["payload_b64"])
    assert encode_frame(header, payload, direction=vector["direction"]) == frame


@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda v: v["name"])
def test_every_invalid_vector_is_refused_with_its_code(vector):
    with pytest.raises(ProtocolError) as caught:
        decode_frame(_b(vector["frame_b64"]), direction=vector["direction"])
    assert caught.value.code == vector["error"]


@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda v: v["name"])
def test_the_stream_decoder_refuses_the_same_vectors(vector):
    decoder = FrameDecoder(direction=vector["direction"])
    frames, error = decoder.feed(_b(vector["frame_b64"]))
    error = error or decoder.close()
    assert frames == []
    assert error == vector["error"]


def test_vectors_cover_every_message_type_both_ways():
    from friday_voice.protocol.messages import CHILD_TO_HOST_TYPES, HOST_TO_CHILD_TYPES

    covered = {(v["direction"], v["header"]["type"]) for v in VECTORS["valid"]}
    assert {t for d, t in covered if d == HOST_TO_CHILD} == HOST_TO_CHILD_TYPES
    assert {t for d, t in covered if d == CHILD_TO_HOST} == CHILD_TO_HOST_TYPES


def test_the_header_encoding_is_canonical():
    header = {"type": "log", "detail": "ışık", "code": "a"}
    assert canonical_header(header) == '{"code":"a","detail":"ışık","type":"log"}'.encode()


def test_an_over_length_prefix_is_refused_before_its_body_is_read():
    class Stream:
        def __init__(self):
            self.reads = []
            self.data = LENGTH.pack(MAX_FRAME_BYTES + 1) + b"x" * 64

        def read(self, size):
            self.reads.append(size)
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    stream = Stream()
    with pytest.raises(ProtocolError) as caught:
        read_frame(stream)
    assert caught.value.code == "frame_too_large"
    assert stream.reads == [4]


def test_read_frame_eof_at_a_boundary_is_none_and_inside_a_frame_is_short_read():
    frame = encode_frame({"type": "shutdown"}, direction=HOST_TO_CHILD)
    assert read_frame(io.BytesIO(b"")) is None
    stream = io.BytesIO(frame)
    assert read_frame(stream, direction=HOST_TO_CHILD) == ({"type": "shutdown"}, b"")
    assert read_frame(stream) is None
    for cut in (1, 3, 4, len(frame) - 1):
        with pytest.raises(ProtocolError) as caught:
            read_frame(io.BytesIO(frame[:cut]))
        assert caught.value.code == "short_read"


def test_encode_refuses_odd_pcm_and_payload_on_control_types():
    with pytest.raises(ProtocolError) as caught:
        encode_frame(
            {"type": "audio", "seq": 0, "captured_at_ms": 0, "aec": "on"},
            b"\x00",
            direction=HOST_TO_CHILD,
        )
    assert caught.value.code == "pcm_odd_length"
    with pytest.raises(ProtocolError) as caught:
        encode_frame({"type": "shutdown"}, b"\x00\x00", direction=HOST_TO_CHILD)
    assert caught.value.code == "payload_unexpected"


def test_encode_validates_against_the_schema():
    with pytest.raises(ProtocolError) as caught:
        encode_frame({"type": "earcon", "earcon": "siren"}, direction=CHILD_TO_HOST)
    assert caught.value.code == "schema_invalid"


def test_a_refusal_never_echoes_the_offending_value():
    secret = "jpat_" + "S3cretS3cretS3cret"
    frame = raw_frame({"type": "credential", "token": secret, "extra": 1})
    with pytest.raises(ProtocolError) as caught:
        decode_frame(frame, direction=HOST_TO_CHILD)
    assert secret not in str(caught.value)


def test_the_decoder_returns_frames_before_an_error_then_stays_dead():
    good = encode_frame({"type": "earcon", "earcon": "end"}, direction=CHILD_TO_HOST)
    decoder = FrameDecoder(direction=CHILD_TO_HOST)
    frames, error = decoder.feed(good + LENGTH.pack(0))
    assert frames == [({"type": "earcon", "earcon": "end"}, b"")]
    assert error == "frame_empty"
    assert decoder.feed(good) == ([], "frame_empty")


def test_the_decoder_reassembles_byte_by_byte():
    frames_in = [
        encode_frame({"type": "earcon", "earcon": "wake"}, direction=CHILD_TO_HOST),
        encode_frame(
            {"type": "play", "utterance_id": "u", "seq": 0, "last": True},
            b"\x01\x02" * 100,
            direction=CHILD_TO_HOST,
        ),
    ]
    decoder = FrameDecoder(direction=CHILD_TO_HOST)
    got = []
    for byte in b"".join(frames_in):
        frames, error = decoder.feed(bytes([byte]))
        assert error is None
        got += frames
    assert [h["type"] for h, _ in got] == ["earcon", "play"]
    assert got[1][1] == b"\x01\x02" * 100
    assert decoder.close() is None


def test_a_header_without_newline_inside_the_cap_is_header_too_large():
    body = b"{" + b" " * (16 * 1024 + 10)
    with pytest.raises(ProtocolError) as caught:
        decode_frame(LENGTH.pack(len(body)) + body)
    assert caught.value.code == "header_too_large"


def test_unknown_error_codes_are_a_programming_error():
    with pytest.raises(ValueError):
        ProtocolError("made_up")
