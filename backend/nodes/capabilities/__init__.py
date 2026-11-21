"""Server-known capability contracts (Phase 9 brief D7).

`v1/<name>.json` holds one read capability each: `name`, `version`,
`class` (always `read` in v1, ADR-014), `data_class` (`firm`/`personal`,
the strictest data class its result can carry), `path_args` (argument names
that are filesystem paths, for D9 workspace resolution),
`workspace_resolution` (`row_cwd`, `path_arg`, `session_lookup` or
`personal_only`), `max_result_bytes`, `args_schema` and `result_schema`.

The server invokes a node's catalogue entry only when it matches a
contract on **name, version and the sha256 of both schemas** (canonical
JSON). A node advertising a wider schema cannot make the server send
arguments outside the server's own contract, and the reverse holds too.

Amounts are integer micro-USD (`usd_micros`, `total_usd_micros`): canonical
JSON is integers-only, so the Swift node and the server's total
recomputation (D9) agree on rounding by construction. `output_sha256` is
the sha256 of the canonical JSON of `output` (protocol `sha256_hex`).

Pure Python like `nodes.protocol` (no Django), so the reference node can
load the same files.
"""

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType

from nodes.protocol.canonical import canonicalize, sha256_hex
from nodes.protocol.messages import ProtocolError, validate_instance

CONTRACT_DIR = Path(__file__).resolve().parent / "v1"

DATA_CLASSES = ("firm", "personal")
WORKSPACE_RESOLUTIONS = ("row_cwd", "path_arg", "session_lookup", "personal_only")

# Why a catalogue entry is not invocable (shown in the UI, D6/D11). An
# entry with no reason is invocable.
NOT_INVOCABLE_REASONS = (
    "unknown_capability",
    "version_mismatch",
    "args_schema_mismatch",
    "result_schema_mismatch",
    "class_not_read",
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

    @property
    def args_schema_sha256(self) -> str:
        return sha256_hex(self.args_schema)

    @property
    def result_schema_sha256(self) -> str:
        return sha256_hex(self.result_schema)

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

    def validate_args(self, args) -> None:
        """Raise ProtocolError("args_contract") if `args` is outside this
        contract's `args_schema`."""
        validate_instance(self.args_schema, args, code="args_contract")

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


def _load(path: Path) -> CapabilityContract:
    body = json.loads(path.read_text(encoding="utf-8"))
    if set(body) != _CONTRACT_KEYS:
        raise ValueError(f"{path.name}: unexpected contract keys")
    if body["class"] != "read":
        raise ValueError(f"{path.name}: v1 contracts are read-only")
    if body["data_class"] not in DATA_CLASSES:
        raise ValueError(f"{path.name}: bad data_class")
    if body["workspace_resolution"] not in WORKSPACE_RESOLUTIONS:
        raise ValueError(f"{path.name}: bad workspace_resolution")
    if path.name != f"{body['name']}.json":
        raise ValueError(f"{path.name}: file name must equal the capability name")
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


@cache
def load_contracts():
    """`{name: CapabilityContract}` for every v1 contract file."""
    contracts = {}
    for path in sorted(CONTRACT_DIR.glob("*.json")):
        contract = _load(path)
        contracts[contract.name] = contract
    return MappingProxyType(contracts)


def get_contract(name):
    return load_contracts().get(name)


def _schema_hash(schema):
    try:
        return sha256_hex(schema)
    except ValueError:  # CanonicalizationError: never equal to a contract hash
        return None


def check_catalogue_entry(entry) -> str | None:
    """Compare one (schema-valid) `hello` catalogue entry with the server's
    contracts. Returns None when it is invocable, else its
    NOT_INVOCABLE_REASONS reason. Availability is checked last: a matching
    entry the owner has not granted on the Mac is recorded, never invoked."""
    contract = get_contract(entry.get("name"))
    if contract is None:
        return "unknown_capability"
    if entry.get("class") != "read":
        return "class_not_read"
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
