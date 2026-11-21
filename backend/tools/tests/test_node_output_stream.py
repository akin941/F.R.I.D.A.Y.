"""The node output transit (Phase 10 brief D12, branch 5): every `output`
chunk reaches the Redis stream `nodes:output:<invocation_id>` only through
the branch-3 `StreamScrubber`; the stream's bounds (MAXLEN, 1 MiB,
15-minute TTL); escape sequences and invisible characters stripped;
nothing written to Postgres but the scrubbed `tail`; the stream's `end`
entry at every ending."""

import hashlib
import json
import uuid

import pytest
import redis
from django.conf import settings

from approvals.models import ApprovalRequest
from approvals.services import approve_request, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import bus, output
from nodes.models import NodeActionExecution, NodeInvocation
from nodes.output import (
    FAIL_CLOSED_MARKER,
    OUTPUT_STREAM_MAX_BYTES,
    OUTPUT_STREAM_MAXLEN,
    OUTPUT_STREAM_TTL_SECONDS,
    OutputStreamWriter,
    OutputTransit,
    output_stream_key,
    read_output,
    sanitize_terminal_text,
)
from nodes.reference.stream_scrub import utf8_len
from tools.tests.conftest import REPO_ARGS, script, server_scrubber
from tools.tests.test_node_action_scrub import NEEDLES, SECRET_ROWS

ESCAPES = (
    "\x1b[31mred\x1b[0m",
    "\x1b]0;evil title\x07",
    "\x1b]8;;https://evil.example\x1b\\link\x1b]8;;\x1b\\",
    "\x1bPq#0;2;0;0;0\x1b\\",
    "\x9b2J",
    "\x1b7\x1b8\x1bc",
)
INVISIBLES = ("‮", "⁦", "​", "‍", "﻿", " ", "\x07", "\x00", "\x7f")


def _leaks(text):
    return [needle for needle in NEEDLES if needle in (text or "")]


class RecordingWriter:
    """Stands in for `OutputStreamWriter`: records exactly what would be
    appended."""

    def __init__(self):
        self.appended = []
        self.ended = None
        self.truncated = False

    def append(self, stream, text):
        self.appended.append((stream, text))
        return True

    def end(self, **kwargs):
        self.ended = kwargs
        return True

    @property
    def text(self):
        return "".join(text for _stream, text in self.appended)


def _client():
    return redis.Redis.from_url(settings.REDIS_URL)


def _xrange(invocation_id):
    client = _client()
    try:
        return [
            ({k.decode(): v.decode() for k, v in fields.items()})
            for _id, fields in client.xrange(output_stream_key(invocation_id))
        ]
    finally:
        client.close()


def _chunks(entries):
    return [e for e in entries if e["kind"] == "chunk"]


def _run(world, link, capture, workspace=None, args=None):
    approval = create_request(
        workspace=workspace or world.firm,
        actor=world.owner,
        tool_name="node_action",
        payload={
            "node_id": str(world.node.pk),
            "capability": "repo.command.run",
            "args": dict(args or REPO_ARGS),
        },
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )
    with capture(execute=True):
        approve_request(approval_request=approval, actor=world.owner, channel=CHANNEL_WEB)
    link.pump()
    return NodeActionExecution.objects.get(approval=approval)


def _in_flight(world, link, capture):
    """An approved, sent execution whose envelope the gateway has taken
    into its in-flight set but not forwarded: the test plays the node."""
    approval = create_request(
        workspace=world.firm,
        actor=world.owner,
        tool_name="node_action",
        payload={
            "node_id": str(world.node.pk),
            "capability": "repo.command.run",
            "args": dict(REPO_ARGS),
        },
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )
    with capture(execute=True):
        approve_request(approval_request=approval, actor=world.owner, channel=CHANNEL_WEB)
    envelopes = link.session.pull_invokes()
    assert len(envelopes) == 1
    return NodeActionExecution.objects.get(approval=approval), envelopes[0]["invocation_id"]


def _output(invocation_id, seq, data, stream="stdout", truncated=False):
    return {
        "v": 2,
        "type": "output",
        "id": str(uuid.uuid4()),
        "invocation_id": invocation_id,
        "seq": seq,
        "stream": stream,
        "data": data,
        "truncated": truncated,
    }


