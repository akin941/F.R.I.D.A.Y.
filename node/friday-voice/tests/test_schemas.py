"""friday-voice/v1 schemas (brief D6): the frozen sets, the bounded-schema
meta-test and the policy-shaped guard."""

import ast
import re
from types import MappingProxyType

import pytest

from friday_voice.protocol.messages import (
    AUDIO_TYPES,
    CHILD_TO_HOST,
    CHILD_TO_HOST_TYPES,
    HOST_TO_CHILD,
    HOST_TO_CHILD_TYPES,
    MESSAGE_TYPES,
    POLICY_SHAPED,
    RELEASE_REASONS,
    SCHEMA_DIR,
    STATES,
    ProtocolError,
    load_schema,
    validate,
)

from conftest import REPO_ROOT

ALL_TYPES = sorted(HOST_TO_CHILD_TYPES | CHILD_TO_HOST_TYPES)


def test_the_host_to_child_set_is_frozen():
    assert HOST_TO_CHILD_TYPES == {
        "hello",
        "credential",
        "audio",
        "playback",
        "mode",
        "mute",
        "ptt",
        "link",
        "shutdown",
    }
    assert isinstance(HOST_TO_CHILD_TYPES, frozenset)


def test_the_child_to_host_set_is_frozen():
    assert CHILD_TO_HOST_TYPES == {
        "ready",
        "state",
        "play",
        "stop_playback",
        "earcon",
        "release_mic",
        "log",
    }
    assert isinstance(CHILD_TO_HOST_TYPES, frozenset)
    assert not HOST_TO_CHILD_TYPES & CHILD_TO_HOST_TYPES
    assert isinstance(MESSAGE_TYPES, MappingProxyType)
    with pytest.raises(TypeError):
        MESSAGE_TYPES[CHILD_TO_HOST] = frozenset({"open_url"})
    assert dict(AUDIO_TYPES) == {"audio": HOST_TO_CHILD, "play": CHILD_TO_HOST}


def test_one_schema_file_per_type_and_nothing_else():
    for direction, types in MESSAGE_TYPES.items():
        files = {p.name.removesuffix(".schema.json") for p in (SCHEMA_DIR / direction).glob("*")}
        assert files == set(types), direction
    assert {p.name for p in SCHEMA_DIR.iterdir()} == {HOST_TO_CHILD, CHILD_TO_HOST}


def _walk(schema, path="$"):
    yield path, schema
    for name, sub in (schema.get("properties") or {}).items():
        yield from _walk(sub, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"], f"{path}[]")
    for keyword in ("anyOf", "oneOf", "allOf"):
        for index, sub in enumerate(schema.get(keyword) or []):
            yield from _walk(sub, f"{path}.{keyword}[{index}]")


def _types(schema):
    declared = schema.get("type")
    if declared is None:
        return set()
    return {declared} if isinstance(declared, str) else set(declared)


@pytest.mark.parametrize("message_type", ALL_TYPES)
def test_every_schema_is_closed_and_bounded(message_type):
    schema = load_schema(message_type)
    assert schema["properties"]["type"]["const"] == message_type
    assert set(schema["required"]) == set(schema["properties"])
    for path, sub in _walk(schema):
        types = _types(sub)
        if "object" in types:
            assert sub.get("additionalProperties") is False, path
        if "string" in types:
            assert isinstance(sub.get("maxLength"), int), path
            assert "enum" in sub or "const" in sub or "pattern" in sub or path.endswith(
                ".detail"
            ), path
        if "integer" in types:
            assert isinstance(sub.get("minimum"), int), path
            assert isinstance(sub.get("maximum"), int), path
        if "array" in types:
            assert isinstance(sub.get("maxItems"), int), path
        assert "number" not in types, f"{path}: integers only"
        if not types:
            assert "enum" in sub or "const" in sub, f"{path}: untyped values are enumerated"


def test_no_type_or_property_name_is_policy_shaped():
    names = set(ALL_TYPES)
    for message_type in ALL_TYPES:
        for _path, sub in _walk(load_schema(message_type)):
            names |= set(sub.get("properties") or {})
    hits = sorted(name for name in names if POLICY_SHAPED.search(name))
    assert hits == []  # no reserved exception in friday-voice/v1


def test_the_policy_regex_is_the_node_protocols_byte_for_byte():
    source = (REPO_ROOT / "backend" / "nodes" / "protocol" / "messages.py").read_text("utf-8")
    match = re.search(r"POLICY_SHAPED = re\.compile\(\s*(r\"[^\"]*\")\s*\)", source)
    assert match, "nodes.protocol.messages.POLICY_SHAPED moved"
    assert ast.literal_eval(match.group(1)) == POLICY_SHAPED.pattern


@pytest.mark.parametrize(
    "name", ["policy.set", "allowlist.add", "config", "set_settings", "grant", "exec_policy"]
)
def test_policy_shaped_names_are_refused_as_types(name):
    assert POLICY_SHAPED.search(name)
    for direction in (HOST_TO_CHILD, CHILD_TO_HOST):
        with pytest.raises(ProtocolError) as caught:
            validate({"type": name}, direction)
        assert caught.value.code == "unknown_type"


def test_the_child_cannot_ask_for_anything_but_its_own_set():
    """No child->host message changes mode, mute, hotkey, device,
    credential or node policy, or names a URL or a command."""
    forbidden = re.compile(r"(?i)url|command|exec|^run|^open|hotkey|device|token|credential|^mode$")
    for message_type in CHILD_TO_HOST_TYPES:
        for name in load_schema(message_type)["properties"]:
            if message_type == "state" and name == "mode":
                continue  # an echo of the host's mode for the voice.state event
            assert not forbidden.search(name), (message_type, name)
        assert not forbidden.search(message_type), message_type


def test_release_reasons_and_states_match_the_schemas():
    assert load_schema("release_mic")["properties"]["reason"]["enum"] == list(RELEASE_REASONS)
    assert load_schema("state")["properties"]["state"]["enum"] == list(STATES)
