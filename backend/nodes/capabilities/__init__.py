"""Server-known capability contracts (Phase 9 brief D7; Phase 10 brief D3).

`v1/<name>.json` holds one read capability each: `name`, `version`,
`class` (always `read`, ADR-014), `data_class` (`firm`/`personal`, the
strictest data class its result can carry), `path_args` (argument names
that are filesystem paths, for D9 workspace resolution),
`workspace_resolution` (`row_cwd`, `path_arg`, `session_lookup` or
`personal_only`), `max_result_bytes`, `args_schema` and `result_schema`.

`v2/<name>.json` (Phase 10 D3) holds the six **action** contracts: the v1
fields plus `label` (the server-side Turkish label `describe_request`
shows), `max_runtime_seconds` (<= 900), `streams_output` and
`confirm_template` (the Turkish sentence the node renders; its `{arg}`
slots are filled **by the node** from its own validated args and must name
real args). Their class is `confirm` or `gate`, and they are invocable only
on a protocol v2 session. `v2/recipes/<name>.json` is the recipe catalogue
of `applescript.recipe.run`: each recipe has its own `params_schema` and
`confirm_template`. A recipe whose params have `repo_path` is a *repo
recipe* (derived from the schema, so the D3 workspace rule cannot drift
from the catalogue). `applescript.recipe.run`'s `args_schema` carries
`x-friday-recipe-catalogue-sha256`, the sha256 of the canonical recipe
catalogue, so the catalogue matching rule below covers every recipe's
params schema too.

The server invokes a node's catalogue entry only when it matches a
contract on **name, version, class and the sha256 of both schemas**
(canonical JSON). A node advertising a wider schema cannot make the server
send arguments outside the server's own contract, and the reverse holds
too.

Amounts are integer micro-USD (`usd_micros`, `total_usd_micros`): canonical
JSON is integers-only, so the Swift node and the server's total
recomputation (D9) agree on rounding by construction. `output_sha256` is
the sha256 of the canonical JSON of `output` (protocol `sha256_hex`).

Pure Python like `nodes.protocol` (no Django), so the reference node can
load the same files.
"""

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType

from nodes.protocol.canonical import canonicalize, sha256_hex
from nodes.protocol.messages import ProtocolError, validate_instance

CONTRACT_DIR = Path(__file__).resolve().parent / "v1"
ACTION_CONTRACT_DIR = Path(__file__).resolve().parent / "v2"
RECIPE_DIR = ACTION_CONTRACT_DIR / "recipes"

RECIPE_CAPABILITY = "applescript.recipe.run"
RECIPE_CATALOGUE_KEYWORD = "x-friday-recipe-catalogue-sha256"
MAX_RUNTIME_SECONDS = 900

DATA_CLASSES = ("firm", "personal")
WORKSPACE_RESOLUTIONS = (
    "row_cwd",
    "path_arg",
    "session_lookup",
    "personal_only",
    # Phase 10 D3: a repo recipe resolves from `params.repo_path` like
    # `path_arg`; every other recipe is personal only.
    "recipe_params",
)
ACTION_CLASSES = ("confirm", "gate")

# Why a catalogue entry is not invocable (shown in the UI, D6/D11). An
# entry with no reason is invocable. Phase 10 adds `protocol_v1` (a confirm
# or gate entry on a v1 session) and `class_mismatch` (an action entry
# advertised with another class).
NOT_INVOCABLE_REASONS = (
    "unknown_capability",
    "protocol_v1",
    "class_not_read",
    "class_mismatch",
    "version_mismatch",
    "args_schema_mismatch",
    "result_schema_mismatch",
    "not_granted",
    "unsupported",
)

