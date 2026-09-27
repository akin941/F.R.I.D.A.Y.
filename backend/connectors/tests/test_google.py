"""Google mail/calendar bridge (Phase 5 branch 8, brief D9): granted-scope
verification, bounded personal reads (web only), the `mail_send` and
`calendar_create_event` gates (raw addresses, full content, MIME built only
from the payload), `invalid_grant` -> reconnect, and no credential anywhere.
`httpx.MockTransport` only -- no real Google."""

import base64
import email
import hashlib
import json
from email.policy import default as default_policy

import pytest

from approvals.models import ApprovalRequest
from audit.models import AuditLog
from connectors.bridges import google
from connectors.manifest import get_manifest
from connectors.models import ConnectorBinding, ConnectorWriteExecution
from connectors.services import check_binding_pins, set_binding_enabled
from connectors.tests import google_fake
from connectors.tests.google_fake import FakeGoogle
from core.channels import CHANNEL_CLI, CHANNEL_WEB
from tools.registry import ReadToolError, get_read_tool, get_tool, list_read_tools

pytestmark = pytest.mark.django_db

URL = "/api/v1/approvals/requests"


@pytest.fixture
def fake(monkeypatch):
    state = FakeGoogle()
    monkeypatch.setenv(google.ENV_CLIENT_ID, google_fake.CLIENT_ID)
    monkeypatch.setenv(google.ENV_CLIENT_SECRET, google_fake.CLIENT_SECRET_CANARY)
    monkeypatch.setenv(google.ENV_REFRESH_TOKEN, google_fake.REFRESH_TOKEN_CANARY)
    monkeypatch.setattr(google, "_transport", state.transport())
    google.clear_token_cache()
    yield state
    google.clear_token_cache()


@pytest.fixture
def manifest():
    return get_manifest("google")


@pytest.fixture
def connect(fake, manifest, workspace, enable):
    def _connect(scopes=google_fake.ALL_SCOPES):
        fake.scopes = tuple(scopes)
        return enable(manifest, workspace)

    return _connect


def _request(web_client, owner, workspace, tool, arguments):
    return web_client(owner).post(
        URL,
        {"workspace": str(workspace.id), "tool_name": tool, "payload": {"arguments": arguments}},
        format="json",
    )


@pytest.fixture
def approve(web_client, owner, committed):
    def _approve(request_id):
        with committed():
            return web_client(owner).post(f"{URL}/{request_id}/approve")

    return _approve


def _read(workspace, owner, tool, args, channel=CHANNEL_WEB):
    return get_read_tool(tool).execute(workspace=workspace, actor=owner, args=args, channel=channel)


MAIL = {
    "to": ["ahmet@example.com", "ayse.k+is@example.org"],
    "cc": ["muhasebe@example.com"],
    "subject": "Re: Yarınki toplantı — bütçe",
    "body": "Merhaba Ahmet,\r\n\r\n10:00 uygun.\r\nSelamlar",
    "thread_id": "t1",
    "in_reply_to": "<abc123@mail.example.com>",
}


# --- manifest ---


def test_the_manifest_is_personal_native_web_only_with_two_user_invocable_gates(manifest):
    assert manifest.kind == "native"
    assert manifest.data_class == "personal"
    assert manifest.workspace_types == frozenset({"personal"})
    assert manifest.read_channels == frozenset({"web"})
    assert {p.name for p in manifest.reads} == {"mail_search", "mail_read", "calendar_agenda"}
    for gate in ("mail_send", "calendar_create_event"):
        spec = get_tool(gate)
        assert spec.gated and spec.user_invocable
    for name in ("mail_search", "mail_read", "calendar_agenda"):
        assert get_read_tool(name).data_class == "personal"
    # Credentials are named, never valued, in the manifest.
    assert manifest.credential_envs == google.CREDENTIAL_ENVS


def test_a_firm_workspace_cannot_bind_google(fake, manifest, firm, owner):
    from connectors.services import ConnectorInvalidTransition

    with pytest.raises(ConnectorInvalidTransition):
        set_binding_enabled(
            workspace=firm, actor=owner, connector="google", enabled=True, channel=CHANNEL_WEB
        )


