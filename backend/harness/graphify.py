"""Graphify `graph.json` -> the memory code-graph snapshot (Phase 8B branch
5, brief D10 "Import" steps 1-6).

`graph.json` is container output, so it is **data**: it is parsed, never
executed, and checked before anything reaches memory.

1. **Validate** against `graphify_schema_v1.json` (the shape observed from
   the pinned `graphifyy==0.9.103`; bounded strings and arrays). An unknown
   top-level key or a missing required field is a refusal. The validator
   below implements exactly the JSON-Schema keywords that file uses -- the
   backend has no direct `jsonschema` dependency.
2. **Filter**: only `_origin == "ast"` nodes and links survive, and only
   links with `confidence == "EXTRACTED"` (Graphify's `INFERRED` guesses
   are dropped).
3. **Map** the kinds: a file node -> `code_module` named by its
   repo-relative path; a class -> `code_class`; any other callable ->
   `code_function`. Relations: `contains` and `method` -> `contains`;
   `imports` and `imports_from` -> `imports`; `calls`; `inherits`. Every
   other node (docstring rationale, document headings, config keys,
   package manifests) and relation (`references`, `uses`, ...) is dropped.
4. Labels are scrubbed and fence-neutralised by the memory port that
   stores them (`memory.graph.code_graph.clean_name`).
5. **External targets** (`external: true`, e.g. `os`) are dropped unless
   a module imports them at the top level: that becomes a `tool` entity
   named after the package as Graphify labels it.
6. **Caps**: at most 20 000 entities and 60 000 facts; over a cap the run
   fails and nothing is replaced.

The temporal replace itself (step 7) and the audit row (step 8) are
memory's: `memory.graph.ports.import_code_graph`.
"""

import json
from pathlib import Path

from memory.graph.code_graph import MAX_ENTITIES, MAX_FACTS

SCHEMA_PATH = Path(__file__).with_name("graphify_schema_v1.json")
MAX_GRAPH_BYTES = 20 * 1024 * 1024
# `graphify_run.sh` runs `graphify update repo` from `/work`, so every
# `source_file` starts with this.
SOURCE_ROOT = "repo/"

RELATION_PREDICATES = {
    "contains": "contains",
    "method": "contains",
    "imports": "imports",
    "imports_from": "imports",
    "calls": "calls",
    "inherits": "inherits",
}

CODE_MODULE = "code_module"
CODE_CLASS = "code_class"
CODE_FUNCTION = "code_function"
TOOL = "tool"


class GraphifyRefused(ValueError):
    """The graph cannot be imported. `reason` is a stable key; the message
    names a JSON path at most, never a label or value."""

    def __init__(self, reason, message=""):
        super().__init__(message or reason)
        self.reason = reason


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# --- the JSON-Schema subset ---------------------------------------------------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "null": lambda v: v is None,
}
SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
    }
)


def _fail(path, what):
    raise GraphifyRefused("schema", f"graph.json {what} at {path or '$'}")


