"""Protocol v2 registry and schemas (Phase 10 brief D1): the frozen sets
(server -> node identical to v1, node -> server adds exactly
`confirmation` and `output`), the policy guard over every v2 type and
property with `invoke.grant` the only exception, the bounds meta-test over
v2 schemas and the v2 capability / recipe schemas, version pinning and
subprotocol negotiation."""

import json
import uuid

import pytest

from nodes.capabilities import load_action_contracts, load_recipes
from nodes.protocol import messages
from nodes.protocol.messages import (
    MAX_BYTES_KEYWORD,
    MAX_GRANT_TTL_SECONDS,
    MESSAGE_TYPES,
    NODE_CHECKS_V2,
    NODE_REFUSAL_CODES,
    NODE_REFUSAL_CODES_V2,
    NODE_TO_SERVER,
    NODE_TO_SERVER_V2,
    POLICY_SHAPED,
    RESERVED_POLICY_SHAPED_PROPERTIES,
    SCHEMA_DIRS,
    SERVER_TO_NODE,
    SERVER_TO_NODE_V2,
    ProtocolError,
    all_types,
    decode_frame,
    direction_of,
    load_schema,
    negotiate_subprotocol,
    validate,
    validate_sub_schema,
)
from nodes.tests.test_protocol_messages import _types, _walk
from nodes.tests.test_protocol_messages import sample as sample_v1
from nodes.tests.vectors_v2 import gated_envelope, plain_envelope

V2_TYPES = sorted(all_types(2))


def _id():
    return str(uuid.uuid4())


def sample_v2(message_type):
    """One valid v2 message of each type."""
    if message_type == "invoke":
        return gated_envelope()
    if message_type == "confirmation":
        return {
            "v": 2,
            "type": "confirmation",
            "id": _id(),
            "invocation_id": _id(),
            "state": "shown",
            "at": 1_800_000_000,
        }
    if message_type == "output":
        return {
            "v": 2,
            "type": "output",
            "id": _id(),
            "invocation_id": _id(),
            "seq": 0,
            "stream": "stdout",
            "data": "collected 12 items\n",
            "truncated": False,
        }
    message = {**sample_v1(message_type), "v": 2}
    if message_type == "result":
        message.update(
            {
                "exit_code": 0,
                "output_bytes": 1234,
                "tail": "12 passed in 0.4s\n",
                "confirm_state": "approved",
                "sandbox_violation": False,
                "output": None,
                "output_sha256": "",
            }
        )
    return message


def _all_v2_schemas():
    for message_type in V2_TYPES:
        yield f"message:{message_type}", load_schema(message_type, 2)
    yield "message:grant", load_schema("grant", 2)
    for name, contract in load_action_contracts().items():
        yield f"capability:{name}:args", contract.args_schema
        yield f"capability:{name}:result", contract.result_schema
    for name, recipe in load_recipes().items():
        yield f"recipe:{name}:params", recipe.params_schema


# --- the frozen registry ---


def test_server_to_node_is_identical_in_v1_and_v2():
    assert SERVER_TO_NODE_V2 is SERVER_TO_NODE
    assert MESSAGE_TYPES[2]["server_to_node"] == {
        "hello.ack",
        "heartbeat.ack",
        "invoke",
        "event.ack",
        "bye",
    }
    assert MESSAGE_TYPES[1]["server_to_node"] == MESSAGE_TYPES[2]["server_to_node"]


def test_node_to_server_v2_adds_exactly_confirmation_and_output():
    assert NODE_TO_SERVER_V2 - NODE_TO_SERVER == {"confirmation", "output"}
    assert NODE_TO_SERVER <= NODE_TO_SERVER_V2
    assert isinstance(NODE_TO_SERVER_V2, frozenset)
    assert MESSAGE_TYPES[2]["node_to_server"] is NODE_TO_SERVER_V2
    assert set(MESSAGE_TYPES) == {1, 2}


def test_there_is_no_server_side_cancel():
    for name in MESSAGE_TYPES[2]["server_to_node"]:
        assert not any(word in name for word in ("cancel", "stop", "kill", "abort")), name


