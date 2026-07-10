"""SSRF-hardened URL fetcher for `url`-source KnowledgeDocuments (branch 3,
MVP 3 §3). Runs only from the ingestion worker (background job) — the
request/response path must never fetch a user-supplied URL directly, since a
slow or hostile URL must not tie up a web worker.

Every mitigation below is a hard requirement (brief §3), not defense in
depth: fetching a user-supplied URL server-side is a classic SSRF vector.
"Resolve the hostname, check the range, then let the HTTP client connect to
the hostname" is NOT sufficient — a second DNS lookup at connect time can
legally return a DIFFERENT, unvalidated address (DNS rebinding). Every
request here resolves once, validates every resolved address, and connects
directly to one validated IP while still sending the correct `Host` header
and TLS SNI — the HTTP client is never given the hostname to resolve itself.

Built directly on `urllib3` (not `requests`): a raw `urllib3` connection pool
never consults `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` env vars unless a
`ProxyManager` is explicitly constructed, so simply not doing that satisfies
the "disable environment-configured proxies" requirement — there is no
`trust_env` flag to turn off because nothing here reads the environment in
the first place.
"""

import html
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import urllib3
from django.conf import settings

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_TYPES = {"text/plain", "text/markdown", "text/html"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_STREAM_CHUNK_BYTES = 8192

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")


class UrlFetchError(Exception):
    """Any fetch failure. Always terminal for the document (the ingestion
    task marks it `failed`, never retries) — a rejected scheme, a blocked
    IP, an oversized body or a disallowed content-type will not become valid
    by retrying. The message is always secret-safe: no raw exception text,
    no internal detail beyond what the caller already supplied."""


def fetch_url(url):
    """Fetch `url`, following up to `KNOWLEDGE_URL_MAX_REDIRECTS` redirects
    — each hop independently re-resolved and re-validated from scratch, so a
    redirect to a private IP is rejected exactly like an initial one — and
    return decoded, chunking-ready text. Raises `UrlFetchError` on any
    failure."""
    max_redirects = settings.KNOWLEDGE_URL_MAX_REDIRECTS
    current_url = url
    for _ in range(max_redirects + 1):
        response_status, headers, body = _fetch_one_hop(current_url)
        if response_status in _REDIRECT_STATUSES:
            location = headers.get("Location")
            if not location:
                raise UrlFetchError(
                    f"Redirect response ({response_status}) had no Location header."
                )
            current_url = urljoin(current_url, location)
            continue
        if response_status != 200:
            raise UrlFetchError(f"Unexpected response status: {response_status}.")
        # Content-Type allowlist enforcement itself already happened inside
        # `_fetch_one_hop`, before it streamed a single byte of body — this
        # re-parse is only to decide html-stripping below, not a second gate.
        content_type = _parse_content_type(headers.get("Content-Type", ""))
        text = _decode_body(body)
        if content_type == "text/html":
            text = _strip_html(text)
        return text
    raise UrlFetchError(f"Too many redirects (> {max_redirects}).")


def _fetch_one_hop(url):
    """Fetch exactly one hop with no automatic redirect following — the
    caller (`fetch_url`) re-resolves and re-validates every hop itself, so
    the HTTP client must never be allowed to chase a `Location` header on
    its own. Returns `(status, headers, body_bytes)`."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UrlFetchError(f"Disallowed URL scheme: {parsed.scheme!r}.")
    hostname = parsed.hostname
    if not hostname:
        raise UrlFetchError("URL has no hostname.")

    pin_ip = _resolve_and_validate(hostname)
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        # `parsed.port` raises (rather than returning a value to check) for
        # an out-of-range port literal like `:99999` — DRF's `URLField`
        # accepts this (review finding), so it isn't caught until here. Must
        # be a terminal `UrlFetchError`, not left to escape as an unexpected
        # exception: the caller's generic `except Exception` would otherwise
        # treat a permanently malformed URL as a transient failure and keep
        # retrying it.
        raise UrlFetchError(f"Invalid port in URL: {url!r}.") from exc
    # HTTP/1.1 requires an explicit non-default port in the Host header —
    # virtual-hosted servers route on it, so omitting it can fetch the
    # wrong (or no) content for a URL like `https://example.com:8443/doc`.
    host_header = hostname if port == default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if parsed.params:
        # `urlparse` splits a `;`-separated path-parameter segment (e.g.
        # `;jsessionid=...`) into `.params`, out of `.path` — reconstructing
        # from `.path` alone silently drops it and fetches a different
        # resource than the one submitted (review finding). Matches
        # `urllib.parse.urlunparse`'s own reconstruction.
        path = f"{path};{parsed.params}"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    timeout = urllib3.Timeout(connect=settings.AI_TIMEOUT_CONNECT, read=settings.AI_TIMEOUT_READ)
    pool_kwargs = {"timeout": timeout, "retries": False}
    if parsed.scheme == "https":
        # Connect to the validated IP (not the hostname — that would hand
        # DNS resolution back to the HTTP client) while still sending the
        # original hostname as SNI and verifying the certificate against it,
        # exactly as urllib3 documents for a pinned-IP connection.
        pool = urllib3.HTTPSConnectionPool(
            pin_ip,
            port=port,
            server_hostname=hostname,
            assert_hostname=hostname,
            cert_reqs="CERT_REQUIRED",
            **pool_kwargs,
        )
    else:
        pool = urllib3.HTTPConnectionPool(pin_ip, port=port, **pool_kwargs)

    max_bytes = settings.KNOWLEDGE_MAX_UPLOAD_BYTES
    try:
        response = pool.request(
            "GET",
            path,
            headers={"Host": host_header},
            redirect=False,
            assert_same_host=False,
            preload_content=False,
        )
        try:
            if response.status != 200:
                # A redirect's (or any other non-200's) body is never
                # ingested — `fetch_url` only needs the status/headers to
                # decide the next hop or raise. Streaming and size-capping it
                # here would reject a legitimate redirect whose body happens
                # to exceed `KNOWLEDGE_MAX_UPLOAD_BYTES` for content that was
                # never going to be used anyway (review finding), and it's
                # strictly better for a hostile redirect too: the body is
                # never read at all, not merely capped.
                return response.status, response.headers, b""
            # Content-Type is checked BEFORE any streaming — a hostile
            # endpoint returning 200 with a disallowed type (e.g.
            # `application/octet-stream`) must not be able to force a
            # download/drip up to the byte cap first; the headers alone are
            # enough to reject it (review finding).
            content_type = _parse_content_type(response.headers.get("Content-Type", ""))
            if content_type not in _ALLOWED_CONTENT_TYPES:
                raise UrlFetchError(f"Disallowed Content-Type: {content_type!r}.")
            body = bytearray()
            # A wall-clock deadline on the WHOLE read, not just urllib3's
            # per-read timeout: a server that drip-feeds bytes just under
            # `AI_TIMEOUT_READ` between each chunk never trips the read
            # timeout (each individual read succeeds), so without this the
            # task's time budget — which assumes one read timeout per hop,
            # not an unbounded slow-drip transfer — would not hold (review
            # finding).
            deadline = time.monotonic() + settings.AI_TIMEOUT_READ
            for piece in response.stream(_STREAM_CHUNK_BYTES):
                body.extend(piece)
                if len(body) > max_bytes:
                    raise UrlFetchError(f"Response exceeds the {max_bytes}-byte limit.")
                if time.monotonic() > deadline:
                    raise UrlFetchError("Response body took too long to read.")
            return response.status, response.headers, bytes(body)
        finally:
            response.release_conn()
    except urllib3.exceptions.HTTPError as exc:
        raise UrlFetchError("Fetch failed: connection or protocol error.") from exc
    finally:
        pool.close()


# Root cause behind two separate review-round bypasses of this SSRF guard
# (round 1: CGNAT 100.64.0.0/10, round 3: NAT64 64:ff9b::/96) — read this
# before touching `_is_blocked` again. `ip.is_global` is NOT a complete SSRF
# predicate by itself: it correctly classifies a *plain* address (an address
# that IS the destination), but several address-TRANSLATION encodings wrap
# a destination address inside a DIFFERENT address that Python classifies
# independently of what's wrapped inside it —
#   - `::ffff:x.x.x.x` (IPv4-mapped) IS special-cased by Python:
#     `is_global`/`is_private` already reflect the embedded IPv4.
#   - `64:ff9b::/96` (NAT64 Well-Known Prefix, RFC 6052) is NOT special-cased:
#     the *outer* range is globally assigned, so `is_global=True` regardless
#     of the embedded IPv4 — even when it embeds 169.254.169.254 (cloud
#     metadata) or a loopback/private address.
#   - CGNAT (100.64.0.0/10, RFC 6598) isn't a translation encoding at all,
#     but the same class of lesson: it's neither `is_private` nor
#     `is_reserved`, so an enumeration of named ranges missed it while
#     `is_global` alone catches it correctly.
# The fix pattern each time: for every encoding capable of wrapping a
# blocked destination, extract the wrapped address and validate IT too —
# never trust one classification of the outer address alone. If a new
# encoding surfaces (a future review round, a new RFC), it needs the same
# extract-and-recheck treatment here, not just an is_global read.
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(ip):
    """The IPv4 address embedded in an RFC 6052 NAT64 Well-Known Prefix
    address (`64:ff9b::/96`), or `None` if `ip` isn't one. Unlike
    `::ffff:x.x.x.x` (IPv4-mapped, where Python's `is_global`/`is_private`
    already reflect the embedded address), a NAT64 address's `is_global`
    reflects only the outer `64:ff9b::/96` range — which IS globally
    assigned — never the embedded IPv4 destination. So a hostname
    resolving to e.g. `64:ff9b::a9fe:a9fe` (embedding 169.254.169.254,
    cloud metadata) sails through `_is_blocked` unblocked in any
    NAT64-enabled environment (review finding) unless the embedded address
    is separately extracted and validated, same as this one is below."""
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_blocked(ip):
    # `not is_global` is the correct predicate here, not an OR-list of named
    # ranges (loopback/link-local/private/...): that enumeration missed
    # RFC 6598 shared address space (100.64.0.0/10, used by CGNAT) — Python
    # reports `is_private=False` AND `is_reserved=False` for e.g.
    # 100.64.0.1, so the named-list version let a non-globally-routable
    # address straight through (review finding). `is_global` subsumes every
    # named range that enumeration used to check, including the
    # 169.254.0.0/16 link-local block that 169.254.169.254 (cloud metadata)
    # falls in — EXCEPT multicast, which Python's ipaddress module reports
    # as `is_global=True`, so it's checked explicitly alongside it.
    if (not ip.is_global) or ip.is_multicast:
        return True
    embedded = _embedded_ipv4(ip)
    return embedded is not None and ((not embedded.is_global) or embedded.is_multicast)


def _resolve_and_validate(hostname):
    """Resolve `hostname` and reject it if ANY resolved address is in a
    blocked range, then return ONE validated address to connect to. Every
    address is checked, not just the first — some resolvers return multiple
    records, and a hostile DNS name could list a public address first and a
    private one second, expecting only the first to be checked."""
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise UrlFetchError(f"Could not resolve host: {hostname!r}.") from exc
    seen = []
    for info in addr_infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    if not seen:
        raise UrlFetchError(f"Could not resolve host: {hostname!r}.")
    for addr in seen:
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError as exc:
            # Fail closed: an address this module can't classify is treated
            # as blocked, not allowed through.
            raise UrlFetchError(
                f"Could not classify resolved address for host: {hostname!r}."
            ) from exc
        if _is_blocked(ip):
            raise UrlFetchError(f"Host resolves to a disallowed address: {hostname!r}.")
    return seen[0]


def _parse_content_type(header_value):
    return header_value.split(";", 1)[0].strip().lower()


def _decode_body(body):
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UrlFetchError("Response body must be UTF-8 encoded text.") from exc


def _strip_html(text):
    """Minimal, dependency-light HTML-to-text: drop script/style blocks
    entirely (their content is not visible text), strip every remaining
    tag, then unescape entities. Deliberately conservative — not a general
    HTML renderer (brief: "stripped to text ... not rendered or executed")."""
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text)
