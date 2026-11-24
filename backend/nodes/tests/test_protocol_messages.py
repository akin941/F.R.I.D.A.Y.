"""The message registry (Phase 9 brief D5): the frozen server -> node set,
the policy-mutation guard over every type name and schema property, and a
meta-test that walks every schema (protocol and capability contracts) for
`additionalProperties: false` and bounds on every string, array and
integer."""

import json
import uuid

import pytest

from nodes.capabilities import load_contracts
from nodes.protocol import messages
from nodes.protocol.messages import (
    MESSAGE_TYPES,
    NODE_TO_SERVER,
    OPAQUE_KEYWORD,
    POLICY_SHAPED,
    RESERVED_POLICY_SHAPED_PROPERTIES,
    SCHEMA_DIR,
    SERVER_TO_NODE,
    ProtocolError,
    catalogue_sha256,
    decode_frame,
    load_schema,
    validate,
)
from nodes.protocol.signing import SigningKey, build_invoke, sign_envelope

ALL_TYPES = sorted(NODE_TO_SERVER | SERVER_TO_NODE)


def _id():
    return str(uuid.uuid4())


def sample(message_type):
    """One valid message of each type."""
    base = {"v": 1, "type": message_type, "id": _id()}
    contract = load_contracts()["git.status"]
    catalogue = [contract.catalogue_entry()]
    bodies = {
        "hello": {
            "node_version": "0.1.0",
            "os_version": "macOS 15.1",
            "catalogue": catalogue,
            "catalogue_sha256": catalogue_sha256(catalogue),
        },
        "heartbeat": {"seq": 3, "uptime_seconds": 45, "spool_count": 0, "spool_bytes": 0},
        "result": {
            "invocation_id": _id(),
            "status": "ok",
            "refusal_code": None,
            "output": {"branch": "main"},
            "truncated": False,
            "output_sha256": "0" * 64,
            "duration_ms": 12,
        },
        "event": {
            "kind": "claude_code.hook",
            "batch": [{"event_id": "e1", "event": "Stop"}],
            "spooled": False,
        },
        "hello.ack": {
            "node_id": _id(),
            "heartbeat_seconds": 15,
            "rotate_after_days": 30,
            "server_time": 1_800_000_000,
            "accepted": ["git.status"],
        },
        "heartbeat.ack": {"seq": 3, "server_time": 1_800_000_000},
        "event.ack": {"event_id": _id(), "stored": 1, "duplicate": 0, "dropped": 0},
        "bye": {"reason": "revoked"},
    }
    if message_type == "invoke":
        envelope = build_invoke(
            node_id=_id(),
            workspace_id=_id(),
            capability="git.status",
            capability_version=1,
            args={"repo_path": "~/work/acme"},
            trace_id="t-1",
            issued_at=1_800_000_000,
            ttl_seconds=30,
        )
        return sign_envelope(envelope, SigningKey.generate())
    return {**base, **bodies[message_type]}


def _walk(schema, path="$"):
    """Yield (path, sub-schema) for every sub-schema, not descending into
    opaque holes."""
    yield path, schema
    if not isinstance(schema, dict) or OPAQUE_KEYWORD in schema:
        return
    for name, sub in (schema.get("properties") or {}).items():
        yield from _walk(sub, f"{path}.{name}")
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"], f"{path}[]")
    for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for index, sub in enumerate(schema.get(keyword) or []):
            if isinstance(sub, dict) and "if" in sub:
                # Phase 11 D15: a conditional is structure, not a value
                # schema; the sub-schemas its branches constrain are walked.
                for branch in ("if", "then", "else"):
                    for name, inner in ((sub.get(branch) or {}).get("properties") or {}).items():
                        yield from _walk(inner, f"{path}.{keyword}[{index}].{branch}.{name}")
                continue
            yield from _walk(sub, f"{path}.{keyword}[{index}]")
    for name, sub in (schema.get("$defs") or {}).items():
        yield from _walk(sub, f"{path}.$defs.{name}")


