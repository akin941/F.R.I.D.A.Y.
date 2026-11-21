"""The server-side output scrub (Phase 10 brief D7, D12): the `tail`
re-scrub, the fail-closed marker, `output` frames scrubbed-counted-dropped,
and the streaming scrub with carry-over against split secrets."""

import base64
import json
import logging
from pathlib import Path

import pytest
import redis
from django.conf import settings

from approvals.models import ApprovalRequest
from approvals.services import approve_request, create_request
from audit.models import AuditLog
from core.channels import CHANNEL_WEB
from nodes import output
from nodes.models import NodeActionExecution, NodeInvocation
from nodes.output import FAIL_CLOSED_MARKER, StreamScrubber, scrub_tail
from nodes.reference.stream_scrub import SCRUB_CARRY_BYTES, utf8_len
from tools.tests.conftest import REPO_ARGS, script, server_scrubber

BACKEND = Path(__file__).resolve().parents[2]
CORPUS = BACKEND / "memory" / "tests" / "scrubber_corpus" / "secrets.jsonl"
VECTORS = BACKEND / "nodes" / "conformance" / "vectors" / "scrub_stream.json"


def _secret_rows():
    rows = []
    for line in CORPUS.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["class"] != "secret":
            continue
        row["text"] = row["text"].replace("<<>>", "")
        row["needles"] = [n.replace("<<>>", "") for n in row["needles"]]
        rows.append(row)
    return rows


SECRET_ROWS = _secret_rows()
NEEDLES = [needle for row in SECRET_ROWS for needle in row["needles"]]


def _leaks(text):
    return [needle for needle in NEEDLES if needle in (text or "")]


def _run(world, link, capture):
    approval = create_request(
        workspace=world.firm,
        actor=world.owner,
        tool_name="node_action",
        payload={
            "node_id": str(world.node.pk),
            "capability": "repo.command.run",
            "args": REPO_ARGS,
        },
        origin=ApprovalRequest.Origin.HUMAN,
        channel=CHANNEL_WEB,
    )
    with capture(execute=True):
        approve_request(approval_request=approval, actor=world.owner, channel=CHANNEL_WEB)
    link.pump()
    return NodeActionExecution.objects.get(approval=approval)


def _secret_script():
    lines = [("stdout", row["text"] + "\n") for row in SECRET_ROWS]
    return script(*lines, exit_code=0)


@pytest.mark.django_db
def test_a_compromised_node_cannot_store_a_secret(
    node_world, make_link, django_capture_on_commit_callbacks, caplog
):
    """The node's own scrub is disabled (a compromised or buggy node): the
    raw output frames and the node's tail carry every corpus secret; the
    server stores a tail with none of them, and no raw secret reaches any
    row, log or Redis key."""
    caplog.set_level(logging.DEBUG)
    link = make_link(scripts=[_secret_script()], node_scrub=lambda text: text)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    node_frames = json.dumps([f for f in link.frames if f["type"] in ("output", "result")])
    assert _leaks(node_frames), "the fake node must really send raw secrets"
    assert execution.status == "ok"
    assert execution.tail and _leaks(execution.tail) == []
    sent = sum(utf8_len(f["data"]) for f in link.frames if f["type"] == "output")
    assert execution.output_bytes == sent > 0
    rows = [
        json.dumps(list(NodeActionExecution.objects.values()), default=str),
        json.dumps(list(NodeInvocation.objects.values()), default=str),
        json.dumps(list(AuditLog.objects.values()), default=str),
        json.dumps(list(ApprovalRequest.objects.values()), default=str),
    ]
    for dump in rows:
        assert _leaks(dump) == []
    assert _leaks(caplog.text) == []
    client = redis.Redis.from_url(settings.REDIS_URL)
    try:
        assert not list(client.scan_iter(match=f"nodes:output:{execution.invocation_id}*"))
        for key in client.scan_iter(match=f"nodes:*{node_world.node.pk}*"):
            if client.type(key) == b"list":
                assert _leaks(b"".join(client.lrange(key, 0, -1)).decode()) == []
    finally:
        client.close()


@pytest.mark.django_db
def test_with_no_scrubber_registered_the_tail_is_the_marker_only(
    node_world, make_link, django_capture_on_commit_callbacks
):
    output.reset_scrubber()
    link = make_link(scripts=[_secret_script()], node_scrub=lambda text: text)
    execution = _run(node_world, link, django_capture_on_commit_callbacks)
    assert execution.tail == FAIL_CLOSED_MARKER
    assert execution.output_bytes > 0


def test_scrub_tail_fails_closed():
    output.reset_scrubber()
    assert scrub_tail("token ghp_" + "a" * 36) == FAIL_CLOSED_MARKER
    assert scrub_tail("") == ""

    def broken(text):
        raise RuntimeError("boom")

    assert scrub_tail("anything", scrub=broken) == FAIL_CLOSED_MARKER
    assert scrub_tail("x" * 9000, scrub=lambda t: t).encode() == b"x" * 4096