# --- granted-scope verification ---


def test_read_scopes_only_leave_both_gates_unavailable(connect, fake):
    binding = connect(google.READ_SCOPES)
    tools = binding.last_health["tools"]
    assert tools == {
        "mail_search": "ok",
        "mail_read": "ok",
        "calendar_agenda": "ok",
        "mail_send": "scope_missing",
        "calendar_create_event": "scope_missing",
    }
    assert binding.last_health["details"]["granted_scopes"] == sorted(google.READ_SCOPES)


def test_send_without_gmail_send_is_refused_at_request_time(
    connect, fake, web_client, owner, workspace
):
    connect(google.READ_SCOPES)
    response = _request(web_client, owner, workspace, "mail_send", MAIL)
    assert response.status_code == 400
    assert not ApprovalRequest.objects.exists()
    assert fake.sent == []


def test_a_re_consent_with_send_scopes_enables_the_gates_without_a_manifest_change(
    connect, fake, manifest
):
    binding = connect(google.READ_SCOPES)
    fake.scopes = google_fake.ALL_SCOPES
    google.clear_token_cache()
    binding = check_binding_pins(binding.id)
    assert binding.last_health["tools"]["mail_send"] == "ok"
    assert binding.last_health["tools"]["calendar_create_event"] == "ok"


def test_an_extra_scope_disables_the_whole_connector(connect, fake, workspace, owner):
    binding = connect((*google_fake.ALL_SCOPES, "https://mail.google.com/"))
    assert set(binding.last_health["tools"].values()) == {"scope_extra"}
    audit = AuditLog.objects.get(action="connector.scope_mismatch")
    assert audit.metadata["extra_scopes"] == ["https://mail.google.com/"]
    names = {spec.name for spec in list_read_tools(workspace=workspace, channel=CHANNEL_WEB)}
    assert not names & {"mail_search", "mail_read", "calendar_agenda"}
    with pytest.raises(ReadToolError):
        _read(workspace, owner, "mail_search", {})


def test_a_grant_narrowed_between_checks_is_refused_inside_the_call(
    connect, fake, workspace, owner
):
    connect(google_fake.ALL_SCOPES)
    fake.scopes = (*google_fake.ALL_SCOPES, "https://mail.google.com/")
    google.clear_token_cache()
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, "mail_search", {})
    assert exc.value.code == "scope_mismatch"
    assert fake.api_requests() == []  # refused before any API request


def test_invalid_grant_means_reconnect_with_connect_google(connect, fake, caplog):
    binding = connect(google.READ_SCOPES)
    fake.token_error = "invalid_grant"
    google.clear_token_cache()
    binding = check_binding_pins(binding.id)
    assert binding.last_health["error"] == "reconnect_required"
    assert set(binding.last_health["tools"].values()) == {"reconnect_required"}
    assert AuditLog.objects.filter(action="connector.reconnect_required").count() == 1
    assert "connect_google" in caplog.text
    # The beat running again does not re-audit.
    check_binding_pins(binding.id)
    assert AuditLog.objects.filter(action="connector.reconnect_required").count() == 1


def test_missing_credentials_are_not_configured(connect, fake, monkeypatch):
    binding = connect(google.READ_SCOPES)
    monkeypatch.delenv(google.ENV_REFRESH_TOKEN)
    binding = check_binding_pins(binding.id)
    assert binding.last_health["error"] == "not_configured"
    assert "ok" not in binding.last_health["tools"].values()


def test_the_access_token_is_reused_in_process_memory(connect, fake, workspace, owner):
    connect(google.READ_SCOPES)
    token_calls = len(fake.requests) - len(fake.api_requests())
    _read(workspace, owner, "calendar_agenda", {})
    _read(workspace, owner, "calendar_agenda", {})
    assert len(fake.requests) - len(fake.api_requests()) == token_calls


# --- reads ---