def _all_schemas():
    for message_type in ALL_TYPES:
        yield f"message:{message_type}", load_schema(message_type)
    for name, contract in load_contracts().items():
        yield f"capability:{name}:args", contract.args_schema
        yield f"capability:{name}:result", contract.result_schema


# --- the frozen registry ---


def test_the_server_to_node_set_is_exactly_the_five_types():
    assert SERVER_TO_NODE == {"hello.ack", "heartbeat.ack", "invoke", "event.ack", "bye"}
    assert NODE_TO_SERVER == {"hello", "heartbeat", "result", "event"}
    assert isinstance(SERVER_TO_NODE, frozenset) and isinstance(NODE_TO_SERVER, frozenset)
    assert not NODE_TO_SERVER & SERVER_TO_NODE
    with pytest.raises(TypeError):
        MESSAGE_TYPES[1]["server_to_node"] = frozenset({"policy.set"})
    with pytest.raises(TypeError):
        MESSAGE_TYPES[3] = MESSAGE_TYPES[1]


def test_one_schema_file_per_type_and_nothing_else():
    files = {path.name.removesuffix(".schema.json") for path in SCHEMA_DIR.glob("*.json")}
    assert files == set(ALL_TYPES)


# --- the policy-mutation guard ---


def test_no_type_name_is_policy_shaped():
    for message_type in ALL_TYPES:
        assert not POLICY_SHAPED.search(message_type), message_type


def test_no_schema_property_is_policy_shaped():
    hits = set()
    for label, schema in _all_schemas():
        for path, sub in _walk(schema):
            for name in (sub.get("properties") or {}) if isinstance(sub, dict) else ():
                if POLICY_SHAPED.search(name):
                    hits.add((label.removeprefix("message:"), name, path))
    # The only match is the reserved Phase 10 grant slot of `invoke`.
    assert {(label, name) for label, name, _ in hits} == RESERVED_POLICY_SHAPED_PROPERTIES
    invoke_grant = load_schema("invoke")["properties"]["grant"]
    assert invoke_grant["type"] == ["null", "object"]


def test_the_guard_regex_catches_policy_shaped_names():
    for name in (
        "policy.set",
        "allowlist.add",
        "catalogue.set",
        "catalogue_set",
        "config.update",
        "grant.add",
        "exec_policy",
        "Settings",
    ):
        assert POLICY_SHAPED.search(name), name
    assert not POLICY_SHAPED.search("grant_id")


@pytest.mark.parametrize(
    "message_type", ["policy.set", "allowlist.add", "catalogue.set", "config.update", "grant.add"]
)
def test_policy_shaped_types_are_unknown_in_both_directions(message_type):
    for direction in MESSAGE_TYPES[1]:
        with pytest.raises(ProtocolError) as exc:
            validate({"v": 1, "type": message_type, "id": _id()}, direction)
        assert exc.value.code == "unknown_type"


# --- the schema meta-test ---


def test_every_schema_is_closed_and_bounded():
    problems = []
    for label, schema in _all_schemas():
        for path, sub in _walk(schema):
            if not isinstance(sub, dict) or OPAQUE_KEYWORD in sub:
                if isinstance(sub, dict) and "object" in _types(sub):
                    if "maxProperties" not in sub:
                        problems.append((label, path, "opaque object without maxProperties"))
                if isinstance(sub, dict) and "array" in _types(sub):
                    if "maxItems" not in sub:
                        problems.append((label, path, "opaque array without maxItems"))
                continue
            types = _types(sub)
            if "object" in types and sub.get("additionalProperties") is not False:
                problems.append((label, path, "object without additionalProperties: false"))
            if "string" in types and "maxLength" not in sub:
                problems.append((label, path, "string without maxLength"))
            if "array" in types and "maxItems" not in sub:
                problems.append((label, path, "array without maxItems"))
            if "integer" in types and "const" not in sub:
                if "minimum" not in sub or "maximum" not in sub:
                    problems.append((label, path, "integer without minimum/maximum"))
            if "number" in types:
                problems.append((label, path, "non-integer numbers are not allowed"))
            if not types and "const" not in sub and "enum" not in sub:
                problems.append((label, path, "untyped schema"))
    assert problems == []