def test_one_v2_schema_file_per_type_plus_the_grant():
    files = {p.name.removesuffix(".schema.json") for p in SCHEMA_DIRS[2].glob("*.json")}
    assert files == set(V2_TYPES) | {"grant"}


def test_every_v2_schema_pins_v_to_2():
    for message_type in V2_TYPES:
        schema = load_schema(message_type, 2)
        assert schema["properties"]["v"] == {"type": "integer", "const": 2}
        assert {"v", "type", "id"} <= set(schema["required"])
        assert set(schema["required"]) == set(schema["properties"])
        assert schema["properties"]["type"]["const"] == message_type
    assert load_schema("grant", 2)["properties"]["v"]["const"] == 2


def test_v1_types_keep_their_v1_properties_in_v2():
    for message_type in sorted(NODE_TO_SERVER | SERVER_TO_NODE):
        v1 = set(load_schema(message_type, 1)["properties"])
        v2 = set(load_schema(message_type, 2)["properties"])
        assert v1 <= v2, message_type
        if message_type != "result":
            assert v1 == v2, message_type
    added = set(load_schema("result", 2)["properties"]) - set(load_schema("result", 1)["properties"])
    assert added == {"exit_code", "output_bytes", "tail", "confirm_state", "sandbox_violation"}


def test_the_invoke_grant_embeds_the_grant_schema_verbatim():
    grant = load_schema("grant", 2)
    embedded = load_schema("invoke", 2)["properties"]["grant"]
    assert embedded["type"] == ["null", "object"]
    assert {k: v for k, v in embedded.items() if k != "type"} == {
        k: v for k, v in grant.items() if k not in ("type", "$schema")
    }
    assert grant["type"] == "object"


# --- the policy-mutation guard over v2 ---


def test_no_v2_type_name_is_policy_shaped():
    for message_type in V2_TYPES:
        assert not POLICY_SHAPED.search(message_type), message_type


def test_no_v2_schema_property_is_policy_shaped_except_invoke_grant():
    hits = set()
    for label, schema in _all_v2_schemas():
        for path, sub in _walk(schema):
            for name in (sub.get("properties") or {}) if isinstance(sub, dict) else ():
                if POLICY_SHAPED.search(name):
                    hits.add((label.removeprefix("message:"), name, path))
    assert {(label, name) for label, name, _ in hits} == RESERVED_POLICY_SHAPED_PROPERTIES
    # The grant's own properties (`grant_id`, `approval_id`, ...) pass as written.
    assert "grant_id" in load_schema("grant", 2)["properties"]


@pytest.mark.parametrize(
    "message_type",
    ["policy.set", "allowlist.add", "sandbox.update", "recipe.add", "grant.add", "cancel"],
)
def test_policy_shaped_and_unknown_types_are_unknown_in_v2(message_type):
    for direction in MESSAGE_TYPES[2]:
        with pytest.raises(ProtocolError) as exc:
            validate({"v": 2, "type": message_type, "id": _id()}, direction, 2)
        assert exc.value.code == "unknown_type"


# --- the bounds meta-test over v2 ---


def test_every_v2_schema_is_closed_and_bounded():
    problems = []
    for label, schema in _all_v2_schemas():
        for path, sub in _walk(schema):
            if not isinstance(sub, dict) or messages.OPAQUE_KEYWORD in sub:
                if isinstance(sub, dict) and "object" in _types(sub):
                    if "maxProperties" not in sub:
                        problems.append((label, path, "opaque object without maxProperties"))
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