def test_mail_search_lists_bounded_metadata(connect, fake, workspace, owner, caplog):
    connect(google.READ_SCOPES)
    result = _read(workspace, owner, "mail_search", {"query": "from:ahmet", "max_results": 2})
    data = json.loads(result.text)
    assert data["count"] == 2
    first = data["messages"][0]
    assert first["from"] == "Ahmet Yılmaz <ahmet@example.com>"
    assert first["subject"] == "Yarınki toplantı"
    assert first["snippet"] == "Merhaba, yarınki toplantı & ajanda"
    listing = fake.api_requests()[0]
    assert "q=from%3Aahmet" in listing["url"] and "maxResults=2" in listing["url"]
    # The owner's query and message ids never reach a log line (httpx is
    # WARNING-only in LOGGING).
    assert "ahmet" not in caplog.text and "/messages/m1" not in caplog.text
    with pytest.raises(ReadToolError):
        _read(workspace, owner, "mail_search", {"max_results": 21})
    with pytest.raises(ReadToolError):
        _read(workspace, owner, "mail_search", {"query": "x", "unknown": 1})


def test_mail_read_defaults_to_the_latest_message_and_is_bounded(
    connect, fake, workspace, owner, settings
):
    settings.GOOGLE_MAIL_BODY_MAX_CHARS = 20
    connect(google.READ_SCOPES)
    data = json.loads(_read(workspace, owner, "mail_read", {}).text)["message"]
    assert data["id"] == "m1"
    assert data["body"] == fake.body_text[:20]
    assert data["body_truncated"] is True
    assert data["message_id_header"] == "<abc123@mail.example.com>"
    assert "maxResults=1" in fake.api_requests()[0]["url"]


def test_mail_read_falls_back_to_html_text(connect, fake, workspace, owner):
    fake.body_text = ""
    fake.body_html = "<p>Selam <b>Akın</b></p><script>alert(1)</script>"
    connect(google.READ_SCOPES)
    body = json.loads(_read(workspace, owner, "mail_read", {"message_id": "m2"}).text)["message"][
        "body"
    ]
    assert "Akın" in body and "<b>" not in body and "alert" not in body
    with pytest.raises(ReadToolError):
        _read(workspace, owner, "mail_read", {"message_id": "../../drafts"})


def test_a_blank_query_means_the_inbox(connect, fake, workspace, owner):
    connect(google.READ_SCOPES)
    _read(workspace, owner, "mail_search", {"query": ""})
    assert "q=in%3Ainbox" in fake.api_requests()[0]["url"]


def test_a_malformed_google_response_is_a_closed_protocol_error(
    connect, fake, workspace, owner, monkeypatch, caplog
):
    connect(google.READ_SCOPES)

    def broken(_payload):
        raise ValueError("Incorrect padding SECRET-BODY-TEXT")

    monkeypatch.setattr(google, "message_text", broken)
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, "mail_read", {})
    assert exc.value.code == "protocol_error"
    assert "SECRET-BODY-TEXT" not in caplog.text


def test_calendar_agenda_is_bounded_to_31_days(connect, fake, workspace, owner):
    connect(google.READ_SCOPES)
    data = json.loads(
        _read(workspace, owner, "calendar_agenda", {"start_date": "2026-09-28", "days": 2}).text
    )
    assert [e["summary"] for e in data["events"]] == ["Ekip toplantısı"]  # cancelled dropped
    assert data["from"] == "2026-09-28T00:00:00+03:00"
    assert data["to"] == "2026-09-30T00:00:00+03:00"
    with pytest.raises(ReadToolError):
        _read(workspace, owner, "calendar_agenda", {"days": 32})


def test_reads_are_web_only(connect, fake, workspace, owner):
    connect(google.READ_SCOPES)
    names = {spec.name for spec in list_read_tools(workspace=workspace, channel=CHANNEL_CLI)}
    assert not names & {"mail_search", "mail_read", "calendar_agenda"}
    with pytest.raises(ReadToolError) as exc:
        _read(workspace, owner, "mail_search", {}, channel=CHANNEL_CLI)
    assert exc.value.code == "channel_forbidden"
    assert fake.api_requests() == []


