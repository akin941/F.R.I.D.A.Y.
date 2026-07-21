"""SSRF hardening tests for `knowledge.url_fetch` (MVP 3 §3). These are
security tests, review-blocking if missing (brief's test strategy) — every
mitigation is exercised independently: initial-resolution blocking, per-hop
redirect re-validation, content-type allowlist, size cap, and scheme
allowlist.

No real network I/O anywhere in this file: DNS resolution is faked via
`socket.getaddrinfo`, and the HTTP layer is faked via `urllib3.*ConnectionPool`
— `_resolve_and_validate`, `_fetch_one_hop` and `fetch_url` all run for real
against these two fake boundaries, so the actual validation/redirect logic is
genuinely exercised, not bypassed.
"""

import socket
from unittest import mock

import pytest
import urllib3

from knowledge.url_fetch import UrlFetchError, _resolve_and_validate, fetch_url

pytestmark = pytest.mark.django_db


def _fake_getaddrinfo(mapping):
    def fn(hostname, *_args, **_kwargs):
        if hostname not in mapping:
            raise socket.gaierror(f"no mapping for {hostname!r}")
        return [(socket.AF_INET, None, None, None, (ip, 0)) for ip in mapping[hostname]]

    return fn


def _fake_response(status, headers, body):
    response = mock.Mock()
    response.status = status
    response.headers = headers
    chunk_size = 8192
    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)] or [b""]
    response.stream = mock.Mock(return_value=iter(chunks))
    response.release_conn = mock.Mock()
    return response


def _mock_pools(monkeypatch, responses):
    """`responses`: list of (status, headers, body) tuples, consumed in
    fetch order (one per hop) regardless of scheme/host."""
    state = {"calls": 0}

    def factory(_host, port=None, **_kwargs):
        index = state["calls"]
        state["calls"] += 1
        status, headers, body = responses[index]
        pool = mock.Mock()
        pool.request = mock.Mock(return_value=_fake_response(status, headers, body))
        pool.close = mock.Mock()
        return pool

    monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPConnectionPool", factory)
    monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPSConnectionPool", factory)
    return state


