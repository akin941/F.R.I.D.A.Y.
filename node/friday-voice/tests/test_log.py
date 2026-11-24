"""The child's log (brief D16): the vendored ADR-012 scrubber is byte-identical
to its generator's output; lines are one code + a short scrubbed detail."""

import io

import pytest
from conftest import load_script

from friday_voice import log
from friday_voice.protocol.messages import CHILD_TO_HOST, validate

vendor = load_script("vendor_scrubber")


def test_the_vendored_scrubber_is_identical_to_the_generator_output():
    assert vendor.SOURCE.exists()
    assert vendor.TARGET.read_text("utf-8") == vendor.render()
    assert vendor.main(["--check"]) == 0


def test_the_vendored_copy_is_the_source_text_byte_for_byte():
    assert vendor.TARGET.read_bytes().endswith(vendor.SOURCE.read_bytes())


def test_check_fails_on_a_stale_copy(tmp_path, monkeypatch):
    stale = tmp_path / "_scrubber_vendored.py"
    stale.write_text(vendor.render() + "# edited\n", encoding="utf-8")
    monkeypatch.setattr(vendor, "TARGET", stale)
    assert vendor.main(["--check"]) == 1


SECRET = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 3


def test_a_secret_in_a_detail_is_scrubbed():
    line = log.format_line("http.error", f"Authorization: Bearer {SECRET}")
    assert SECRET not in line
    assert line.startswith("friday-voice http.error ")


def test_a_detail_is_one_short_line():
    detail = log.clean_detail("first\nsecond\r\x00" + "x" * 500)
    assert "\n" not in detail and "\r" not in detail and "\x00" not in detail
    assert len(detail) <= log.MAX_DETAIL_CHARS


@pytest.mark.parametrize("code", ["", "Upper", "has space", "x" * 49, "1starts.digit", None])
def test_codes_are_a_closed_shape(code):
    with pytest.raises(ValueError):
        log.format_line(code)


def test_emit_writes_one_line_and_survives_a_closed_stream():
    stream = io.StringIO()
    log.emit("child.start", stream=stream)
    assert stream.getvalue() == "friday-voice child.start\n"
    closed = io.StringIO()
    closed.close()
    log.emit("child.start", stream=closed)  # never raises


def test_log_frames_validate_and_carry_no_secret():
    header = log.log_message("http.error", f"token={SECRET}")
    validate(header, CHILD_TO_HOST)
    assert SECRET not in header["detail"]