# --- mail_send ---


def _rows(summary):
    return {row["label"]: row["value"] for row in summary["rows"]}


def test_mail_send_shows_raw_addresses_and_sends_the_mime_built_from_the_payload(
    connect, fake, web_client, owner, workspace, approve
):
    connect()
    response = _request(web_client, owner, workspace, "mail_send", MAIL)
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    approval = ApprovalRequest.objects.get(id=request_id)
    arguments = approval.payload["arguments"]
    # Newlines normalized; MIME text ends with one, so the payload does too.
    assert arguments["body"] == "Merhaba Ahmet,\n\n10:00 uygun.\nSelamlar\n"

    summary = web_client(owner).get(f"{URL}/{request_id}").json()["display_summary"]
    rows = _rows(summary)
    assert rows["Kime (To)"] == "ahmet@example.com, ayse.k+is@example.org"
    assert rows["Bilgi (Cc)"] == "muhasebe@example.com"
    assert rows["Konu"] == MAIL["subject"]
    assert rows["Metin"] == arguments["body"]  # full content
    body_sha = hashlib.sha256(arguments["body"].encode()).hexdigest()
    assert rows["Metin sha256"] == body_sha
    assert fake.sent == []  # nothing before the decision

    assert approve(request_id).status_code == 200
    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(sent["raw"])
    # Built only from the snapshotted payload: byte-identical to a rebuild.
    assert raw == google.build_mime(arguments)
    message = email.message_from_bytes(raw, policy=default_policy)
    assert set(message.keys()) == {
        "To",
        "Cc",
        "Subject",
        "In-Reply-To",
        "References",
        "MIME-Version",
        "Content-Type",
        "Content-Transfer-Encoding",
    }
    assert message["To"] == ", ".join(MAIL["to"])
    assert message["Cc"] == "muhasebe@example.com"
    assert message["Subject"] == MAIL["subject"]
    decoded = message.get_content().replace("\r\n", "\n")
    assert hashlib.sha256(decoded.encode()).hexdigest() == body_sha
    assert summary["arguments_json"] == json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "succeeded"


@pytest.mark.parametrize(
    "change",
    [
        {"to": ["Ahmet Yılmaz <ahmet@example.com>"]},  # display name
        {"to": ['"a,b"@example.com']},
        {"to": [f"u{i}@example.com" for i in range(11)]},
        {"cc": [f"c{i}@example.com" for i in range(9)]},  # 2 + 9 > 10
        {"subject": "Konu\r\nBcc: attacker@example.com"},
        {"in_reply_to": "<a@b>\r\nBcc: x@y.z"},
        {"thread_id": "t1/../x"},
        {"bcc": ["x@example.com"]},  # unknown field
        {"to": []},
    ],
)
def test_mail_send_refuses_bad_arguments_at_request_time(
    connect, fake, web_client, owner, workspace, change
):
    connect()
    response = _request(web_client, owner, workspace, "mail_send", {**MAIL, **change})
    assert response.status_code == 400
    assert not ApprovalRequest.objects.exists()


def test_a_server_error_after_the_send_is_unknown_outcome_never_resent(
    connect, fake, web_client, owner, workspace, approve
):
    connect()
    fake.send_status = 503
    request_id = _request(web_client, owner, workspace, "mail_send", MAIL).json()["id"]
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert execution.status == "unknown_outcome"
    assert len(fake.sent) == 1


def test_a_refused_send_fails_cleanly(connect, fake, web_client, owner, workspace, approve):
    connect()
    fake.send_status = 400
    request_id = _request(web_client, owner, workspace, "mail_send", MAIL).json()["id"]
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "tool_error")


def test_a_revoked_send_scope_before_approval_sends_nothing(
    connect, fake, web_client, owner, workspace, approve
):
    binding = connect()
    request_id = _request(web_client, owner, workspace, "mail_send", MAIL).json()["id"]
    fake.scopes = google.READ_SCOPES
    google.clear_token_cache()
    check_binding_pins(binding.id)
    approve(request_id)
    execution = ConnectorWriteExecution.objects.get()
    assert (execution.status, execution.error_type) == ("failed", "connector_changed")
    assert fake.sent == []


