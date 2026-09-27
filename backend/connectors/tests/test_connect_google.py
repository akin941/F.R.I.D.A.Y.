"""`manage.py connect_google` (brief D9, R4): the installed-app PKCE flow on a
real `127.0.0.1` loopback server, against a mocked token endpoint. The
"browser" is a thread that follows the consent URL's redirect the way Google
would; the token endpoint checks the PKCE S256 verifier."""

import io
import threading
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.core.management import CommandError, call_command

from connectors.bridges import google
from connectors.management.commands import connect_google
from connectors.tests import google_fake
from connectors.tests.google_fake import FakeGoogle


@pytest.fixture
def fake(monkeypatch):
    state = FakeGoogle()
    monkeypatch.setenv(google.ENV_CLIENT_ID, google_fake.CLIENT_ID)
    monkeypatch.setenv(google.ENV_CLIENT_SECRET, google_fake.CLIENT_SECRET_CANARY)
    monkeypatch.delenv(google.ENV_REFRESH_TOKEN, raising=False)
    monkeypatch.setattr(google, "_transport", state.transport())
    return state


@pytest.fixture
def browser(monkeypatch, fake):
    """Stands in for the owner's browser: records the consent URL and hits
    the loopback redirect with `code` and `state` (or a tampered state)."""
    seen = {}

    def install(*, state_override=None, error=None):
        def open_url(url):
            params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
            seen.update(params)
            fake.code_challenge = params["code_challenge"]
            fake.redirect_uri = params["redirect_uri"]
            query = {"state": state_override or params["state"]}
            if error:
                query["error"] = error
            else:
                query["code"] = fake.expected_code

            def hit():
                urllib.request.urlopen(f"{params['redirect_uri']}/?{urlencode(query)}", timeout=5)

            threading.Thread(target=hit, daemon=True).start()
            return True

        monkeypatch.setattr(connect_google, "open_browser", open_url)
        return seen

    return install


def _run(*args):
    out = io.StringIO()
    call_command("connect_google", *args, "--timeout", "10", stdout=out)
    return out.getvalue()


def test_the_pkce_loopback_flow_prints_the_refresh_token_once(fake, browser, caplog):
    seen = browser()
    output = _run("--send")
    assert seen["code_challenge_method"] == "S256"
    assert seen["access_type"] == "offline" and seen["prompt"] == "consent"
    assert set(seen["scope"].split()) == set(google_fake.ALL_SCOPES)
    redirect = urlsplit(seen["redirect_uri"])
    assert redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.port
    # The token endpoint verified the S256 challenge against the verifier.
    assert output.count(google_fake.NEW_REFRESH_TOKEN) == 1
    assert f"{google.ENV_REFRESH_TOKEN}={google_fake.NEW_REFRESH_TOKEN}" in output
    assert google_fake.NEW_REFRESH_TOKEN not in caplog.text
    assert google_fake.CLIENT_SECRET_CANARY not in output + caplog.text


def test_read_scopes_by_default(fake, browser):
    fake.consent_scopes = google.READ_SCOPES
    seen = browser()
    _run()
    assert set(seen["scope"].split()) == set(google.READ_SCOPES)


def test_a_declined_scope_is_reported(fake, browser):
    fake.consent_scopes = (*google.READ_SCOPES, google.SCOPE_CALENDAR_EVENTS)
    browser()
    output = _run("--send")
    assert "Declined" in output and google.SCOPE_GMAIL_SEND in output
    assert google_fake.NEW_REFRESH_TOKEN in output


def test_a_grant_wider_than_the_manifest_is_discarded(fake, browser):
    fake.consent_scopes = (*google_fake.ALL_SCOPES, "https://mail.google.com/")
    browser()
    out = io.StringIO()
    with pytest.raises(CommandError, match="beyond the connector manifest"):
        call_command("connect_google", "--send", "--timeout", "10", stdout=out)
    assert google_fake.NEW_REFRESH_TOKEN not in out.getvalue()


def test_a_tampered_state_is_refused_before_any_exchange(fake, browser):
    browser(state_override="forged")
    with pytest.raises(CommandError, match="state"):
        _run()
    assert fake.requests == []


def test_a_consent_error_changes_nothing(fake, browser):
    browser(error="access_denied")
    with pytest.raises(CommandError, match="access_denied"):
        _run()
    assert fake.requests == []


def test_missing_client_credentials(monkeypatch, fake):
    monkeypatch.delenv(google.ENV_CLIENT_SECRET)
    with pytest.raises(CommandError, match=google.ENV_CLIENT_SECRET):
        _run()


def test_pkce_pair_is_rfc7636_s256():
    import base64
    import hashlib

    verifier, challenge = connect_google.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()
    assert connect_google.pkce_pair()[0] != verifier
