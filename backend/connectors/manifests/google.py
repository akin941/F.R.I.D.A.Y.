"""Google mail and calendar -- native bridge, personal class (brief D9).

Reads (`mail_search`, `mail_read`, `calendar_agenda`) are ungated, audited,
fenced and bounded like every connector read, `personal` -> web only,
personal workspace only, and they route the conversation to a
personal-data-allowed (local) model (D5). Writes (`mail_send`,
`calendar_create_event`) are gate-class and user-invocable: the decision
dialog shows raw addresses (the serializers accept bare addresses only, so
a display name can never appear) and the full content, and the execution
builds the request from the snapshotted arguments alone
(`connectors.bridges.google.build_mime` / `event_body`).

Governance twin: `docs/mcp/google.md`.
"""

import hashlib
import re

from django.core.validators import RegexValidator
from rest_framework import serializers

from connectors.bridges import google
from connectors.manifest import (
    KIND_NATIVE,
    STATUS_ACTIVE,
    TOOL_READ,
    TOOL_WRITE,
    WORKSPACE_PERSONAL,
    ConnectorManifest,
    ToolPolicy,
)
from core.channels import CHANNEL_WEB

# Bare addresses only -- no display names, no quoted local parts, no
# comments: what the dialog shows is exactly what goes into the header.
_ADDRESS_RE = re.compile(
    r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\Z"
)
# Every character the email layer treats as a line break (CR, LF, VT, FF,
# NEL, U+2028/U+2029) or NUL is refused here, at request time, with a clear
# 400 -- not later as an opaque protocol_error inside build_mime.
_ONE_LINE_RE = re.compile(r"^[^\r\n\x00\x0b\x0c\x85\u2028\u2029]*\Z")

MAIL_RECIPIENTS_MAX = 10
EVENT_ATTENDEES_MAX = 20
AGENDA_DAYS_MAX = 31
MAIL_BODY_MAX = 20000
EVENT_MAX_DAYS = 14


class StrictSerializer(serializers.Serializer):
    """Unknown keys are an error, not silently dropped."""

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unknown field." for key in sorted(unknown)})
        return attrs


def _address_field():
    return serializers.CharField(
        max_length=254, validators=[RegexValidator(_ADDRESS_RE, "A bare e-mail address.")]
    )


def _one_line(max_length, **kwargs):
    return serializers.CharField(
        max_length=max_length,
        validators=[RegexValidator(_ONE_LINE_RE, "A single line.")],
        **kwargs,
    )


def _normalize_newlines(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


# --- reads ---


_QUERY_HELP = (
    "Gmail search syntax, e.g. 'from:ali@example.com newer_than:2d' or 'is:unread'; "
    "empty = the inbox"
)


class MailSearchArgs(StrictSerializer):
    query = _one_line(
        200, required=False, allow_blank=True, default="in:inbox", help_text=_QUERY_HELP
    )
    max_results = serializers.IntegerField(
        min_value=1,
        max_value=google.MAIL_SEARCH_MAX,
        required=False,
        default=10,
        help_text="1-20, default 10",
    )


class MailReadArgs(StrictSerializer):
    # Without an id: the newest message matching `query` ("the last mail").
    message_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        validators=[RegexValidator(r"^(|[A-Za-z0-9_-]{1,128})\Z", "A Gmail message id.")],
        help_text="a Gmail message id; empty = the newest message matching query",
    )
    query = _one_line(
        200, required=False, allow_blank=True, default="in:inbox", help_text=_QUERY_HELP
    )


class CalendarAgendaArgs(StrictSerializer):
    start_date = serializers.DateField(
        required=False, allow_null=True, default=None, help_text="YYYY-MM-DD; empty = today"
    )
    days = serializers.IntegerField(
        min_value=1,
        max_value=AGENDA_DAYS_MAX,
        required=False,
        default=1,
        help_text="how many days from start_date, 1-31, default 1",
    )


# --- writes ---


class MailSendArgs(StrictSerializer):
    to = serializers.ListField(child=_address_field(), min_length=1, max_length=MAIL_RECIPIENTS_MAX)
    cc = serializers.ListField(
        child=_address_field(), required=False, default=list, max_length=MAIL_RECIPIENTS_MAX
    )
    subject = _one_line(200)
    body = serializers.CharField(max_length=MAIL_BODY_MAX, trim_whitespace=False)
    thread_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        validators=[RegexValidator(r"^(|[A-Za-z0-9]{1,64})\Z", "A Gmail thread id.")],
    )
    in_reply_to = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        validators=[RegexValidator(r"^(|<[^<>\s]{1,250}>)\Z", "A Message-ID.")],
    )

    def validate_body(self, value):
        # MIME text always ends with a line break; normalizing here keeps the
        # displayed body (and its sha256) byte-identical to what is sent.
        value = _normalize_newlines(value)
        return value if value.endswith("\n") else value + "\n"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if len(attrs["to"]) + len(attrs.get("cc") or []) > MAIL_RECIPIENTS_MAX:
            raise serializers.ValidationError({"cc": "At most 10 recipients in To and Cc."})
        return attrs


