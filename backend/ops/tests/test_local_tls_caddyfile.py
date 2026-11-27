"""`deploy/docker/Caddyfile.local` (compose.local-tls.yml): the loopback TLS
front forwards exactly the node's paths -- pairing, credential rotation,
the node gateway and the node voice flow (the host's voice-token mint plus
the six views a `node_voice` token reaches, Phase 11 D4, the same set
preflight `voice_live_channel` checks) -- and answers 404 to everything
else. Parsed text plus Django's URL resolver; no Docker, no database."""

import re
from pathlib import Path

import pytest
import yaml
from django.urls import resolve

from ops.preflight import NODE_VOICE_SURFACES

REPO = Path(__file__).resolve().parents[3]
CADDYFILE = REPO / "deploy" / "docker" / "Caddyfile.local"
COMPOSE = REPO / "deploy" / "docker" / "compose.local-tls.yml"

if not CADDYFILE.is_file():  # a backend-only image, not a checkout
    pytest.skip("no repository checkout around the backend", allow_module_level=True)

NODE_PATHS = {
    "/api/v1/nodes/pair",
    "/api/v1/auth/nodes/credential/rotate",
}
VOICE_PATHS = {
    "/api/v1/auth/nodes/voice-token",
    "/api/v1/auth/nodes/voice-policy",
    "/api/v1/voice/live/sessions",
    "/api/v1/voice/transcriptions",
    "/api/v1/voice/speech",
    "/api/v1/voice/receipt",
}
TURN_REGEXP = (
    "^/api/v1/ai/conversations/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    "/messages$"
)
CONVERSATION = "11111111-1111-4111-8111-111111111111"


def _directives():
    return [
        line.strip()
        for line in CADDYFILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _matchers():
    """{name: (kind, [args])} for every named matcher."""
    found = {}
    for line in _directives():
        match = re.fullmatch(r"@(\w+)\s+(path|path_regexp)\s+(.+)", line)
        if match:
            found[match.group(1)] = (match.group(2), match.group(3).split())
    return found


def _view_name(path):
    func = resolve(path).func
    view = getattr(func, "view_class", None) or getattr(func, "cls", None) or func
    return f"{view.__module__}.{view.__qualname__}"


def test_the_global_options_and_the_deny_by_default_stay():
    text = CADDYFILE.read_text(encoding="utf-8")
    directives = _directives()
    for option in ("admin off", "auto_https off", "persist_config off", "skip_install_trust"):
        assert option in directives
    assert re.search(r"\n\thandle \{\n\t\trespond 404\n\t\}\n\}", text), "catch-all 404 missing"
    assert not any("0.0.0.0" in line for line in directives)
    # Every handle is bound to a named matcher, except the final 404.
    handles = [line for line in directives if line.startswith("handle")]
    assert handles[-1] == "handle {"
    assert all(re.fullmatch(r"handle @\w+ \{", line) for line in handles[:-1])
    assert {line.split()[1][1:] for line in handles[:-1]} == set(_matchers())


def test_the_proxy_publishes_on_loopback_only():
    service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["local-tls"]
    assert service["ports"] == ["127.0.0.1:8443:8443"]


def test_exactly_the_node_and_node_voice_paths_are_forwarded():
    matchers = _matchers()
    paths = {path for kind, args in matchers.values() if kind == "path" for path in args}
    regexps = [arg for kind, args in matchers.values() if kind == "path_regexp" for arg in args]
    assert paths == NODE_PATHS | VOICE_PATHS | {"/ws/node/v1"}
    assert regexps == [TURN_REGEXP]
    assert not any("*" in path for path in paths)  # exact paths, no prefixes


def test_the_voice_paths_are_the_d4_views_plus_the_mint():
    """The six `node_voice` views preflight checks, plus the host's mint."""
    reached = {_view_name(path) for path in VOICE_PATHS}
    reached.add(_view_name(f"/api/v1/ai/conversations/{CONVERSATION}/messages"))
    assert reached == NODE_VOICE_SURFACES | {"accounts.views.NodeVoiceTokenMintView"}
    assert len(NODE_VOICE_SURFACES) == 6


def test_the_chat_turn_regexp_matches_only_the_messages_route():
    pattern = re.compile(TURN_REGEXP)
    assert pattern.match(f"/api/v1/ai/conversations/{CONVERSATION}/messages")
    for path in (
        f"/api/v1/ai/conversations/{CONVERSATION}",
        f"/api/v1/ai/conversations/{CONVERSATION}/messages/x",
        "/api/v1/ai/conversations/not-a-uuid/messages",
        "/api/v1/ai/conversations/",
    ):
        assert not pattern.match(path)


# --- every forwarded path is a real route (Caddy normalises before matching) ---


def test_every_forwarded_path_is_a_real_route():
    """Caddy's `path` matcher is case-insensitive and matches the cleaned
    path (`//` merged, `%2f` and dot-segments resolved), so each forwarded
    name must be a route that exists exactly: Django (case-sensitive) or
    the node gateway's ASGI mount. A variant Caddy lets through that is
    not this exact path is a 404 in Django."""
    from config.asgi_nodes import GATEWAY_WS_PATH

    matchers = _matchers()
    paths = {path for kind, args in matchers.values() if kind == "path" for path in args}
    assert GATEWAY_WS_PATH in paths
    for path in paths - {GATEWAY_WS_PATH}:
        _view_name(path)  # Resolver404 for a path Django does not route
    _view_name(f"/api/v1/ai/conversations/{CONVERSATION}/messages")


# --- CORS: the proxy adds nothing, Django's policy is unchanged ---

PROXY_ORIGINS = ("https://127.0.0.1:8443", "https://localhost:8443", "https://local-tls:8443")
CORS_SETTINGS = {"CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS", "CORS_ALLOW_ALL_ORIGINS"}


def test_the_proxy_never_sets_cors_or_rewrites_headers():
    directives = _directives()
    assert not any(line.startswith(("header", "header_up", "header_down")) for line in directives)
    assert not any("access-control" in line.lower() for line in directives)
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for name in ("web", "local-tls"):
        env = (services.get(name) or {}).get("environment") or {}
        keys = set(env) if isinstance(env, dict) else {item.split("=")[0] for item in env}
        assert not keys & CORS_SETTINGS


@pytest.mark.parametrize("origin", PROXY_ORIGINS)
def test_django_grants_no_cors_to_the_proxy_origin(settings, origin):
    from corsheaders.middleware import CorsMiddleware
    from django.http import HttpResponse
    from django.test import RequestFactory

    assert origin not in settings.CORS_ALLOWED_ORIGINS
    assert not getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False)
    assert origin not in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
    middleware = CorsMiddleware(lambda _request: HttpResponse("view"))
    factory = RequestFactory()
    preflight = factory.options(
        "/api/v1/voice/speech", HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST"
    )
    plain = factory.post("/api/v1/voice/live/sessions", HTTP_ORIGIN=origin)
    for request in (preflight, plain):
        response = middleware(request)
        assert "Access-Control-Allow-Origin" not in response.headers
        assert "Access-Control-Allow-Credentials" not in response.headers