def test_tail_and_output_data_are_bounded_in_utf8_bytes():
    tail = load_schema("result", 2)["properties"]["tail"]
    data = load_schema("output", 2)["properties"]["data"]
    assert tail[MAX_BYTES_KEYWORD] == messages.MAX_TAIL_BYTES == 4096
    assert data[MAX_BYTES_KEYWORD] == messages.MAX_OUTPUT_CHUNK_BYTES == 8192
    # 4 096 characters of a 2-byte letter are 8 192 bytes: refused.
    with pytest.raises(ProtocolError):
        validate({**sample_v2("result"), "tail": "ç" * 4096}, "node_to_server", 2)
    validate({**sample_v2("result"), "tail": "ç" * 2048}, "node_to_server", 2)
    with pytest.raises(ProtocolError):
        validate({**sample_v2("output"), "data": "ğ" * 4097}, "node_to_server", 2)
    validate({**sample_v2("output"), "data": "a" * 8192}, "node_to_server", 2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exit_code", 256),
        ("exit_code", -256),
        ("exit_code", 1.0),
        ("output_bytes", -1),
        ("confirm_state", "clicked"),
        ("sandbox_violation", "no"),
        ("refusal_code", "class_not_allowed"),
    ],
)
def test_v2_result_bounds(field, value):
    with pytest.raises(ProtocolError) as exc:
        validate({**sample_v2("result"), field: value}, "node_to_server", 2)
    assert exc.value.code == "schema"


@pytest.mark.parametrize(
    ("message_type", "field", "value"),
    [
        ("output", "seq", 65_536),
        ("output", "seq", -1),
        ("output", "stream", "stdin"),
        ("confirmation", "state", "allowed"),
        ("confirmation", "at", -1),
    ],
)
def test_v2_frame_bounds(message_type, field, value):
    with pytest.raises(ProtocolError):
        validate({**sample_v2(message_type), field: value}, "node_to_server", 2)


# --- validate across versions ---


@pytest.mark.parametrize("message_type", V2_TYPES)
def test_a_v2_sample_of_every_type_validates_in_its_direction_only(message_type):
    message = sample_v2(message_type)
    direction = direction_of(message_type, 2)
    other = "server_to_node" if direction == "node_to_server" else "node_to_server"
    assert validate(message, direction, 2) is message
    with pytest.raises(ProtocolError) as exc:
        validate(message, other, 2)
    assert exc.value.code == "unknown_type"


@pytest.mark.parametrize("message_type", V2_TYPES)
def test_an_extra_key_is_a_v2_schema_error(message_type):
    with pytest.raises(ProtocolError) as exc:
        validate({**sample_v2(message_type), "extra": 1}, direction_of(message_type, 2), 2)
    assert exc.value.code == "schema"


@pytest.mark.parametrize("message_type", V2_TYPES)
def test_a_frame_of_the_other_version_is_a_schema_error(message_type):
    message = sample_v2(message_type)
    direction = direction_of(message_type, 2)
    if message_type in all_types(1):
        with pytest.raises(ProtocolError) as exc:
            validate(message, direction, 1)
        assert exc.value.code == "schema"
        v1 = sample_v1(message_type)
        with pytest.raises(ProtocolError) as exc:
            validate(v1, direction, 2)
        assert exc.value.code == "schema"
    else:  # confirmation / output do not exist in v1
        with pytest.raises(ProtocolError) as exc:
            validate(message, direction, 1)
        assert exc.value.code == "unknown_type"


def test_a_grant_object_is_checked_by_the_v2_schema():
    envelope = gated_envelope()
    validate(envelope, "server_to_node", 2)
    for name in ("approval_id", "sig", "key_id", "nonce"):
        broken = {**envelope, "grant": {**envelope["grant"], name: "x"}}
        with pytest.raises(ProtocolError):
            validate(broken, "server_to_node", 2)
    with pytest.raises(ProtocolError):
        validate({**envelope, "grant": {**envelope["grant"], "extra": 1}}, "server_to_node", 2)
    with pytest.raises(ProtocolError):
        validate({**envelope, "grant": {**envelope["grant"], "class": "confirm"}}, "server_to_node", 2)
    with pytest.raises(ProtocolError):
        validate_sub_schema("grant", {**envelope["grant"], "v": 1})
    # A null grant is schema-valid: presence is a class rule (check 14).
    validate({**envelope, "grant": None}, "server_to_node", 2)
    read = plain_envelope("git.status", {"repo_path": "~/work/acme"}, "read")
    validate(read, "server_to_node", 2)


