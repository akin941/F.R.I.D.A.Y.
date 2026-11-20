"""A `hook` PAT reaches exactly one endpoint (Phase 8A brief D6, release
gate "a hook token accepted outside the ingest endpoint or able to read
anything").

Walks every routed DRF view with a valid hook token: every route whose view
authenticates at all answers 401/403, and the token is never accepted
(`last_used_at` stays empty) anywhere but `POST /memory/ingest/hook-events`.
"""

import re
import secrets
import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework.views import APIView

from accounts.authentication import TOKEN_PREFIX, HookTokenAuthentication, hash_token
from accounts.models import PersonalAccessToken
from accounts.services import register_user
from memory.ingestion.views import HookEventIngestView
from workspaces.models import Workspace

pytestmark = pytest.mark.django_db

_CONVERTER = re.compile(r"<(?:(?P<type>\w+):)?(?P<name>\w+)>")


def _sample(match):
    kind = match.group("type") or "str"
    if kind == "uuid":
        return str(uuid.uuid4())
    if kind == "int":
        return "1"
    return "x"


def _routes(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for pattern in resolver.url_patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern, route)
        elif isinstance(pattern, URLPattern):
            view_class = getattr(pattern.callback, "view_class", None)
            if view_class is None or not issubclass(view_class, APIView):
                continue
            if route.startswith("^"):
                continue  # regex routes (none of ours are DRF views)
            initkwargs = getattr(pattern.callback, "view_initkwargs", {}) or {}
            auth = list(initkwargs.get("authentication_classes", view_class.authentication_classes))
            yield "/" + _CONVERTER.sub(_sample, route), view_class, auth


@pytest.fixture
def hook_token():
    owner = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    owner.is_owner = True
    owner.save()
    workspace = Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = PersonalAccessToken.objects.create(
        workspace=workspace,
        user=owner,
        name="hook",
        kind=PersonalAccessToken.Kind.HOOK,
        token_hash=hash_token(raw),
    )
    return raw, token


def test_the_ingest_view_is_the_only_one_using_the_hook_authenticator():
    users = {view for _, view, auth in _routes() if HookTokenAuthentication in auth}
    assert users == {HookEventIngestView}
    ingest = [auth for _, view, auth in _routes() if view is HookEventIngestView]
    assert ingest == [[HookTokenAuthentication]]


def test_a_hook_token_is_refused_on_every_other_route(hook_token):
    raw, token = hook_token
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    checked = 0
    for url, view_class, auth in _routes():
        if view_class is HookEventIngestView:
            continue
        for method in ("get", "post", "patch", "delete"):
            response = getattr(client, method)(url, {}, format="json")
            if auth:
                assert response.status_code in (401, 403), (method, url, response.status_code)
                checked += 1
    assert checked > 100  # the walk really covered the API
    token.refresh_from_db()
    assert token.last_used_at is None


def test_the_hook_token_is_accepted_on_the_ingest_endpoint(hook_token):
    raw, token = hook_token
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    response = client.post("/api/v1/memory/ingest/hook-events", {"events": []}, format="json")
    assert response.status_code == 202
    token.refresh_from_db()
    assert token.last_used_at is not None