def validate(instance, schema, path="$"):
    """Raise `GraphifyRefused("schema")` unless `instance` matches."""
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise ValueError(f"unsupported schema keywords: {sorted(unknown)}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](instance) for t in types):
            _fail(path, "has the wrong type")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            _fail(path, "is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            _fail(path, "is too long")
    if "minimum" in schema and _TYPES["number"](instance) and instance < schema["minimum"]:
        _fail(path, "is below the minimum")
    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            _fail(path, "has too many items")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                _fail(path, f"misses the required key {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties", True) is False:
            extra = set(instance) - set(properties)
            if extra:
                _fail(path, "has an unknown key")
        for key, sub in properties.items():
            if key in instance:
                validate(instance[key], sub, f"{path}.{key}")


# --- mapping ------------------------------------------------------------------


def _relative(source_file):
    if isinstance(source_file, str) and source_file.startswith(SOURCE_ROOT):
        return source_file[len(SOURCE_ROOT) :]
    return ""


def _classify(node):
    """`(kind, name)` for a node the snapshot keeps, else `None`."""
    if node.get("_origin") != "ast" or node.get("file_type") != "code":
        return None
    label = (node.get("label") or "").strip()
    relative = _relative(node.get("source_file"))
    if not label or not relative:
        return None
    if node.get("_callable_class") is True:
        return CODE_CLASS, f"{label} ({relative})"
    if node.get("_callable") is True:
        return CODE_FUNCTION, f"{label} ({relative})"
    if node.get("type") in (None, "") and (relative == label or relative.endswith("/" + label)):
        return CODE_MODULE, relative
    return None


def map_graph(raw):
    """Validated `graph.json` (a parsed dict) -> `(snapshot, stats)`.
    `snapshot` is the `memory.graph.ports.import_code_graph` shape; `stats`
    counts what was kept and why the rest was dropped. Raises
    `GraphifyRefused`."""
    validate(raw, load_schema())
    stats = {
        "nodes_seen": len(raw["nodes"]),
        "links_seen": len(raw["links"]),
        "nodes_dropped": 0,
        "links_not_ast": 0,
        "links_inferred": 0,
        "links_other_relation": 0,
        "links_external_dropped": 0,
        "links_endpoint_dropped": 0,
        "links_duplicate": 0,
    }
    kept, externals, seen_ids = {}, {}, set()
    for node in raw["nodes"]:
        node_id = node["id"]
        if node_id in seen_ids:
            raise GraphifyRefused("duplicate_node", "graph.json repeats a node id")
        seen_ids.add(node_id)
        if node.get("external") is True and "_origin" not in node:
            label = (node.get("label") or "").strip()
            if label:
                externals[node_id] = label
            continue
        classified = _classify(node)
        if classified is None:
            stats["nodes_dropped"] += 1
            continue
        kept[node_id] = classified

    entities = {
        node_id: {"key": node_id, "kind": kind, "name": name}
        for node_id, (kind, name) in kept.items()
    }
    facts, seen = [], set()
    for link in raw["links"]:
        if link.get("_origin") != "ast":
            stats["links_not_ast"] += 1
            continue
        if link.get("confidence") != "EXTRACTED":
            stats["links_inferred"] += 1
            continue
        predicate = RELATION_PREDICATES.get(link.get("relation"))
        if predicate is None:
            stats["links_other_relation"] += 1
            continue
        source, target = link["source"], link["target"]
        if source not in kept:
            stats["links_endpoint_dropped"] += 1
            continue
        if target in externals:
            # A module's own top-level import of an external package -> a
            # `tool` entity; any other edge to an external is dropped.
            if predicate != "imports" or kept[source][0] != CODE_MODULE:
                stats["links_external_dropped"] += 1
                continue
            tool_key = f"tool:{externals[target]}"
            entities.setdefault(
                tool_key, {"key": tool_key, "kind": TOOL, "name": externals[target]}
            )
            target = tool_key
        elif target not in kept:
            stats["links_endpoint_dropped"] += 1
            continue
        if source == target:
            stats["links_endpoint_dropped"] += 1
            continue
        triple = (source, predicate, target)
        if triple in seen:
            stats["links_duplicate"] += 1
            continue
        seen.add(triple)
        facts.append({"subject": source, "predicate": predicate, "object": target})

    if len(entities) > MAX_ENTITIES:
        raise GraphifyRefused("entity_cap", f"more than {MAX_ENTITIES} entities")
    if len(facts) > MAX_FACTS:
        raise GraphifyRefused("fact_cap", f"more than {MAX_FACTS} facts")
    stats["entities"] = len(entities)
    stats["facts"] = len(facts)
    return {"entities": list(entities.values()), "facts": facts}, stats


def parse_graph_bytes(data):
    """`graph.json` bytes (already size-checked by `harness.safe_read`) ->
    the parsed object. Never evaluates anything."""
    if len(data) > MAX_GRAPH_BYTES:
        raise GraphifyRefused("oversize", "graph.json is over the size cap")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise GraphifyRefused("invalid_json", "graph.json is not valid UTF-8 JSON") from exc