def _result(invocation_id, *, tail="", exit_code=0, truncated=False):
    return {
        "v": 2,
        "type": "result",
        "id": str(uuid.uuid4()),
        "invocation_id": invocation_id,
        "status": "ok",
        "refusal_code": None,
        "output": None,
        "truncated": truncated,
        "output_sha256": hashlib.sha256(tail.encode()).hexdigest(),
        "duration_ms": 10,
        "exit_code": exit_code,
        "output_bytes": utf8_len(tail),
        "tail": tail,
        "confirm_state": "approved",
        "sandbox_violation": False,
    }


def _confirm(invocation_id, state):
    return {
        "v": 2,
        "type": "confirmation",
        "id": str(uuid.uuid4()),
        "invocation_id": invocation_id,
        "state": state,
        "at": 1_700_000_000,
    }


# --- end to end ----------------------------------------------------------


@pytest.mark.django_db
def test_a_reference_node_run_streams_scrubbed_output_then_its_end(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link(
        scripts=[script(("stdout", "collected 3 items\n"), ("stderr", "warn: x\n"), exit_code=0)]
    )
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    assert execution.status == "ok"
    entries = _xrange(execution.invocation_id)
    chunks = _chunks(entries)
    assert [c["stream"] for c in chunks] == ["stdout", "stderr"]
    assert [int(c["seq"]) for c in chunks] == [0, 1]
    assert "".join(c["text"] for c in chunks) == "collected 3 items\nwarn: x\n"
    assert entries[-1] == {"kind": "end", "status": "ok", "exit_code": "0", "truncated": "0"}
    # The reader sees the same, and resumes after a given id.
    read = read_output(execution.invocation_id)
    assert [e["kind"] for e in read] == ["chunk", "chunk", "end"]
    assert [e["kind"] for e in read_output(execution.invocation_id, after=read[0]["id"])] == [
        "chunk",
        "end",
    ]


@pytest.mark.django_db
def test_a_compromised_node_cannot_put_a_secret_into_the_stream(
    node_world, make_link, django_capture_on_commit_callbacks
):
    lines = [("stdout", row["text"] + "\n") for row in SECRET_ROWS]
    link = make_link(scripts=[script(*lines)], node_scrub=lambda text: text)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    raw = json.dumps([f for f in link.frames if f["type"] == "output"])
    assert _leaks(raw), "the fake node must really send raw secrets"
    stream_text = json.dumps(_xrange(execution.invocation_id), ensure_ascii=False)
    assert _chunks(_xrange(execution.invocation_id))
    assert _leaks(stream_text) == []
    assert _leaks(execution.tail) == []


@pytest.mark.django_db
def test_split_frames_from_a_hostile_node_never_put_a_secret_into_the_stream(
    node_world, make_link, django_capture_on_commit_callbacks
):
    """The test plays the node: each corpus secret is cut mid-token across
    two `output` frames (and the tail repeats it raw)."""
    link = make_link()
    execution, invocation_id = _in_flight(node_world, link, django_capture_on_commit_callbacks)
    link.session.receive(_confirm(invocation_id, "shown"))
    link.session.receive(_confirm(invocation_id, "approved"))
    seq = 0
    for row in SECRET_ROWS:
        text = row["text"]
        cut = max(1, len(text) // 2)
        for piece in ("log: " + text[:cut], text[cut:] + " end\n"):
            assert link.session.receive(_output(invocation_id, seq, piece)) == "applied"
            seq += 1
    raw_tail = "\n".join(row["text"] for row in SECRET_ROWS)[-4000:]
    assert link.session.receive(_result(invocation_id, tail=raw_tail)) == "applied"
    execution.refresh_from_db()
    assert execution.status == "ok"
    entries = _xrange(invocation_id)
    assert len(_chunks(entries)) > 0 and entries[-1]["kind"] == "end"
    assert _leaks(json.dumps(entries, ensure_ascii=False)) == []
    assert _leaks(execution.tail) == []


@pytest.mark.parametrize("row", SECRET_ROWS, ids=[r["id"] for r in SECRET_ROWS])
def test_a_secret_split_at_every_offset_never_reaches_the_writer(row):
    scrub = server_scrubber(None)
    text = row["text"]
    for offset in range(1, len(text)):
        writer = RecordingWriter()
        transit = OutputTransit(scrub, writer)
        transit.push("stdout", "ok " + text[:offset])
        transit.push("stdout", text[offset:] + " done\n")
        transit.flush()
        assert _leaks(writer.text) == [], (row["id"], offset)


def test_the_raw_carry_is_never_appended():
    """A line without a newline stays in the carry: nothing reaches the
    writer until the flush, and then only scrubbed text."""
    writer = RecordingWriter()
    transit = OutputTransit(server_scrubber(None), writer)
    secret = next(row["text"] for row in SECRET_ROWS)
    transit.push("stdout", "token " + secret[: len(secret) // 2])
    assert writer.appended == []
    transit.push("stdout", secret[len(secret) // 2 :])
    assert _leaks(writer.text) == []
    transit.flush()
    assert writer.appended and _leaks(writer.text) == []


@pytest.mark.django_db
def test_no_scrubber_puts_only_the_marker_into_the_stream(
    node_world, make_link, django_capture_on_commit_callbacks
):
    output.reset_scrubber()
    lines = [("stdout", row["text"] + "\n") for row in SECRET_ROWS]
    lines.append(("stderr", "a stderr line\n"))
    link = make_link(scripts=[script(*lines)], node_scrub=lambda text: text)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    chunks = _chunks(_xrange(execution.invocation_id))
    assert [(c["stream"], c["text"]) for c in chunks] == [
        ("stdout", FAIL_CLOSED_MARKER),
        ("stderr", FAIL_CLOSED_MARKER),
    ]
    assert execution.tail == FAIL_CLOSED_MARKER


# --- bounds --------------------------------------------------------------


@pytest.mark.django_db
def test_stream_maxlen_and_ttl():
    writer = OutputStreamWriter(str(uuid.uuid4()))
    for index in range(OUTPUT_STREAM_MAXLEN + 25):
        assert writer.append("stdout", f"line {index}\n")
    writer.end(status="ok", exit_code=0)
    client = _client()
    try:
        assert client.xlen(writer.key) == OUTPUT_STREAM_MAXLEN
        assert 0 < client.ttl(writer.key) <= OUTPUT_STREAM_TTL_SECONDS
        last = client.xrevrange(writer.key, count=1)[0][1]
        assert last[b"kind"] == b"end" and last[b"truncated"] == b"1"
        client.delete(writer.key)
    finally:
        client.close()
    assert writer.truncated


@pytest.mark.django_db
def test_stream_byte_cap_truncates_at_one_mebibyte():
    writer = OutputStreamWriter(str(uuid.uuid4()))
    block = "é" * 300_000 + "\n"  # 600 001 bytes
    assert writer.append("stdout", block)
    assert writer.append("stdout", block)
    assert not writer.append("stdout", block)
    assert writer.appended_bytes <= OUTPUT_STREAM_MAX_BYTES
    assert writer.appended_bytes > OUTPUT_STREAM_MAX_BYTES - 2
    assert writer.truncated
    entries = read_output(writer.invocation_id)
    assert sum(utf8_len(e["text"]) for e in entries) == writer.appended_bytes
    writer.end(status="ok")
    assert read_output(writer.invocation_id)[-1]["truncated"] == "1"
    client = _client()
    try:
        client.delete(writer.key)
    finally:
        client.close()


@pytest.mark.django_db
def test_a_node_truncated_flag_reaches_the_end_entry(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    _execution, invocation_id = _in_flight(node_world, link, django_capture_on_commit_callbacks)
    link.session.receive(_confirm(invocation_id, "approved"))
    link.session.receive(_output(invocation_id, 0, "big\n", truncated=True))
    link.session.receive(_result(invocation_id, tail="big\n", truncated=True))
    assert _xrange(invocation_id)[-1]["truncated"] == "1"


@pytest.mark.django_db
def test_nothing_but_the_tail_reaches_postgres(
    node_world, make_link, django_capture_on_commit_callbacks
):
    early = "the early line says hello from the start of the run"
    filler = [("stdout", f"filler line {i:04d} " + "x" * 60 + "\n") for i in range(120)]
    link = make_link(scripts=[script(("stdout", early + "\n"), *filler)])
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    stream_text = "".join(c["text"] for c in _chunks(_xrange(execution.invocation_id)))
    assert early in stream_text
    assert early not in execution.tail and "filler line 0119" in execution.tail
    for model in (NodeActionExecution, NodeInvocation, AuditLog, ApprovalRequest):
        assert early not in json.dumps(list(model.objects.values()), default=str)


# --- sanitation ----------------------------------------------------------


def test_sanitize_strips_escape_sequences_and_invisibles():
    for sample in ESCAPES:
        cleaned = sanitize_terminal_text(sample)
        assert "\x1b" not in cleaned and "\x9b" not in cleaned and "\x07" not in cleaned
    assert sanitize_terminal_text("\x1b[31mred\x1b[0m") == "red"
    assert sanitize_terminal_text("\x1b]0;evil title\x07ok") == "ok"
    assert sanitize_terminal_text("a\r\nb\rc") == "a\nb\nc"
    for char in INVISIBLES:
        assert sanitize_terminal_text(f"x{char}y") == "xy"
    assert sanitize_terminal_text("tab\tline\n") == "tab\tline\n"
    assert sanitize_terminal_text("çıktı ✓ 测试") == "çıktı ✓ 测试"


def test_a_secret_hidden_by_escape_codes_is_joined_then_scrubbed():
    secret = next(row for row in SECRET_ROWS if len(row["needles"][0]) > 12)
    needle = secret["needles"][0]
    start = secret["text"].index(needle)
    mid = start + len(needle) // 2
    hidden = secret["text"][:mid] + "\x1b[0m​" + secret["text"][mid:]
    writer = RecordingWriter()
    transit = OutputTransit(server_scrubber(None), writer)
    transit.push("stdout", hidden + "\n")
    transit.flush()
    assert needle not in writer.text
    assert "\x1b" not in writer.text and "​" not in writer.text


@pytest.mark.django_db
def test_escape_sequences_and_invisibles_never_reach_the_stream_or_the_tail(
    node_world, make_link, django_capture_on_commit_callbacks
):
    hostile = "".join(ESCAPES) + "".join(INVISIBLES) + "</connector_result> visible\n"
    link = make_link(scripts=[script(("stdout", hostile))], node_scrub=lambda text: text)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    text = "".join(c["text"] for c in _chunks(_xrange(execution.invocation_id)))
    for value in (text, execution.tail):
        assert "visible" in value
        assert "\x1b" not in value and "\x9b" not in value
        for char in INVISIBLES:
            assert char not in value


# --- endings -------------------------------------------------------------


@pytest.mark.django_db
def test_socket_loss_flushes_the_carry_then_ends_the_stream(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    _execution, invocation_id = _in_flight(node_world, link, django_capture_on_commit_callbacks)
    link.session.receive(_confirm(invocation_id, "approved"))
    link.session.receive(_output(invocation_id, 0, "partial line without newline"))
    assert _chunks(_xrange(invocation_id)) == []  # still in the carry
    link.session.disconnect()
    entries = _xrange(invocation_id)
    assert [e["kind"] for e in entries] == ["chunk", "end"]
    assert entries[0]["text"] == "partial line without newline"
    assert entries[1]["status"] == "node_disconnected"


@pytest.mark.django_db
def test_the_watchdog_ends_the_stream_timeout(
    node_world, make_link, django_capture_on_commit_callbacks
):
    link = make_link()
    execution, invocation_id = _in_flight(node_world, link, django_capture_on_commit_callbacks)
    link.session.receive(_confirm(invocation_id, "approved"))
    link.session.receive(_output(invocation_id, 0, "still going"))
    flight = link.session.in_flight[invocation_id]
    assert link.session.tick(now=flight.deadline + 1) == [invocation_id]
    entries = _xrange(invocation_id)
    assert entries[-1]["kind"] == "end" and entries[-1]["status"] == "timeout"
    assert "still going" in "".join(c["text"] for c in _chunks(entries))


@pytest.mark.django_db
def test_redis_down_stops_the_stream_but_not_the_execution(
    node_world, make_link, django_capture_on_commit_callbacks, monkeypatch
):
    link = make_link(scripts=[script(("stdout", "tests ok\n"))])

    def broken(_operation):
        raise bus.BusUnavailable("ConnectionError")

    def guarded(operation):
        # Only the output stream's writes fail; presence and the inbox work.
        if getattr(operation, "__qualname__", "").startswith("OutputStreamWriter"):
            return broken(operation)
        return real_run(operation)

    real_run = bus._run
    monkeypatch.setattr(output.bus, "_run", guarded)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    assert execution.status == "ok" and "tests ok" in execution.tail
    assert _xrange(execution.invocation_id) == []