def _types(schema):
    value = schema.get("type")
    if value is None:
        return set()
    return {value} if isinstance(value, str) else set(value)


def test_every_message_requires_v_type_and_id():
    for message_type in ALL_TYPES:
        schema = load_schema(message_type)
        assert {"v", "type", "id"} <= set(schema["required"])
        assert set(schema["required"]) == set(schema["properties"])  # nothing optional
        assert schema["properties"]["type"]["const"] == message_type


# --- validate / decode_frame ---


@pytest.mark.parametrize("message_type", ALL_TYPES)
def test_a_sample_of_every_type_validates_in_its_direction_only(message_type):
    message = sample(message_type)
    direction = "node_to_server" if message_type in NODE_TO_SERVER else "server_to_node"
    other = "server_to_node" if direction == "node_to_server" else "node_to_server"
    assert validate(message, direction) is message
    with pytest.raises(ProtocolError) as exc:
        validate(message, other)
    assert exc.value.code == "unknown_type"


@pytest.mark.parametrize("message_type", ALL_TYPES)
def test_an_extra_key_is_a_schema_error(message_type):
    message = {**sample(message_type), "extra": 1}
    direction = "node_to_server" if message_type in NODE_TO_SERVER else "server_to_node"
    with pytest.raises(ProtocolError) as exc:
        validate(message, direction)
    assert exc.value.code == "schema"


def test_schema_errors_never_echo_the_payload():
    message = {**sample("bye"), "reason": "SECRET-sk-ant-abc123"}
    with pytest.raises(ProtocolError) as exc:
        validate(message, "server_to_node")
    assert "SECRET" not in str(exc.value)


def test_floats_and_trailing_newlines_are_schema_errors():
    with pytest.raises(ProtocolError) as exc:
        validate({**sample("heartbeat"), "seq": 3.0}, "node_to_server")
    assert exc.value.code == "schema"
    # `pattern` is a full match: a trailing newline does not slip past `$`.
    with pytest.raises(ProtocolError):
        validate({**sample("bye"), "id": _id() + "\n"}, "server_to_node")


def test_a_non_null_grant_and_a_non_read_class_pass_the_schema():
    # They must reach the node's own checks (D5 check 13, class_not_allowed)
    # instead of being swallowed as `schema` (check 4).
    invoke = sample("invoke")
    validate({**invoke, "grant": {"approval_id": _id()}}, "server_to_node")
    validate({**invoke, "class": "gate"}, "server_to_node")


def test_decode_frame_runs_checks_one_to_four_in_order():
    too_large = json.dumps({"pad": "x" * messages.MAX_FRAME_BYTES})
    cases = [
        (too_large, "too_large"),
        ("{not json", "malformed"),
        ('{"a":1.5}', "malformed"),
        (json.dumps({"v": 1, "type": "policy.set", "id": _id()}), "unknown_type"),
        (json.dumps({**sample("bye"), "reason": "nope"}), "schema"),
    ]
    for raw, code in cases:
        with pytest.raises(ProtocolError) as exc:
            decode_frame(raw, "server_to_node")
        assert exc.value.code == code
    bye = sample("bye")
    assert decode_frame(json.dumps(bye).encode(), "server_to_node") == bye


def test_refusal_codes_are_the_d5_order():
    assert messages.NODE_REFUSAL_CODES[:4] == messages.FRAME_ERROR_CODES
    assert messages.NODE_REFUSAL_CODES[-1] == "not_granted"
    assert len(set(messages.NODE_REFUSAL_CODES)) == 17
    schema_enum = load_schema("result")["properties"]["refusal_code"]["enum"]
    assert schema_enum == [*messages.NODE_REFUSAL_CODES, None]


def test_unknown_direction_is_a_programming_error():
    with pytest.raises(ValueError):
        validate(sample("bye"), "sideways")