def test_a_stream_without_a_scrubber_emits_only_the_marker():
    scrubber = StreamScrubber(None)
    assert scrubber.fail_closed
    assert scrubber.push("ghp_" + "a" * 36) == FAIL_CLOSED_MARKER
    assert scrubber.push("") == "" and scrubber.flush() == ""


@pytest.mark.django_db
@pytest.mark.parametrize("row", SECRET_ROWS, ids=[row["id"] for row in SECRET_ROWS])
def test_a_secret_split_at_every_offset_never_leaks(row):
    full = "prefix line\n" + row["text"] + " trailing\n"
    scrub = server_scrubber(None)
    for offset in range(len(full) + 1):
        scrubber = StreamScrubber(scrub)
        emitted = scrubber.push(full[:offset])
        assert scrubber.carry_size <= scrubber.bound_bytes
        emitted += scrubber.push(full[offset:])
        assert scrubber.carry_size <= scrubber.bound_bytes
        emitted += scrubber.flush()
        assert not any(needle in emitted for needle in row["needles"]), (row["id"], offset)


@pytest.mark.django_db
@pytest.mark.parametrize("row", SECRET_ROWS[:8], ids=[row["id"] for row in SECRET_ROWS[:8]])
def test_a_secret_cut_by_a_line_break_at_the_chunk_boundary_never_leaks(row):
    """The first chunk ends exactly at a line break and the next starts
    with the secret, cut again inside it."""
    scrub = server_scrubber(None)
    text = row["text"]
    for offset in range(1, len(text)):
        scrubber = StreamScrubber(scrub)
        emitted = scrubber.push("build log\n")
        emitted += scrubber.push(text[:offset])
        emitted += scrubber.push(text[offset:] + "\n")
        emitted += scrubber.flush()
        assert not any(needle in emitted for needle in row["needles"]), (row["id"], offset)


def test_a_multibyte_character_is_never_split_on_emit():
    scrubber = StreamScrubber(lambda text: text)
    text = "çğıöşü✓" * 400 + "\n" + "ü" * 700
    emitted = scrubber.push(text[:1000]) + scrubber.push(text[1000:]) + scrubber.flush()
    assert emitted == text
    emitted.encode("utf-8")  # whole code points only


def test_the_carry_never_exceeds_its_bound():
    scrubber = StreamScrubber(lambda text: text)
    for _ in range(50):
        scrubber.push("a" * 8000)  # never a newline
        assert scrubber.carry_size <= SCRUB_CARRY_BYTES + 8192
    assert scrubber.max_carry_seen <= scrubber.bound_bytes


def test_the_server_scrubber_reproduces_the_shared_vectors():
    """D12: the server `StreamScrubber` reproduces
    `conformance/vectors/scrub_stream.json` (the 8A scrubber, no terms)."""
    document = json.loads(VECTORS.read_text("utf-8"))
    scrub = server_scrubber(None)
    for case in document["text"]:
        scrubber = StreamScrubber(scrub, carry_bytes=document["carry_bytes"])
        chunks = [base64.b64decode(c).decode("utf-8") for c in case["chunks_b64"]]
        assert [scrubber.push(c) for c in chunks] == case["emitted"], case["name"]
        assert scrubber.flush() == case["flush"], case["name"]


@pytest.mark.django_db
@pytest.mark.parametrize("ending", ["result", "disconnect", "watchdog"])
def test_the_carry_is_scrubbed_and_flushed_at_every_ending(
    node_world, make_link, django_capture_on_commit_callbacks, ending
):
    """A partial line (held in the carry) is flushed through the scrubber
    at the final result, on socket loss and at the watchdog timeout."""
    flushed = []
    real_flush = StreamScrubber.flush

    def recording_flush(self):
        text = real_flush(self)
        flushed.append(text)
        return text

    decisions = (("approve", 0),) if ending == "result" else ()
    secret = SECRET_ROWS[0]["text"]
    link = make_link(
        decisions=decisions,
        scripts=[script(("stdout", "partial " + secret))],
        node_scrub=lambda text: text,
    )
    StreamScrubber.flush = recording_flush
    try:
        execution = _run(node_world, link, django_capture_on_commit_callbacks)
        if ending == "result":
            assert execution.status == "ok"
        else:
            # Feed a frame for the still-open invocation by hand: the
            # window is open, so play the output a running command sends.
            flight = next(iter(link.session.in_flight.values()))
            link.session.receive(
                {
                    "v": 2,
                    "type": "output",
                    "id": "00000000-0000-4000-8000-000000000001",
                    "invocation_id": flight.invocation_id,
                    "seq": 0,
                    "stream": "stdout",
                    "data": "partial " + secret,
                    "truncated": False,
                }
            )
            if ending == "disconnect":
                link.session.disconnect()
            else:
                link.session.tick(now=flight.deadline + 1)
            execution.refresh_from_db()
            assert execution.status == (
                "node_disconnected" if ending == "disconnect" else "timeout"
            )
    finally:
        StreamScrubber.flush = real_flush
    assert any(flushed), "the carry was flushed"
    assert all(not _leaks(text) for text in flushed)
