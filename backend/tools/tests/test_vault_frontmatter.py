import uuid
from types import SimpleNamespace

from django.utils import timezone

from tools.vault_frontmatter import (
    body_sha256,
    is_owner_edited,
    parse_frontmatter,
    render_frontmatter,
    split_frontmatter,
)


def _fake_memory(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "memory_type": "semantic",
        "workspace_id": uuid.uuid4(),
        "sensitivity": "normal",
        "title": "A Title",
        "content": "Some body content.",
        "updated_at": timezone.now(),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestRenderParseRoundTrip:
    def test_round_trip_recovers_all_fields(self):
        memory = _fake_memory()
        text = render_frontmatter(memory)
        fields = parse_frontmatter(text)
        assert fields == {
            "friday_id": str(memory.id),
            "type": "semantic",
            "workspace": str(memory.workspace_id),
            "project": "",
            "sensitivity": "normal",
            "source": "friday_export",
            "updated_at": memory.updated_at.isoformat(),
            "friday_format": "2",
            "content_sha256": body_sha256(split_frontmatter(text)[1]),
        }

    def test_body_contains_title_and_content(self):
        memory = _fake_memory(title="My Title", content="My body")
        text = render_frontmatter(memory)
        assert "# My Title" in text
        assert "My body" in text


class TestParseMalformedInput:
    def test_missing_opening_delimiter_returns_none(self):
        assert parse_frontmatter("friday_id: abc\n---\n") is None

    def test_missing_closing_delimiter_returns_none(self):
        assert parse_frontmatter("---\nfriday_id: abc\n") is None

    def test_line_without_colon_returns_none(self):
        assert parse_frontmatter("---\nthis has no colon\n---\n") is None

    def test_empty_key_returns_none(self):
        assert parse_frontmatter("---\n: value\n---\n") is None

    def test_empty_string_returns_none(self):
        assert parse_frontmatter("") is None

    def test_empty_frontmatter_block_returns_empty_dict(self):
        assert parse_frontmatter("---\n---\n") == {}


class TestFormatV2:
    """Phase 8B branch 7 (brief D12): `friday_format: 2` + `content_sha256`."""

    def test_content_sha256_is_the_body_below_the_frontmatter(self):
        memory = _fake_memory(title="T", content="Body")
        text = render_frontmatter(memory)
        fields, body = split_frontmatter(text)
        assert body == "\n# T\n\nBody\n"
        assert fields["content_sha256"] == body_sha256(body)
        assert fields["friday_format"] == "2"

    def test_rendered_file_is_not_edited(self):
        assert is_owner_edited(render_frontmatter(_fake_memory())) is False

    def test_body_edit_is_detected(self):
        text = render_frontmatter(_fake_memory(content="Original"))
        assert is_owner_edited(text.replace("Original", "Changed by the owner")) is True

    def test_crlf_line_endings_are_not_an_edit(self):
        text = render_frontmatter(_fake_memory(content="line one\nline two"))
        assert is_owner_edited(text.replace("\n", "\r\n")) is False

    def test_v1_file_without_a_hash_is_not_a_conflict(self):
        v1 = "---\nfriday_id: x\nsource: friday_export\n---\n\n# T\n\nAnything at all\n"
        assert is_owner_edited(v1) is False

    def test_unparseable_frontmatter_counts_as_edited(self):
        assert is_owner_edited("the owner replaced everything") is True
        assert is_owner_edited("---\nno closing delimiter\n") is True

    def test_frontmatter_only_edit_is_not_a_body_edit(self):
        text = render_frontmatter(_fake_memory())
        assert is_owner_edited(text.replace("project: ", "project: mine")) is False
