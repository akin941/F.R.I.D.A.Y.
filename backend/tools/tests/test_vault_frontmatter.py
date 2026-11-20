import uuid
from types import SimpleNamespace

from django.utils import timezone

from tools.vault_frontmatter import parse_frontmatter, render_frontmatter


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