# --- calendar_create_event ---


EVENT = {
    "summary": "Bütçe toplantısı",
    "start": "2026-09-29T10:00:00+03:00",
    "end": "2026-09-29T11:00:00+03:00",
    "location": "Ofis",
    "description": "Gündem:\n1. Bütçe",
    "attendees": ["ahmet@example.com"],
}


def test_calendar_event_dialog_states_invitations_and_the_insert_matches_it(
    connect, fake, web_client, owner, workspace, approve
):
    connect()
    response = _request(web_client, owner, workspace, "calendar_create_event", EVENT)
    assert response.status_code == 201, response.content
    request_id = response.json()["id"]
    rows = _rows(web_client(owner).get(f"{URL}/{request_id}").json()["display_summary"])
    assert rows["Katılımcılar"] == "ahmet@example.com"
    assert "GÖNDERECEK" in rows["Davetler"]
    assert rows["Açıklama"] == EVENT["description"]
    approve(request_id)
    assert len(fake.inserted) == 1
    inserted = fake.inserted[0]
    assert inserted["params"] == {"sendUpdates": "all"}
    arguments = ApprovalRequest.objects.get(id=request_id).payload["arguments"]
    assert inserted["body"] == google.event_body(arguments)
    assert inserted["body"]["attendees"] == [{"email": "ahmet@example.com"}]


@pytest.mark.parametrize(
    "change",
    [
        {"end": "2026-09-29T09:00:00+03:00"},
        {"end": "2026-10-20T09:00:00+03:00"},
        {"attendees": [f"a{i}@example.com" for i in range(21)]},
        {"summary": "a\nb"},
    ],
)
def test_calendar_event_bad_arguments_are_refused(
    connect, fake, web_client, owner, workspace, change
):
    connect()
    response = _request(web_client, owner, workspace, "calendar_create_event", {**EVENT, **change})
    assert response.status_code == 400


def test_an_event_without_attendees_sends_no_invitations(
    connect, fake, web_client, owner, workspace, approve
):
    connect()
    request_id = _request(
        web_client, owner, workspace, "calendar_create_event", {**EVENT, "attendees": []}
    ).json()["id"]
    rows = _rows(web_client(owner).get(f"{URL}/{request_id}").json()["display_summary"])
    assert "göndermeyecek" in rows["Davetler"]
    approve(request_id)
    assert fake.inserted[0]["params"] == {"sendUpdates": "none"}


# --- credentials ---


def test_no_credential_value_in_db_cache_audit_or_logs(
    connect, fake, web_client, owner, workspace, approve, caplog
):
    from django.core.cache import caches

    from ai_core.models import ModelUsage
    from assistant.models import Message

    connect()
    _read(workspace, owner, "mail_read", {})
    request_id = _request(web_client, owner, workspace, "mail_send", MAIL).json()["id"]
    approve(request_id)
    assert ConnectorWriteExecution.objects.get().status == "succeeded"

    dumped = json.dumps(
        [
            list(AuditLog.objects.values()),
            list(ConnectorBinding.objects.values()),
            list(ConnectorWriteExecution.objects.values()),
            list(ApprovalRequest.objects.values()),
            list(ModelUsage.objects.values()),
            list(Message.objects.values()),
        ],
        default=str,
    )
    cached = b"".join(
        value if isinstance(value, bytes) else repr(value).encode()
        for cache in caches.all()
        for value in getattr(cache, "_cache", {}).values()
    )
    for canary in (
        google_fake.REFRESH_TOKEN_CANARY,
        google_fake.CLIENT_SECRET_CANARY,
        google_fake.ACCESS_TOKEN_CANARY,
    ):
        assert canary not in dumped
        assert canary.encode() not in cached
        assert canary not in caplog.text
    # The token object never prints its value.
    token = next(iter(google._TOKENS.values()))
    assert google_fake.ACCESS_TOKEN_CANARY not in repr(token)
