"""The workers (brief D8): the discrete-event SimWorker on a manual clock
and the ThreadWorker of the stdio child; plus `friday-voice run`'s loop
with a pipeline over real pipes (a thread worker, FakeFriday)."""

import io
import os
import queue
import threading
import time

from friday_voice.child import run_stdio
from friday_voice.conformance.definitions import hello
from friday_voice.fakes import FakeFriday
from friday_voice.pipeline import VoicePipeline
from friday_voice.ports import ManualClock, RealClock
from friday_voice.protocol.framing import encode_frame, read_frame
from friday_voice.protocol.messages import CHILD_TO_HOST, HOST_TO_CHILD
from friday_voice.replay import DEV_CREDENTIAL
from friday_voice.workers import Closer, Delay, SimWorker, ThreadWorker


def job(name, steps, log):
    for delay in steps:
        yield Delay(delay)
        yield {"name": name, "delay": delay}
    log.append(f"{name}:end")


def test_sim_worker_interleaves_jobs_in_manual_time():
    clock = ManualClock()
    worker = SimWorker(clock)
    seen = []
    worker.deliver = lambda job_id, event: seen.append((clock.monotonic_ms(), event["name"]))
    log = []
    worker.submit(job("a", [100, 100], log))
    worker.submit(job("b", [50, 300], log))
    worker.run_until_idle()
    assert seen == [(50, "b"), (100, "a"), (200, "a"), (350, "b")]
    assert log == ["a:end", "b:end"]


def test_sim_worker_cancel_closes_the_job_and_its_closers():
    clock = ManualClock()
    worker = SimWorker(clock)
    closed = []

    def streaming():
        yield Closer(lambda: closed.append("stream"))
        yield Delay(1_000)
        yield {"never": True}

    worker.deliver = lambda job_id, event: closed.append("delivered")
    job_id = worker.submit(streaming())
    worker.advance(10)
    worker.cancel(job_id)
    worker.run_until_idle()
    assert closed == ["stream"] and not worker.busy()


def test_sim_worker_turns_an_exception_into_an_error_event():
    clock = ManualClock()
    worker = SimWorker(clock)
    seen = []
    worker.deliver = lambda job_id, event: seen.append(event)

    def failing():
        yield Delay(5)
        raise RuntimeError("boom")

    worker.submit(failing())
    worker.run_until_idle()
    assert seen[0]["kind"] == "error" and isinstance(seen[0]["error"], RuntimeError)


def test_thread_worker_posts_events_and_cancel_runs_closers():
    inbox = queue.Queue()
    worker = ThreadWorker(lambda job_id, event: inbox.put((job_id, event)))
    gate = threading.Event()
    closed = []

    def blocking():
        yield Closer(lambda: (closed.append(1), gate.set()))
        yield {"kind": "open"}
        gate.wait(5)
        yield {"kind": "late"}

    job_id = worker.submit(blocking())
    assert inbox.get(timeout=5) == (job_id, {"kind": "open"})
    worker.cancel(job_id)
    deadline = time.monotonic() + 5
    while worker.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [1]
    assert inbox.empty()  # the late event of a cancelled job is dropped


def test_thread_worker_reports_failures():
    inbox = queue.Queue()
    worker = ThreadWorker(lambda job_id, event: inbox.put(event))

    def failing():
        raise ValueError("x")
        yield  # pragma: no cover

    worker.submit(failing())
    assert inbox.get(timeout=5)["kind"] == "error"


# --- the stdio loop with a pipeline (real pipes, a thread worker) ---


def _pipes():
    a_r, a_w = os.pipe()
    b_r, b_w = os.pipe()
    return (
        os.fdopen(a_r, "rb", buffering=0),
        os.fdopen(a_w, "wb", buffering=0),
        os.fdopen(b_r, "rb", buffering=0),
        os.fdopen(b_w, "wb", buffering=0),
    )


def test_run_stdio_completes_a_push_to_talk_turn_on_a_thread_worker():
    child_in, host_out, host_in, child_out = _pipes()
    fake = FakeFriday(latencies={key: 0 for key in ("session", "stt", "chat_first", "tts")})
    clock = RealClock()

    def make_pipeline(post):
        return VoicePipeline(clock, fake, ThreadWorker(post), log_stream=io.StringIO())

    result = {}
    thread = threading.Thread(
        target=lambda: result.update(
            code=run_stdio(
                child_in, child_out, clock, make_pipeline=make_pipeline, tick_seconds=0.01
            )
        )
    )
    thread.start()

    def send(header, payload=b""):
        host_out.write(encode_frame(header, payload, direction=HOST_TO_CHILD))

    send(hello())
    send({"type": "credential", **DEV_CREDENTIAL})
    send({"type": "ptt", "key": "down"})
    for seq in range(25):
        send(
            {"type": "audio", "seq": seq, "captured_at_ms": clock.now_ms(), "aec": "on"},
            b"\x10\x00" * 320,
        )
    send({"type": "ptt", "key": "up"})
    plays = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        header, _payload = read_frame(host_in, direction=CHILD_TO_HOST)
        if header["type"] == "play":
            plays.append(header)
            if header["last"]:
                send(
                    {
                        "type": "playback",
                        "utterance_id": header["utterance_id"],
                        "state": "ended",
                        "at_ms": clock.now_ms(),
                    }
                )
        if header["type"] == "state" and header["state"] == "armed_ptt" and plays:
            break
    host_out.close()
    thread.join(timeout=5)
    assert result == {"code": 0}
    assert {p["utterance_id"] for p in plays} == {"t1s0", "t1s1"}
    assert [c["endpoint"] for c in fake.requests] == ["session", "stt", "chat", "tts", "tts"]