class TestResolveAndValidate:
    def test_public_address_is_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        assert _resolve_and_validate("example.com") == "8.8.8.8"

    def test_loopback_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["127.0.0.1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_link_local_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["169.254.1.1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_cloud_metadata_address_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["169.254.169.254"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_rfc1918_private_address_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["10.0.0.5"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_multicast_address_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["224.0.0.1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_ipv6_loopback_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["::1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_shared_address_space_cgnat_is_blocked(self, monkeypatch):
        """Round-1 Codex review finding: RFC 6598 shared address space
        (100.64.0.0/10, used by CGNAT) is neither `is_private` nor
        `is_reserved` in Python's ipaddress module — an OR-list of named
        ranges missed it entirely. `is_global` is the correct predicate."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["100.64.0.1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_nat64_embedded_cloud_metadata_address_is_blocked(self, monkeypatch):
        """Round-3 Codex review finding: `64:ff9b::/96` (RFC 6052 NAT64
        Well-Known Prefix) addresses report `is_global=True` from Python's
        `ipaddress` module regardless of what IPv4 address is embedded in
        the low 32 bits — unlike `::ffff:x.x.x.x` (IPv4-mapped), which
        Python DOES special-case. `64:ff9b::a9fe:a9fe` embeds
        169.254.169.254 (cloud metadata) and must still be blocked."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["64:ff9b::a9fe:a9fe"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_nat64_embedded_loopback_address_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["64:ff9b::7f00:1"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_nat64_embedded_private_address_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["64:ff9b::a00:1"]}),  # embeds 10.0.0.1
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_nat64_embedded_public_address_is_allowed(self, monkeypatch):
        """The NAT64 embedded-address check must not overreject: a
        NAT64-synthesized address embedding a genuinely public IPv4
        destination is legitimate traffic, not an SSRF vector."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["64:ff9b::808:808"]}),  # embeds 8.8.8.8
        )
        assert _resolve_and_validate("example.com") == "64:ff9b::808:808"

    def test_any_blocked_address_among_multiple_records_rejects_the_host(self, monkeypatch):
        """A hostile DNS name could list a public address first and a
        private one second, hoping only the first gets checked — every
        resolved address must be validated, not just the first."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["8.8.8.8", "10.0.0.5"]}),
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            _resolve_and_validate("evil.example")

    def test_dns_resolution_failure_raises(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({}),
        )
        with pytest.raises(UrlFetchError, match="Could not resolve"):
            _resolve_and_validate("nowhere.example")


class TestFetchOneHopRealPoolConstruction:
    """Round-1 Codex review finding: `assert_same_host` is a `request()`
    kwarg on real urllib3 pools, not a pool-CONSTRUCTOR kwarg — passing it
    to the constructor silently lands in `conn_kw` and crashes the first
    real connection attempt with `TypeError: HTTPConnection.__init__() got
    an unexpected keyword argument 'assert_same_host'`. Every other test in
    this file mocks the pool CLASS itself (which accepts arbitrary kwargs),
    so none of them could have caught this — these two construct a REAL
    `urllib3.*ConnectionPool` (only `.request()` is patched, so no real
    socket is ever touched) to prove the constructor itself doesn't raise.
    """

    def test_real_http_pool_construction_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        with mock.patch.object(
            urllib3.HTTPConnectionPool,
            "request",
            return_value=_fake_response(200, {"Content-Type": "text/plain"}, b"ok"),
        ):
            assert fetch_url("http://example.com/doc.txt") == "ok"

    def test_real_https_pool_construction_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        with mock.patch.object(
            urllib3.HTTPSConnectionPool,
            "request",
            return_value=_fake_response(200, {"Content-Type": "text/plain"}, b"ok"),
        ):
            assert fetch_url("https://example.com/doc.txt") == "ok"


class TestFetchUrlHostHeader:
    def _capture_host_header(self, monkeypatch):
        captured = {}

        def factory(_host, port=None, **_kwargs):
            pool = mock.Mock()

            def fake_request(_method, _path, headers=None, **_kwargs):
                captured["headers"] = headers
                return _fake_response(200, {"Content-Type": "text/plain"}, b"ok")

            pool.request = mock.Mock(side_effect=fake_request)
            pool.close = mock.Mock()
            return pool

        monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPConnectionPool", factory)
        return captured

    def test_default_port_is_omitted_from_host_header(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        captured = self._capture_host_header(monkeypatch)
        fetch_url("http://example.com/doc.txt")
        assert captured["headers"]["Host"] == "example.com"

    def test_explicit_non_default_port_is_preserved_in_host_header(self, monkeypatch):
        """Round-1 Codex review finding: a non-default port must appear in
        the `Host` header (HTTP/1.1 requirement for virtual hosting) —
        omitting it can route to the wrong vhost or fail entirely."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        captured = self._capture_host_header(monkeypatch)
        fetch_url("http://example.com:8443/doc.txt")
        assert captured["headers"]["Host"] == "example.com:8443"


class TestFetchUrlPathParameters:
    def test_semicolon_path_parameters_are_preserved(self, monkeypatch):
        """Round-5 Codex review finding: `urlparse` splits a `;`-separated
        path-parameter segment (e.g. `;jsessionid=...`) out of `.path` into
        `.params` — reconstructing the request path from `.path` alone
        silently drops it and fetches a different resource than the one
        submitted."""
        captured = {}

        def factory(_host, port=None, **_kwargs):
            pool = mock.Mock()

            def fake_request(_method, path, headers=None, **_kwargs):
                captured["path"] = path
                return _fake_response(200, {"Content-Type": "text/plain"}, b"ok")

            pool.request = mock.Mock(side_effect=fake_request)
            pool.close = mock.Mock()
            return pool

        monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPConnectionPool", factory)
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        fetch_url("http://example.com/article;jsessionid=abc123?x=1")
        assert captured["path"] == "/article;jsessionid=abc123?x=1"


class TestFetchUrlScheme:
    def test_disallowed_scheme_is_rejected_before_any_dns_lookup(self, monkeypatch):
        # No DNS mapping registered at all — if this reached resolution, it
        # would raise "Could not resolve", not the scheme error, proving the
        # scheme check runs first.
        monkeypatch.setattr("knowledge.url_fetch.socket.getaddrinfo", _fake_getaddrinfo({}))
        with pytest.raises(UrlFetchError, match="Disallowed URL scheme"):
            fetch_url("ftp://evil.example/file")

    def test_missing_hostname_is_rejected(self, monkeypatch):
        monkeypatch.setattr("knowledge.url_fetch.socket.getaddrinfo", _fake_getaddrinfo({}))
        with pytest.raises(UrlFetchError, match="no hostname"):
            fetch_url("http:///path")

    def test_out_of_range_port_is_a_terminal_fetch_error(self, monkeypatch):
        """Round-4 Codex review finding: `parsed.port` RAISES `ValueError`
        for an out-of-range port literal (e.g. `:99999`) instead of
        returning a value to check — DRF's `URLField` accepts such a URL, so
        this must be caught here and turned into a terminal `UrlFetchError`,
        not left to escape as an unexpected exception (which the ingestion
        task would otherwise treat as a transient, retryable failure)."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        with pytest.raises(UrlFetchError, match="Invalid port"):
            fetch_url("http://example.com:99999/path")


class TestFetchUrlContentAndSize:
    def test_happy_path_text_plain(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        _mock_pools(
            monkeypatch,
            [(200, {"Content-Type": "text/plain"}, b"hello world")],
        )
        assert fetch_url("http://example.com/doc.txt") == "hello world"

    def test_html_is_stripped_to_text(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        html_body = (
            b"<html><head><style>.x{color:red}</style></head>"
            b"<body><script>alert(1)</script><p>Hello &amp; welcome</p></body></html>"
        )
        _mock_pools(monkeypatch, [(200, {"Content-Type": "text/html; charset=utf-8"}, html_body)])
        text = fetch_url("http://example.com/page")
        assert "alert(1)" not in text
        assert "color:red" not in text
        assert "Hello & welcome" in text
        assert "<" not in text and ">" not in text

    def test_disallowed_content_type_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        _mock_pools(monkeypatch, [(200, {"Content-Type": "application/json"}, b"{}")])
        with pytest.raises(UrlFetchError, match="Disallowed Content-Type"):
            fetch_url("http://example.com/data.json")

    def test_disallowed_content_type_is_rejected_before_streaming(self, monkeypatch):
        """Round-7 Codex review finding: the Content-Type allowlist must be
        enforced BEFORE any body streaming — proven here via a `stream()`
        that raises if ever invoked, not just via the end-state error, so a
        hostile endpoint can't force a download/drip up to the byte cap
        before the (already-sufficient) headers get checked."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )

        def factory(_host, port=None, **_kwargs):
            response = mock.Mock()
            response.status = 200
            response.headers = {"Content-Type": "application/octet-stream"}
            response.stream = mock.Mock(
                side_effect=AssertionError("must not stream a disallowed content-type")
            )
            response.release_conn = mock.Mock()
            pool = mock.Mock()
            pool.request = mock.Mock(return_value=response)
            pool.close = mock.Mock()
            return pool

        monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPConnectionPool", factory)
        with pytest.raises(UrlFetchError, match="Disallowed Content-Type"):
            fetch_url("http://example.com/data.bin")

    def test_slow_drip_body_exceeds_total_read_deadline(self, monkeypatch):
        """Round-7 Codex review finding: a server that drip-feeds bytes just
        under `AI_TIMEOUT_READ` between each individual read never trips
        urllib3's per-read timeout (each read succeeds on its own), so the
        total transfer needs its own wall-clock deadline independent of any
        single read's timeout."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        # Small body (well under any byte cap) so the ONLY reason to reject
        # it is the total-time deadline, not the size cap.
        _mock_pools(monkeypatch, [(200, {"Content-Type": "text/plain"}, b"a" * 100)])
        # First call establishes the deadline; the second (after the one
        # chunk this body streams as) reports elapsed time past it.
        monkeypatch.setattr("knowledge.url_fetch.time.monotonic", mock.Mock(side_effect=[0, 1000]))
        with pytest.raises(UrlFetchError, match="took too long"):
            fetch_url("http://example.com/slow")

    def test_oversized_body_is_rejected_mid_stream(self, monkeypatch, settings):
        settings.KNOWLEDGE_MAX_UPLOAD_BYTES = 10
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        _mock_pools(monkeypatch, [(200, {"Content-Type": "text/plain"}, b"x" * 1000)])
        with pytest.raises(UrlFetchError, match="exceeds the"):
            fetch_url("http://example.com/huge.txt")

    def test_non_utf8_body_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        _mock_pools(monkeypatch, [(200, {"Content-Type": "text/plain"}, b"\xff\xfe\x00bad")])
        with pytest.raises(UrlFetchError, match="UTF-8"):
            fetch_url("http://example.com/bad.txt")

    def test_unexpected_status_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )
        _mock_pools(monkeypatch, [(500, {"Content-Type": "text/plain"}, b"error")])
        with pytest.raises(UrlFetchError, match="Unexpected response status"):
            fetch_url("http://example.com/broken")

    def test_connection_error_is_wrapped(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"example.com": ["8.8.8.8"]}),
        )

        def factory(*_args, **_kwargs):
            pool = mock.Mock()
            pool.request = mock.Mock(
                side_effect=urllib3.exceptions.MaxRetryError(None, "/", reason="boom")
            )
            pool.close = mock.Mock()
            return pool

        monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPConnectionPool", factory)
        monkeypatch.setattr("knowledge.url_fetch.urllib3.HTTPSConnectionPool", factory)
        with pytest.raises(UrlFetchError, match="Fetch failed"):
            fetch_url("http://example.com/")


class TestFetchUrlRedirects:
    def test_redirect_is_followed_and_re_resolved(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"a.example": ["8.8.8.8"], "b.example": ["8.8.4.4"]}),
        )
        _mock_pools(
            monkeypatch,
            [
                (302, {"Location": "http://b.example/final"}, b""),
                (200, {"Content-Type": "text/plain"}, b"final content"),
            ],
        )
        assert fetch_url("http://a.example/start") == "final content"

    def test_redirect_to_a_private_ip_host_is_rejected(self, monkeypatch):
        """The core TOCTOU-closing property (brief §3): each hop is
        independently re-resolved and re-validated — a redirect target
        resolving to a private IP is rejected exactly like an initial one,
        even though the first hop's host was genuinely public."""
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"a.example": ["8.8.8.8"], "internal.example": ["10.0.0.9"]}),
        )
        _mock_pools(
            monkeypatch,
            [(302, {"Location": "http://internal.example/secret"}, b"")],
        )
        with pytest.raises(UrlFetchError, match="disallowed address"):
            fetch_url("http://a.example/start")

    def test_redirect_missing_location_header_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"a.example": ["8.8.8.8"]}),
        )
        _mock_pools(monkeypatch, [(302, {}, b"")])
        with pytest.raises(UrlFetchError, match="Location header"):
            fetch_url("http://a.example/start")

    def test_redirect_with_oversized_body_is_still_followed(self, monkeypatch, settings):
        """Review finding: `_fetch_one_hop` used to stream and size-cap
        EVERY response body before `fetch_url` got a chance to see the 3xx
        status, so a real-world redirect page whose body happens to exceed
        `KNOWLEDGE_MAX_UPLOAD_BYTES` was rejected as oversized instead of
        being followed — the redirect body is never ingested, so it must
        never be read (let alone size-capped) at all."""
        # The redirect body (1000 bytes) is far larger than the cap; the
        # final hop's real content ("final content", 13 bytes) fits under it
        # — proving the cap was never applied to the redirect body at all,
        # not just raised high enough to let both through.
        settings.KNOWLEDGE_MAX_UPLOAD_BYTES = 20
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"a.example": ["8.8.8.8"], "b.example": ["8.8.4.4"]}),
        )
        _mock_pools(
            monkeypatch,
            [
                (302, {"Location": "http://b.example/final"}, b"x" * 1000),
                (200, {"Content-Type": "text/plain"}, b"final content"),
            ],
        )
        assert fetch_url("http://a.example/start") == "final content"

    def test_too_many_redirects_is_rejected(self, monkeypatch, settings):
        settings.KNOWLEDGE_URL_MAX_REDIRECTS = 2
        monkeypatch.setattr(
            "knowledge.url_fetch.socket.getaddrinfo",
            _fake_getaddrinfo({"a.example": ["8.8.8.8"]}),
        )
        _mock_pools(
            monkeypatch,
            [
                (302, {"Location": "http://a.example/1"}, b""),
                (302, {"Location": "http://a.example/2"}, b""),
                (302, {"Location": "http://a.example/3"}, b""),
            ],
        )
        with pytest.raises(UrlFetchError, match="Too many redirects"):
            fetch_url("http://a.example/start")