def test_decode_frame_v2():
    envelope = gated_envelope()
    assert decode_frame(json.dumps(envelope), "server_to_node", 2) == envelope
    with pytest.raises(ProtocolError) as exc:
        decode_frame(json.dumps({"v": 2, "type": "policy.set", "id": _id()}), "server_to_node", 2)
    assert exc.value.code == "unknown_type"


def test_unknown_versions_are_programming_errors():
    with pytest.raises(ValueError):
        validate(sample_v2("bye"), "server_to_node", 3)
    with pytest.raises(ValueError):
        load_schema("bye", 0)


# --- refusal codes ---


def test_v2_refusal_codes_keep_v1_checks_1_to_12_verbatim():
    assert [n for n, _ in NODE_CHECKS_V2] == list(range(1, 25))
    v1_first_twelve = NODE_REFUSAL_CODES[: NODE_REFUSAL_CODES.index("unknown_capability") + 1]
    assert NODE_REFUSAL_CODES_V2[: len(v1_first_twelve)] == v1_first_twelve
    assert "class_not_allowed" not in NODE_REFUSAL_CODES_V2
    assert len(set(NODE_REFUSAL_CODES_V2)) == len(NODE_REFUSAL_CODES_V2)
    assert dict(NODE_CHECKS_V2)[15] == ("unknown_key",)
    assert dict(NODE_CHECKS_V2)[18] == ("ttl_too_long",)
    schema_enum = load_schema("result", 2)["properties"]["refusal_code"]["enum"]
    assert schema_enum == [*NODE_REFUSAL_CODES_V2, None]


def test_the_d5_v2_codes_are_the_briefs():
    by_check = dict(NODE_CHECKS_V2)
    assert by_check[13] == ("class_mismatch",)
    assert by_check[14] == ("grant_unexpected", "grant_missing")
    assert by_check[16] == ("bad_grant_signature",)
    assert by_check[17] == ("grant_mismatch",)
    assert by_check[19] == ("args_hash_mismatch",)
    assert by_check[20] == ("args_schema",)
    assert by_check[21] == ("not_granted",)
    assert by_check[22] == ("not_configured",)
    assert set(by_check[23]) == {"busy_confirming", "owner_denied", "confirm_timeout"}
    assert by_check[24] == ("expired_after_confirm",)


def test_ttl_ceilings():
    assert MAX_GRANT_TTL_SECONDS == 120
    assert messages.MAX_INVOKE_TTL_SECONDS_V2 == {"read": 30, "confirm": 120, "gate": 120}
    assert messages.CONFIRM_WINDOW_SECONDS == 60


# --- negotiation ---


@pytest.mark.parametrize(
    ("offered", "expected"),
    [
        (["friday-node.v2", "friday-node.v1"], (2, "friday-node.v2")),
        (["friday-node.v1", "friday-node.v2"], (2, "friday-node.v2")),
        (["friday-node.v1"], (1, "friday-node.v1")),
        (["friday-node.v2"], (2, "friday-node.v2")),
        (["friday-node.v3", "friday-node.v1"], (1, "friday-node.v1")),
        (["friday-node.v3"], None),
        ([], None),
        (None, None),
        ([2, None, "FRIDAY-NODE.V2"], None),
    ],
)
def test_negotiation_picks_the_highest_common_version(offered, expected):
    assert negotiate_subprotocol(offered) == expected


def test_a_v1_only_server_never_picks_v2():
    assert negotiate_subprotocol(["friday-node.v2", "friday-node.v1"], supported=(1,)) == (
        1,
        "friday-node.v1",
    )
    assert negotiate_subprotocol(["friday-node.v2"], supported=(1,)) is None


def test_the_gateway_path_keeps_its_v1_name():
    assert messages.GATEWAY_PATH == "/ws/node/v1"
    assert messages.SUBPROTOCOLS == {1: "friday-node.v1", 2: "friday-node.v2"}
