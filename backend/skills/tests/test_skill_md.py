"""Phase 12 D13: the strict SKILL.md parser -- valid files and every
refusal (permission keys, unknown keys, YAML features, sizes, names,
second documents), invisible formatting stripped."""

from pathlib import Path

import pytest

from skills.skill_md import (
    PERMISSION_KEYS,
    SkillParseError,
    content_sha256,
    parse_skill_md,
)

FIXTURES = Path(__file__).parent / "fixtures"


def doc(front, body="Gövde metni."):
    return f"---\n{front}\n---\n{body}\n"


def refused(text):
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(text)
    return exc_info.value.code


def test_the_valid_fixture_parses():
    parsed = parse_skill_md((FIXTURES / "valid_pr_description.md").read_text("utf-8"))
    assert parsed.name == "pr-aciklamasi"
    assert parsed.description.startswith("PR açıklaması nasıl yazılır: özet")
    assert parsed.license == "MIT"
    assert parsed.metadata == {"author": "owner", "version": "1.0"}
    assert parsed.body.startswith("# PR açıklaması")
    assert len(parsed.content_sha256) == 64


def test_minimal_plain_scalars():
    parsed = parse_skill_md(doc("name: my-skill\ndescription: Use it for notes"))
    assert parsed.front_matter == {"name": "my-skill", "description": "Use it for notes"}


def test_comments_and_blank_lines_are_skipped():
    parsed = parse_skill_md(doc("# a comment\nname: ab\n\ndescription: x y"))
    assert parsed.name == "ab"


def test_quoted_escapes():
    parsed = parse_skill_md(doc('name: ab\ndescription: "say \\"hi\\" and \\\\ ok"'))
    assert parsed.description == 'say "hi" and \\ ok'


def test_hash_is_stable_and_content_sensitive():
    a = parse_skill_md(doc("name: ab\ndescription: x"))
    b = parse_skill_md(doc("name: ab\ndescription: x"))
    c = parse_skill_md(doc("name: ab\ndescription: x", body="Other."))
    assert a.content_sha256 == b.content_sha256 != c.content_sha256
    assert a.content_sha256 == content_sha256(a.front_matter, a.body, {})


def test_render_round_trips():
    parsed = parse_skill_md((FIXTURES / "valid_pr_description.md").read_text("utf-8"))
    again = parse_skill_md(parsed.render())
    assert again.content_sha256 == parsed.content_sha256


@pytest.mark.parametrize("key", sorted(PERMISSION_KEYS))
def test_every_permission_key_is_refused(key):
    assert refused(doc(f"name: ab\ndescription: x\n{key}: yes")) == "permission_key_refused"


@pytest.mark.parametrize("key", ["Allowed-Tools", "ALLOW", "Auto_Approve", "allowed_tools"])
def test_permission_keys_any_case(key):
    assert refused(doc(f"name: ab\ndescription: x\n{key}: yes")) == "permission_key_refused"


def test_permission_key_inside_metadata_is_refused():
    text = doc("name: ab\ndescription: x\nmetadata:\n  allowed-tools: Bash")
    assert refused(text) == "permission_key_refused"


def test_the_hostile_fixture_is_refused():
    assert refused((FIXTURES / "hostile_allowed_tools.md").read_text("utf-8")) == (
        "permission_key_refused"
    )


def test_unknown_key():
    assert refused(doc("name: ab\ndescription: x\nversion: 2")) == "unknown_key"


@pytest.mark.parametrize(
    "front",
    [
        "name: ab\ndescription: &anchor x",
        "name: ab\ndescription: *alias",
        "name: ab\ndescription: !!python/object x",
        "name: ab\ndescription: [a, b]",
        "name: ab\ndescription: {a: b}",
        "name: ab\ndescription: |",
        "name: ab\ndescription: >",
        "name: ab\ndescription: 'single'",
        "name: ab\ndescription: a: b",
        "name: ab\ndescription: a #comment",
        "name: ab\ndescription: x\n  continued",
        "name: ab\ndescription: x\nmetadata: {a: b}",
        "name: ab\ndescription: \"x\\n\"",
        "? name\n: ab",
    ],
)
def test_yaml_features_are_refused(front):
    assert refused(doc(front)) in {"yaml_feature_refused", "invalid_line", "invalid_value"}


def test_the_alias_bomb_fixture_is_refused():
    assert refused((FIXTURES / "hostile_alias_bomb.md").read_text("utf-8")) == (
        "yaml_feature_refused"
    )


def test_tabs_in_indentation_are_refused():
    text = doc("name: ab\ndescription: x\nmetadata:\n\tk: v")
    assert refused(text) == "yaml_feature_refused"


def test_multi_line_scalar_is_refused():
    assert refused(doc("name: ab\ndescription: x\n  more")) == "yaml_feature_refused"


def test_oversize_body(settings):
    settings.SKILLS_BODY_MAX_CHARS = 100
    assert refused(doc("name: ab\ndescription: x", body="a" * 101)) == "body_too_large"
    assert parse_skill_md(doc("name: ab\ndescription: x", body="a" * 100)).body == "a" * 100


def test_missing_description():
    assert refused(doc("name: ab")) == "missing_description"


def test_description_too_long():
    assert refused(doc("name: ab\ndescription: " + "x" * 1025)) == "description_too_long"


def test_license_too_long():
    assert refused(doc("name: ab\ndescription: x\nlicense: " + "x" * 101)) == "license_too_long"


@pytest.mark.parametrize("name", ["A", "a", "Ab", "1ab", "a_b", "a b", "ab!", "x" * 65, "-ab"])
def test_bad_names(name):
    assert refused(doc(f'name: "{name}"\ndescription: x')) in {"invalid_name", "missing_name"}


def test_missing_name():
    assert refused(doc("description: x")) == "missing_name"


def test_second_document_in_front_matter():
    text = "---\nname: ab\ndescription: x\n...\n---\nbody\n"
    assert refused(text) == "second_document"


def test_second_document_after_the_first():
    text = "---\nname: ab\ndescription: x\n---\n---\nname: cd\n---\nbody\n"
    assert refused(text) == "second_document"


def test_missing_front_matter():
    assert refused("# just markdown\n") == "missing_front_matter"
    assert refused("---\nname: ab\ndescription: x\n") == "missing_front_matter"


def test_duplicate_key():
    assert refused(doc("name: ab\nname: cd\ndescription: x")) == "duplicate_key"


def test_too_many_metadata_keys():
    lines = "\n".join(f"  k{i}: v" for i in range(11))
    assert refused(doc(f"name: ab\ndescription: x\nmetadata:\n{lines}")) == "metadata_invalid"


def test_empty_body_is_refused():
    assert refused("---\nname: ab\ndescription: x\n---\n\n") == "body_empty"


def test_invisible_characters_are_stripped():
    zw = "​"
    bidi = "‮"
    text = doc(f"name: ab\ndescription: Not{zw}e{bidi}s", body=f"Hello{zw}{bidi} world\x07.")
    parsed = parse_skill_md(text)
    assert parsed.description == "Notes"
    assert parsed.body == "Hello world."
    assert "​" not in parsed.body


def test_crlf_and_bom_are_normalized():
    text = "﻿---\r\nname: ab\r\ndescription: x\r\n---\r\nBody\r\n"
    assert parse_skill_md(text).body == "Body"


def test_errors_never_echo_the_content():
    secret = "SECRET-PAYLOAD-123"
    with pytest.raises(SkillParseError) as exc_info:
        parse_skill_md(doc(f"name: ab\ndescription: x\nallowed-tools: {secret}"))
    assert secret not in str(exc_info.value)