class CalendarCreateEventArgs(StrictSerializer):
    summary = _one_line(200)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    description = serializers.CharField(
        max_length=5000, required=False, allow_blank=True, default="", trim_whitespace=False
    )
    location = _one_line(300, required=False, allow_blank=True, default="")
    attendees = serializers.ListField(
        child=_address_field(), required=False, default=list, max_length=EVENT_ATTENDEES_MAX
    )

    def validate_description(self, value):
        return _normalize_newlines(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["end"] <= attrs["start"]:
            raise serializers.ValidationError({"end": "The end must be after the start."})
        if (attrs["end"] - attrs["start"]).days >= EVENT_MAX_DAYS:
            raise serializers.ValidationError({"end": "An event lasts less than 14 days."})
        return attrs


def body_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_mail_send(arguments):
    """Decision-dialog rows: raw addresses, full subject and body, and the
    body's sha256 (the payload keeps it via `arguments_sha256`; the MIME
    sent is built from exactly these fields)."""
    rows = [
        {"label": "Kime (To)", "value": ", ".join(arguments["to"])},
        {"label": "Bilgi (Cc)", "value": ", ".join(arguments.get("cc") or []) or "—"},
        {"label": "Konu", "value": arguments["subject"]},
        {"label": "Metin", "value": arguments["body"]},
        {"label": "Metin sha256", "value": body_sha256(arguments["body"])},
    ]
    if arguments.get("thread_id"):
        rows.append({"label": "Gmail konu dizisi (thread)", "value": arguments["thread_id"]})
    if arguments.get("in_reply_to"):
        rows.append({"label": "Yanıtlanan ileti (In-Reply-To)", "value": arguments["in_reply_to"]})
    rows.append(
        {
            "label": "Gönderen",
            "value": "Bağlı Google hesabı (Gmail, gönderici adresini kendi koyar)",
        }
    )
    return rows


def render_calendar_create_event(arguments):
    attendees = arguments.get("attendees") or []
    invitations = (
        f"Google {len(attendees)} katılımcıya davet e-postası GÖNDERECEK."
        if attendees
        else "Katılımcı yok; Google davet göndermeyecek."
    )
    return [
        {"label": "Başlık", "value": arguments["summary"]},
        {"label": "Başlangıç", "value": arguments["start"]},
        {"label": "Bitiş", "value": arguments["end"]},
        {"label": "Yer", "value": arguments.get("location") or "—"},
        {"label": "Açıklama", "value": arguments.get("description") or "—"},
        {"label": "Katılımcılar", "value": ", ".join(attendees) or "—"},
        {"label": "Davetler", "value": invitations},
        {"label": "Takvim", "value": "Bağlı Google hesabının birincil takvimi"},
    ]


MANIFEST = ConnectorManifest(
    name="google",
    version=1,
    display_name="Google (Gmail + Takvim)",
    kind=KIND_NATIVE,
    status=STATUS_ACTIVE,
    data_class="personal",
    workspace_types=frozenset({WORKSPACE_PERSONAL}),
    read_channels=frozenset({CHANNEL_WEB}),
    credential_envs=google.CREDENTIAL_ENVS,
    tools=(
        ToolPolicy(
            name="mail_search",
            server_tool="gmail.messages.search",
            kind=TOOL_READ,
            description=(
                "Search the owner's Gmail (Gmail search syntax, default the inbox) and list "
                "up to 20 messages: sender, subject, date and a short snippet."
            ),
            argument_serializer=MailSearchArgs,
            schema_sha256=google.TOOL_PINS["gmail.messages.search"],
        ),
        ToolPolicy(
            name="mail_read",
            server_tool="gmail.messages.read",
            kind=TOOL_READ,
            description=(
                "Read one Gmail message's text body (bounded): by message id, or the newest "
                "message matching a Gmail search (default: the latest inbox message)."
            ),
            argument_serializer=MailReadArgs,
            schema_sha256=google.TOOL_PINS["gmail.messages.read"],
        ),
        ToolPolicy(
            name="calendar_agenda",
            server_tool="calendar.events.agenda",
            kind=TOOL_READ,
            description=(
                "List the owner's primary Google Calendar events from a start date "
                "(default today) for 1-31 days."
            ),
            argument_serializer=CalendarAgendaArgs,
            schema_sha256=google.TOOL_PINS["calendar.events.agenda"],
        ),
        ToolPolicy(
            name="mail_send",
            server_tool="gmail.messages.send",
            kind=TOOL_WRITE,
            description="Send a plain-text e-mail from the owner's Gmail account.",
            argument_serializer=MailSendArgs,
            schema_sha256=google.TOOL_PINS["gmail.messages.send"],
            gate_name="mail_send",
            renderer=render_mail_send,
        ),
        ToolPolicy(
            name="calendar_create_event",
            server_tool="calendar.events.insert",
            kind=TOOL_WRITE,
            description=(
                "Create an event in the owner's primary Google Calendar; Google e-mails "
                "invitations to any attendees."
            ),
            argument_serializer=CalendarCreateEventArgs,
            schema_sha256=google.TOOL_PINS["calendar.events.insert"],
            gate_name="calendar_create_event",
            renderer=render_calendar_create_event,
        ),
    ),
)

BRIDGE = google.GoogleBridge()