_CONTRACT_KEYS = frozenset(
    {
        "name",
        "version",
        "class",
        "data_class",
        "description",
        "path_args",
        "workspace_resolution",
        "max_result_bytes",
        "args_schema",
        "result_schema",
    }
)
_ACTION_CONTRACT_KEYS = _CONTRACT_KEYS | {
    "label",
    "max_runtime_seconds",
    "streams_output",
    "confirm_template",
}
_RECIPE_KEYS = frozenset(
    {"name", "version", "label", "description", "params_schema", "confirm_template"}
)
# The one "printable, no format or invisible characters" class (Phase 10
# D3, review round 1). Every user-visible free string an action contract or
# recipe accepts (`text`, `title`, `repo_path`) is a negated character class
# that must contain exactly this body (after its own C0 exclusion, and for
# paths its shell metacharacters), so the fields cannot drift apart. The
# owner reads these values in the node's confirm sentence: anything they
# cannot see could be typed or used while the owner approves only the
# visible text. Excluded: C1 controls and DEL; every Cf (format) code point
# of Unicode 15 (soft hyphen, Arabic/Syriac marks, bidi, zero-width,
# U+2060-206F, BOM, interlinear annotation, Kaithi/Egyptian/shorthand/
# musical format controls, tag characters); Zl/Zp; Cs (surrogates); Co
# (private use, all planes); noncharacters; blank or invisible glyphs of
# other categories (U+034F CGJ, Hangul fillers U+115F-1160/U+3164/U+FFA0,
# Khmer U+17B4-17B5, Mongolian FVS U+180B-180F, Braille blank U+2800,
# variation selectors U+FE00-FE0F and U+E0100-E01EF); U+FFF0-FFF8; and
# planes 4-16 wholesale (no visible character is assigned there). Scattered
# unassigned (Cn) code points of planes 0-3 stay allowed: macOS renders them
# as visible Last Resort glyphs, and enumerating them (~700 ranges) would
# pin the class to one Unicode version. The class is plain code-point ranges
# (`\uXXXX`/`\UXXXXXXXX`, the same syntax in Python `re` and ICU), so the
# Swift node applies the identical set whatever its Unicode version.
INVISIBLE_CHARACTER_CLASS = (
    r"\x7f-\x9f\xad\u034f\u0600-\u0605\u061c\u06dd\u070f\u0890-\u0891\u08e2"
    r"\u115f-\u1160\u17b4-\u17b5\u180b-\u180f\u200b-\u200f\u2028-\u202e"
    r"\u2060-\u206f\u2800\u3164\ud800-\udfff\ue000-\uf8ff\ufdd0-\ufdef"
    r"\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufffb\ufffe-\uffff"
    r"\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a\U0001fffe-\U0001ffff\U0002fffe-\U0002ffff"
    r"\U0003fffe-\U0003ffff\U00040000-\U0010ffff"
)
_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}")
_SLOT = re.compile(r"\{([a-z][a-z0-9_]{0,31})\}")


def free_string_properties(schema) -> dict:
    """`{name: subschema}` of the free-text string properties of a closed
    object schema: those whose pattern is a negated character class
    (`[^...]`). Enum- and allow-list-patterned strings are not free text."""
    return {
        name: sub
        for name, sub in (schema.get("properties") or {}).items()
        if sub.get("type") == "string" and "[^" in sub.get("pattern", "")
    }


def _check_free_strings(path: Path, schema) -> None:
    for name, sub in free_string_properties(schema).items():
        if INVISIBLE_CHARACTER_CLASS not in sub["pattern"]:
            raise ValueError(
                f"{path.name}: {name} must exclude the shared invisible character class"
            )


def template_slots(template: str) -> tuple:
    """The `{arg}` slot names of a confirm template, in order. Raises
    ValueError for a stray brace (`{`, `}` or `{Bad}`): the node fills only
    well-formed slots, so a malformed template is a contract error."""
    if not isinstance(template, str) or not template or len(template) > 300:
        raise ValueError("confirm_template must be a non-empty string of at most 300 characters")
    if "{" in _SLOT.sub("", template) or "}" in _SLOT.sub("", template):
        raise ValueError("confirm_template has a malformed slot")
    return tuple(_SLOT.findall(template))


