"""The sidecar server's refusals (brief D11): over 1 MB, any path but
`/compress` and `/healthz`, any method but POST (GET for `/healthz`),
malformed bodies; a count-changing compressor is reverted; `/healthz`
reports the telemetry and offline env (Phase 8B closeout). Uses a fake compress function, so it runs without
Headroom (`python test_server.py` or pytest)."""

import http.client
import json
import os
import threading

import server


def _fake(messages, model):
    return {
        "messages": [{"role": "assistant", "content": m["content"][:10]} for m in messages],
        "tokens_before": 100,
        "tokens_after": 10,
        "transforms_applied": ["fake"],
        "lossless": False,
    }


def _serve(compress_fn=_fake):
    httpd = server.make_server("127.0.0.1", 0, compress_fn=compress_fn)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _request(httpd, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        conn.close()


def _body(messages):
    return json.dumps({"model": "m", "messages": messages}).encode()


GOOD = [{"role": "assistant", "content": "a long assistant reply " * 5}]


def test_compress_round_trip():
    httpd = _serve()
    try:
        status, data = _request(httpd, "POST", "/compress", _body(GOOD))
        assert status == 200
        assert data["messages"] == [{"role": "assistant", "content": GOOD[0]["content"][:10]}]
        assert (data["tokens_before"], data["tokens_after"]) == (100, 10)
    finally:
        httpd.shutdown()


def test_refuses_other_paths_and_methods():
    httpd = _serve()
    try:
        for path in ("/", "/compress/", "/v1/messages", "/compress?x=1", "/healthz/"):
            assert _request(httpd, "POST", path, _body(GOOD))[0] == 404, path
        assert _request(httpd, "GET", "/compress")[0] == 405
        assert _request(httpd, "GET", "/anything")[0] == 404
        assert _request(httpd, "GET", "/healthz?x=1")[0] == 404
        assert _request(httpd, "POST", "/healthz", _body(GOOD))[0] == 405
        assert _request(httpd, "DELETE", "/healthz")[0] == 405
    finally:
        httpd.shutdown()


def test_healthz_reports_telemetry_off_and_offline():
    """Phase 8B closeout: preflight's `compressor` check reads `/healthz`;
    it calls no compressor and reports nothing but the env verdict."""
    calls = []

    def counting(messages, model):
        calls.append(messages)
        return _fake(messages, model)

    httpd = _serve(counting)
    saved = os.environ.get("HEADROOM_TELEMETRY")
    try:
        assert _request(httpd, "GET", "/healthz") == (
            200,
            {"status": "ok", "telemetry": "off", "offline": True},
        )
        os.environ["HEADROOM_TELEMETRY"] = "on"
        assert _request(httpd, "GET", "/healthz")[1]["telemetry"] == "on"
        assert calls == []
    finally:
        os.environ["HEADROOM_TELEMETRY"] = saved if saved is not None else "off"
        httpd.shutdown()


def test_refuses_a_body_over_one_megabyte():
    calls = []
    httpd = _serve(lambda m, model: calls.append(m) or _fake(m, model))
    try:
        big = _body([{"role": "assistant", "content": "x" * (server.MAX_BODY_BYTES + 1)}])
        assert _request(httpd, "POST", "/compress", big)[0] == 413
        assert not calls
    finally:
        httpd.shutdown()


def test_refuses_malformed_bodies_and_other_roles():
    httpd = _serve()
    try:
        bad = [
            b"not json",
            json.dumps({"messages": "x"}).encode(),
            json.dumps({"messages": [], "model": "m"}).encode(),
            json.dumps({"messages": GOOD, "extra": 1}).encode(),
            _body([{"role": "user", "content": "hi"}]),
            _body([{"role": "system", "content": "hi"}]),
            _body([{"role": "assistant", "content": 3}]),
        ]
        for body in bad:
            assert _request(httpd, "POST", "/compress", body)[0] == 400, body
    finally:
        httpd.shutdown()


def test_a_count_change_is_reverted_and_errors_are_500():
    def dropping(messages, model):
        return {"messages": [], "tokens_before": 50, "tokens_after": 1}

    def failing(messages, model):
        raise RuntimeError("boom")

    httpd = _serve(dropping)
    try:
        status, data = _request(httpd, "POST", "/compress", _body(GOOD))
        assert status == 200 and data["messages"] == GOOD
        assert data["tokens_after"] == data["tokens_before"] == 50
    finally:
        httpd.shutdown()
    httpd = _serve(failing)
    try:
        assert _request(httpd, "POST", "/compress", _body(GOOD))[0] == 500
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    for _name, _test in sorted(globals().items()):
        if _name.startswith("test_") and callable(_test):
            _test()
            print(f"ok {_name}")