@dataclass(frozen=True)
class RecipeContract:
    """One recipe of `applescript.recipe.run` (Phase 10 D3)."""

    name: str
    version: int
    label: str
    description: str
    params_schema: dict = field(repr=False)
    confirm_template: str = ""

    @property
    def repo_recipe(self) -> bool:
        """A repo recipe follows the repo's workspace (D3); every other
        recipe is personal only."""
        return "repo_path" in (self.params_schema.get("properties") or {})

    def document(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "label": self.label,
            "description": self.description,
            "params_schema": json.loads(json.dumps(self.params_schema)),
            "confirm_template": self.confirm_template,
        }

    def validate_params(self, params, *, code="args_contract") -> None:
        validate_instance(self.params_schema, params, code=code)


@dataclass(frozen=True)
class CapabilityContract:
    name: str
    version: int
    capability_class: str
    data_class: str
    description: str
    path_args: tuple
    workspace_resolution: str
    max_result_bytes: int
    args_schema: dict = field(repr=False)
    result_schema: dict = field(repr=False)
    # Phase 10 action contracts (v2/); reads keep the defaults.
    label: str = ""
    max_runtime_seconds: int | None = None
    streams_output: bool = False
    confirm_template: str | None = None
    min_protocol_version: int = 1
    recipes: MappingProxyType = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @property
    def args_schema_sha256(self) -> str:
        return sha256_hex(self.args_schema)

    @property
    def result_schema_sha256(self) -> str:
        return sha256_hex(self.result_schema)

    @property
    def is_action(self) -> bool:
        return self.capability_class in ACTION_CLASSES

    def catalogue_entry(self, availability="granted") -> dict:
        """The `hello` catalogue entry a conforming node advertises."""
        return {
            "name": self.name,
            "version": self.version,
            "class": self.capability_class,
            "args_schema": json.loads(json.dumps(self.args_schema)),
            "result_schema": json.loads(json.dumps(self.result_schema)),
            "availability": availability,
        }

    def validate_args(self, args, *, code="args_contract") -> None:
        """Raise ProtocolError(code) if `args` is outside this contract's
        `args_schema` (for `applescript.recipe.run`, also outside the named
        recipe's own `params_schema`)."""
        validate_instance(self.args_schema, args, code=code)
        if self.recipes:
            recipe = self.recipes.get(args.get("recipe"))
            if recipe is None:  # unreachable while the enum equals the catalogue
                raise ProtocolError(code, "unknown recipe")
            recipe.validate_params(args.get("params"), code=code)

    def recipe_for(self, args):
        """The RecipeContract named by valid `args`, or None."""
        return self.recipes.get(args.get("recipe")) if self.recipes else None

    def validate_result(self, output) -> None:
        """Raise ProtocolError("result_too_large") when the canonical output
        exceeds `max_result_bytes`, or ProtocolError("result_schema") when
        it does not match `result_schema`."""
        try:
            size = len(canonicalize(output))
        except ValueError as exc:
            raise ProtocolError("result_schema", "output is not canonical JSON") from exc
        if size > self.max_result_bytes:
            raise ProtocolError("result_too_large")
        validate_instance(self.result_schema, output, code="result_schema")


def _common(path: Path, body: dict):
    if body["data_class"] not in DATA_CLASSES:
        raise ValueError(f"{path.name}: bad data_class")
    if body["workspace_resolution"] not in WORKSPACE_RESOLUTIONS:
        raise ValueError(f"{path.name}: bad workspace_resolution")
    if path.name != f"{body['name']}.json":
        raise ValueError(f"{path.name}: file name must equal the capability name")
    if not _NAME.fullmatch(body["name"]):
        raise ValueError(f"{path.name}: bad capability name")


def _load(path: Path) -> CapabilityContract:
    body = json.loads(path.read_text(encoding="utf-8"))
    if set(body) != _CONTRACT_KEYS:
        raise ValueError(f"{path.name}: unexpected contract keys")
    if body["class"] != "read":
        raise ValueError(f"{path.name}: v1 contracts are read-only")
    _common(path, body)
    return CapabilityContract(
        name=body["name"],
        version=body["version"],
        capability_class=body["class"],
        data_class=body["data_class"],
        description=body["description"],
        path_args=tuple(body["path_args"]),
        workspace_resolution=body["workspace_resolution"],
        max_result_bytes=body["max_result_bytes"],
        args_schema=body["args_schema"],
        result_schema=body["result_schema"],
    )


def _load_recipe(path: Path) -> RecipeContract:
    body = json.loads(path.read_text(encoding="utf-8"))
    if set(body) != _RECIPE_KEYS:
        raise ValueError(f"{path.name}: unexpected recipe keys")
    if path.name != f"{body['name']}.json" or not _NAME.fullmatch(body["name"]):
        raise ValueError(f"{path.name}: file name must equal the recipe name")
    schema = body["params_schema"]
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError(f"{path.name}: params_schema must be a closed object")
    _check_free_strings(path, schema)
    slots = template_slots(body["confirm_template"])
    if not set(slots) <= set(schema.get("properties") or {}):
        raise ValueError(f"{path.name}: confirm_template names a param that does not exist")
    if not isinstance(body["label"], str) or not body["label"]:
        raise ValueError(f"{path.name}: empty label")
    return RecipeContract(
        name=body["name"],
        version=body["version"],
        label=body["label"],
        description=body["description"],
        params_schema=schema,
        confirm_template=body["confirm_template"],
    )


@cache
def load_recipes():
    """`{name: RecipeContract}` for the v1 recipe catalogue (D3)."""
    recipes = {}
    for path in sorted(RECIPE_DIR.glob("*.json")):
        recipe = _load_recipe(path)
        recipes[recipe.name] = recipe
    return MappingProxyType(recipes)


def recipe_catalogue_sha256(recipes=None) -> str:
    """sha256 of the canonical JSON of the recipe documents, by name."""
    recipes = load_recipes() if recipes is None else recipes
    return sha256_hex([recipes[name].document() for name in sorted(recipes)])


def _load_action(path: Path) -> CapabilityContract:
    body = json.loads(path.read_text(encoding="utf-8"))
    if set(body) != _ACTION_CONTRACT_KEYS:
        raise ValueError(f"{path.name}: unexpected contract keys")
    if body["class"] not in ACTION_CLASSES:
        raise ValueError(f"{path.name}: v2 action contracts are confirm or gate")
    _common(path, body)
    runtime = body["max_runtime_seconds"]
    if isinstance(runtime, bool) or not isinstance(runtime, int):
        raise ValueError(f"{path.name}: max_runtime_seconds must be an integer")
    if not 1 <= runtime <= MAX_RUNTIME_SECONDS:
        raise ValueError(f"{path.name}: max_runtime_seconds outside 1..{MAX_RUNTIME_SECONDS}")
    if not isinstance(body["streams_output"], bool):
        raise ValueError(f"{path.name}: streams_output must be a boolean")
    if not isinstance(body["label"], str) or not body["label"]:
        raise ValueError(f"{path.name}: empty label")
    _check_free_strings(path, body["args_schema"])
    args_properties = set(body["args_schema"].get("properties") or {})
    if not set(template_slots(body["confirm_template"])) <= args_properties:
        raise ValueError(f"{path.name}: confirm_template names an arg that does not exist")
    if not set(body["path_args"]) <= args_properties:
        raise ValueError(f"{path.name}: path_args names an arg that does not exist")
    recipes = MappingProxyType({})
    if body["name"] == RECIPE_CAPABILITY:
        recipes = load_recipes()
        recipe_enum = body["args_schema"]["properties"]["recipe"]["enum"]
        if sorted(recipe_enum) != sorted(recipes):
            raise ValueError(f"{path.name}: the recipe enum must equal the recipe catalogue")
        if body["args_schema"].get(RECIPE_CATALOGUE_KEYWORD) != recipe_catalogue_sha256(recipes):
            raise ValueError(f"{path.name}: stale {RECIPE_CATALOGUE_KEYWORD}")
    elif RECIPE_CATALOGUE_KEYWORD in body["args_schema"]:
        raise ValueError(f"{path.name}: only {RECIPE_CAPABILITY} carries a recipe catalogue")
    return CapabilityContract(
        name=body["name"],
        version=body["version"],
        capability_class=body["class"],
        data_class=body["data_class"],
        description=body["description"],
        path_args=tuple(body["path_args"]),
        workspace_resolution=body["workspace_resolution"],
        max_result_bytes=body["max_result_bytes"],
        args_schema=body["args_schema"],
        result_schema=body["result_schema"],
        label=body["label"],
        max_runtime_seconds=runtime,
        streams_output=body["streams_output"],
        confirm_template=body["confirm_template"],
        min_protocol_version=2,
        recipes=recipes,
    )


@cache
def load_contracts():
    """`{name: CapabilityContract}` for every v1 **read** contract file."""
    contracts = {}
    for path in sorted(CONTRACT_DIR.glob("*.json")):
        contract = _load(path)
        contracts[contract.name] = contract
    return MappingProxyType(contracts)


@cache
def load_action_contracts():
    """`{name: CapabilityContract}` for every v2 action contract (confirm
    and gate classes, Phase 10 D3)."""
    contracts = {}
    for path in sorted(ACTION_CONTRACT_DIR.glob("*.json")):
        contract = _load_action(path)
        contracts[contract.name] = contract
    if set(contracts) & set(load_contracts()):
        raise ValueError("an action contract reuses a read capability name")
    return MappingProxyType(contracts)


@cache
def load_all_contracts():
    """Read and action contracts together, by name."""
    return MappingProxyType({**load_contracts(), **load_action_contracts()})


def get_contract(name):
    """A **read** contract (the only kind `sign_read_invoke` may sign)."""
    return load_contracts().get(name)


def get_action_contract(name):
    """A confirm- or gate-class contract (Phase 10)."""
    return load_action_contracts().get(name)


def get_any_contract(name):
    return load_all_contracts().get(name)


def check_gate_request(capability, capability_version, args, *, code="args_contract"):
    """The server-side contract check a caller must run before
    `nodes.protocol.grants.build_gated_invoke` signs a grant (review round
    1): `capability` is a known gate-class action contract of exactly
    `capability_version` and `args` match its `args_schema` (and, for a
    recipe, the recipe's own params). Raises ProtocolError(code); returns
    the contract."""
    contract = get_action_contract(capability)
    if contract is None:
        raise ProtocolError(code, "unknown capability")
    if contract.capability_class != "gate":
        raise ProtocolError(code, "not a gate-class capability")
    if isinstance(capability_version, bool) or capability_version != contract.version:
        raise ProtocolError(code, "capability version mismatch")
    contract.validate_args(args, code=code)
    return contract


def _schema_hash(schema):
    try:
        return sha256_hex(schema)
    except ValueError:  # CanonicalizationError: never equal to a contract hash
        return None


def check_catalogue_entry(entry, protocol_version=1) -> str | None:
    """Compare one (schema-valid) `hello` catalogue entry with the server's
    contracts for a session of `protocol_version`. Returns None when it is
    invocable, else its NOT_INVOCABLE_REASONS reason.

    Order: unknown name; an action contract on a v1 session
    (`protocol_v1`, whatever class the entry claims: a v1 session gets read
    invokes only, Phase 10 D1); the class (`class_not_read` for a read
    contract, `class_mismatch` for an action contract); version; both
    schema hashes. Availability is checked last: a matching entry the owner
    has not granted on the Mac is recorded, never invoked."""
    contract = get_any_contract(entry.get("name"))
    if contract is None:
        return "unknown_capability"
    if contract.min_protocol_version > protocol_version:
        return "protocol_v1"
    if entry.get("class") != contract.capability_class:
        return "class_mismatch" if contract.is_action else "class_not_read"
    if entry.get("version") != contract.version:
        return "version_mismatch"
    if _schema_hash(entry.get("args_schema")) != contract.args_schema_sha256:
        return "args_schema_mismatch"
    if _schema_hash(entry.get("result_schema")) != contract.result_schema_sha256:
        return "result_schema_mismatch"
    if entry.get("availability") == "not_granted":
        return "not_granted"
    if entry.get("availability") != "granted":
        return "unsupported"
    return None
