"""SKILL.md instruction skills: the strict parser and the bundle reader
(Phase 12 D13, ADR-016 decision 4).

An instruction skill is **text**: a front-matter block and a Markdown
body. It grants no tool, no permission and no host execution; this module
refuses everything that is shaped like one.

**Front matter.** A hand-written parser for a YAML *subset* -- no YAML
library is a dependency, and a full loader is an attack surface (tags,
aliases, billion laughs) for a five-key header:

- the file starts with a `---` line; the block ends at the next `---`;
- one `key: value` per line; values are plain or double-quoted scalars
  (`\\"` and `\\\\` are the only escapes); full-line `#` comments are
  skipped;
- `metadata:` with an empty value opens a flat map of indented
  `key: value` lines (spaces only, at most `MAX_METADATA_KEYS`);
- refused: anchors (`&`), aliases (`*`), tags (`!`), flow collections
  (`[` / `{`), block scalars (`|` / `>`), directives (`%`), a document
  end marker (`...`), multi-line scalars, a plain value carrying `: ` or
  ` #`, duplicate keys, and a second document.

Allowed keys: `name` (`^[a-z][a-z0-9-]{1,63}$`, required), `description`
(<= 1024 chars, required), `license` (<= 100 chars), `metadata`. A
permission-shaped key (`allowed-tools`, `tools`, `permissions`, `allow`,
`auto_approve`, `trust`, ...; also inside `metadata`) is
`permission_key_refused`; any other key is `unknown_key`.

**Body.** Plain text <= `SKILLS_BODY_MAX_CHARS` after invisible
formatting (format characters such as zero-width and bidi controls, and
control characters other than newline and tab) is stripped.

**Bundles.** An upload is one `SKILL.md` or a `.zip` holding `SKILL.md`
and at most 10 `references/*.md` files (<= 64 KB each). Limits are
checked from the zip's central directory **before any entry is read**:
upload size, entry count, declared total uncompressed size and a
per-entry compression ratio. Entries are then read into memory (never
written to disk) with a hard cap at their declared size. Any script,
binary, dotfile, symlink, nested archive, absolute or `..` path refuses
the whole upload. Nothing here executes, imports or writes anything.

Errors are `SkillParseError` / `SkillBundleError` with a closed `code`;
the messages never echo the refused content.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field

from django.conf import settings

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_METADATA_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_REFERENCE_RE = re.compile(r"^references/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.md$")

ALLOWED_KEYS = ("name", "description", "license", "metadata")
# Permission-shaped keys (compared case-insensitively, `-` and `_` equal).
PERMISSION_KEYS = frozenset(
    {
        "allowed-tools",
        "allowed_tools",
        "allowedtools",
        "tools",
        "tool",
        "permissions",
        "permission",
        "allow",
        "allowed",
        "auto_approve",
        "auto-approve",
        "autoapprove",
        "trust",
        "trusted",
        "approve",
        "approval",
        "approvals",
        "capabilities",
        "scopes",
        "exec",
        "execute",
        "run",
        "commands",
        "hosts",
    }
)

MAX_DESCRIPTION_CHARS = 1024
MAX_LICENSE_CHARS = 100
MAX_METADATA_KEYS = 10
MAX_METADATA_VALUE_CHARS = 256
MAX_REFERENCES = 10
MAX_REFERENCE_BYTES = 64 * 1024
# `SKILL.md`, the `references/` directory entry and 10 files.
MAX_BUNDLE_ENTRIES = 12
MAX_BUNDLE_UNCOMPRESSED_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 20
# Below this declared size the ratio is not meaningful (a short Markdown
# file of repeated words compresses well and can do no harm at that size).
RATIO_FLOOR_BYTES = 4096

# Extensions that name something executable or binary: their presence is
# host execution, not a skill.
_EXECUTABLE_SUFFIXES = (
    ".py",
    ".pyc",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".rb",
    ".pl",
    ".php",
    ".ps1",
    ".bat",
    ".cmd",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".jar",
    ".class",
    ".wasm",
    ".app",
    ".command",
    ".applescript",
    ".scpt",
    ".lua",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".swift",
)
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".whl", ".egg")

_REFUSED_VALUE_START = ("&", "*", "!", "[", "{", "|", ">", "%", "@", "`")


class SkillParseError(ValueError):
    """A refused SKILL.md. `code` is a closed token; the message never
    quotes the refused content."""

    def __init__(self, code, message=""):
        self.code = code
        super().__init__(message or code)


class SkillBundleError(ValueError):
    """A refused upload: `bundle_refused`, `bundle_too_large` or
    `host_execution_refused`. Nothing from it is stored."""

    def __init__(self, code, message=""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    license: str = ""
    metadata: dict = field(default_factory=dict)
    body: str = ""
    references: dict = field(default_factory=dict)

    @property
    def front_matter(self) -> dict:
        front = {"name": self.name, "description": self.description}
        if self.license:
            front["license"] = self.license
        if self.metadata:
            front["metadata"] = dict(self.metadata)
        return front

    @property
    def content_sha256(self) -> str:
        return content_sha256(self.front_matter, self.body, self.references)

    def render(self) -> str:
        """The canonical SKILL.md text (what an editor shows)."""
        return render_skill_md(self.front_matter, self.body)


def content_sha256(front_matter: dict, body: str, references: dict) -> str:
    """The version's identity: canonical JSON of front matter, body and
    references. Approval is by this hash (D14)."""
    canonical = json.dumps(
        {"front_matter": front_matter, "body": body, "references": references or {}},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_skill_md(front_matter: dict, body: str) -> str:
    lines = ["---"]
    for key in ("name", "description", "license"):
        if front_matter.get(key):
            lines.append(f"{key}: {_quote(str(front_matter[key]))}")
    metadata = front_matter.get("metadata") or {}
    if metadata:
        lines.append("metadata:")
        for key in sorted(metadata):
            lines.append(f"  {key}: {_quote(str(metadata[key]))}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body + ("\n" if body and not body.endswith("\n") else "")


def strip_invisible(text: str) -> str:
    """NFC, CRLF to LF, and every format character (zero-width, bidi
    controls, BOM) and control character other than newline and tab
    removed."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    kept = []
    for char in text:
        if char in ("\n", "\t"):
            kept.append(char)
            continue
        category = unicodedata.category(char)
        if category in ("Cf", "Cc", "Co", "Cs", "Cn") or category in ("Zl", "Zp"):
            continue
        kept.append(char)
    return "".join(kept)


def _is_permission_key(key: str) -> bool:
    folded = key.strip().lower()
    return folded in PERMISSION_KEYS or folded.replace("-", "_") in PERMISSION_KEYS


def _parse_scalar(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise SkillParseError("invalid_value", "A value is empty.")
    if value.startswith('"'):
        out = []
        index = 1
        closed = False
        while index < len(value):
            char = value[index]
            if char == "\\":
                if index + 1 >= len(value) or value[index + 1] not in ('"', "\\"):
                    raise SkillParseError("yaml_feature_refused", "Unsupported escape.")
                out.append(value[index + 1])
                index += 2
                continue
            if char == '"':
                closed = True
                index += 1
                break
            out.append(char)
            index += 1
        if not closed or value[index:].strip():
            raise SkillParseError("invalid_value", "A quoted value is not closed on its line.")
        return "".join(out)
    if value.startswith("'"):
        raise SkillParseError("yaml_feature_refused", "Single-quoted scalars are not supported.")
    if value.startswith(_REFUSED_VALUE_START) or value.startswith("- "):
        raise SkillParseError("yaml_feature_refused", "YAML features are not supported.")
    if ": " in value or " #" in value or value.endswith(":"):
        raise SkillParseError("yaml_feature_refused", "Quote a value that holds ': ' or ' #'.")
    return value


def _split_key(line: str):
    if ":" not in line:
        raise SkillParseError("invalid_line", "A front-matter line is not 'key: value'.")
    key, _, rest = line.partition(":")
    if rest and not rest.startswith(" "):
        raise SkillParseError("invalid_line", "A front-matter line is not 'key: value'.")
    return key, rest


def _check_key_shape(key: str) -> None:
    if key.startswith(_REFUSED_VALUE_START) or key.startswith(("?", "-", '"', "'")):
        raise SkillParseError("yaml_feature_refused", "YAML features are not supported.")


def parse_front_matter(lines: list[str]) -> dict:
    result: dict = {}
    metadata_open = False
    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise SkillParseError("yaml_feature_refused", "Tabs are not allowed for indentation.")
        stripped = raw_line.strip()
        if stripped in ("...", "---") or stripped.startswith("%"):
            raise SkillParseError("second_document", "Only one front-matter document.")
        indented = raw_line[:1] == " "
        if indented:
            if not metadata_open:
                raise SkillParseError("yaml_feature_refused", "Multi-line values are refused.")
            key, rest = _split_key(stripped)
            _check_key_shape(key)
            if _is_permission_key(key):
                raise SkillParseError("permission_key_refused", "Permission keys are refused.")
            if not _METADATA_KEY_RE.match(key):
                raise SkillParseError("metadata_invalid", "A metadata key is malformed.")
            metadata = result["metadata"]
            if key in metadata:
                raise SkillParseError("duplicate_key", "A key appears twice.")
            if len(metadata) >= MAX_METADATA_KEYS:
                raise SkillParseError("metadata_invalid", "Too many metadata keys.")
            value = _parse_scalar(rest)
            if len(value) > MAX_METADATA_VALUE_CHARS:
                raise SkillParseError("metadata_invalid", "A metadata value is too long.")
            metadata[key] = value
            continue
        metadata_open = False
        key, rest = _split_key(stripped)
        _check_key_shape(key)
        if not _KEY_RE.match(key):
            raise SkillParseError("invalid_line", "A front-matter key is malformed.")
        if _is_permission_key(key):
            raise SkillParseError("permission_key_refused", "Permission keys are refused.")
        if key not in ALLOWED_KEYS:
            raise SkillParseError("unknown_key", "Unknown front-matter key.")
        if key in result:
            raise SkillParseError("duplicate_key", "A key appears twice.")
        if key == "metadata":
            if rest.strip():
                raise SkillParseError("yaml_feature_refused", "metadata is an indented map.")
            result["metadata"] = {}
            metadata_open = True
            continue
        result[key] = _parse_scalar(rest)
    return result


def parse_skill_md(text, *, references=None) -> ParsedSkill:
    """Parse and validate one SKILL.md; raises `SkillParseError`."""
    if not isinstance(text, str):
        raise SkillParseError("encoding", "SKILL.md is not text.")
    text = strip_invisible(text)
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("missing_front_matter", "SKILL.md starts with a '---' block.")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        raise SkillParseError("missing_front_matter", "The front matter is not closed.") from None
    front = parse_front_matter(lines[1:end])
    body = "\n".join(lines[end + 1 :]).strip("\n")
    first_body_line = next((line.strip() for line in body.split("\n") if line.strip()), "")
    if first_body_line == "---":
        raise SkillParseError("second_document", "Only one front-matter document.")

    name = front.get("name", "")
    if not name:
        raise SkillParseError("missing_name", "name is required.")
    if not NAME_RE.match(name):
        raise SkillParseError("invalid_name", "name is lower-case letters, digits and '-'.")
    description = " ".join(front.get("description", "").split())
    if not description:
        raise SkillParseError("missing_description", "description is required.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillParseError("description_too_long", "description is too long.")
    license_text = front.get("license", "")
    if len(license_text) > MAX_LICENSE_CHARS:
        raise SkillParseError("license_too_long", "license is too long.")
    if not body.strip():
        raise SkillParseError("body_empty", "The body is empty.")
    if len(body) > int(settings.SKILLS_BODY_MAX_CHARS):
        raise SkillParseError("body_too_large", "The body is too long.")
    clean_references = {}
    for path, content in sorted((references or {}).items()):
        if not _REFERENCE_RE.match(path):
            raise SkillParseError("invalid_reference", "A reference path is malformed.")
        clean = strip_invisible(content)
        if len(clean.encode("utf-8")) > MAX_REFERENCE_BYTES:
            raise SkillParseError("reference_too_large", "A reference is too large.")
        clean_references[path] = clean
    if len(clean_references) > MAX_REFERENCES:
        raise SkillParseError("too_many_references", "Too many references.")
    return ParsedSkill(
        name=name,
        description=description,
        license=license_text,
        metadata=dict(front.get("metadata") or {}),
        body=body,
        references=clean_references,
    )


# --- bundles -------------------------------------------------------------------


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SkillBundleError("bundle_refused", "The file is not UTF-8 text.") from None


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data


def _entry_kind(name: str) -> str:
    """'skill', 'references_dir', 'reference', or raise."""
    if not name or "\\" in name or "\x00" in name:
        raise SkillBundleError("bundle_refused", "A path is malformed.")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise SkillBundleError("bundle_refused", "Absolute paths are refused.")
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SkillBundleError("bundle_refused", "Relative path components are refused.")
    if any(part.startswith(".") for part in parts):
        raise SkillBundleError("bundle_refused", "Dotfiles are refused.")
    lowered = name.lower()
    if lowered.endswith(_EXECUTABLE_SUFFIXES):
        raise SkillBundleError("host_execution_refused", "Scripts and binaries are refused.")
    if lowered.endswith(_ARCHIVE_SUFFIXES):
        raise SkillBundleError("bundle_refused", "Nested archives are refused.")
    if name == "SKILL.md":
        return "skill"
    if name == "references/":
        return "references_dir"
    if _REFERENCE_RE.match(name):
        return "reference"
    raise SkillBundleError("bundle_refused", "Only SKILL.md and references/*.md are allowed.")


def _read_capped(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Stream one entry with a hard cap at its declared size: a header that
    under-declares stops the read (refused), it never reads past the cap."""
    declared = info.file_size
    chunks = []
    total = 0
    try:
        with archive.open(info) as handle:
            while True:
                chunk = handle.read(min(8192, declared - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > declared:
                    raise SkillBundleError("bundle_too_large", "An entry is larger than declared.")
                chunks.append(chunk)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, RuntimeError) as exc:
        raise SkillBundleError("bundle_too_large", "An entry does not match its header.") from exc
    except NotImplementedError as exc:
        raise SkillBundleError("bundle_refused", "Unsupported compression.") from exc
    return b"".join(chunks)


def read_bundle(data: bytes, *, filename: str = "SKILL.md") -> ParsedSkill:
    """One upload (a SKILL.md or a zip) to a `ParsedSkill`; raises
    `SkillBundleError` or `SkillParseError`. In memory only."""
    if not isinstance(data, bytes | bytearray):
        raise SkillBundleError("bundle_refused", "The upload is not a file.")
    max_bytes = int(settings.SKILLS_BUNDLE_MAX_BYTES)
    if len(data) > max_bytes:
        raise SkillBundleError("bundle_too_large", "The upload is too large.")
    lowered = (filename or "").lower()
    is_zip = bytes(data[:4]) == b"PK\x03\x04" or lowered.endswith(".zip")
    if not is_zip:
        if lowered and not lowered.endswith((".md", ".markdown", ".txt")):
            if lowered.endswith(_EXECUTABLE_SUFFIXES):
                raise SkillBundleError("host_execution_refused", "Scripts are refused.")
            raise SkillBundleError("bundle_refused", "Upload a SKILL.md or a .zip bundle.")
        if _looks_binary(bytes(data)):
            raise SkillBundleError("bundle_refused", "The file is not text.")
        return parse_skill_md(_decode(bytes(data)))
    return _read_zip(bytes(data))


def _read_zip(data: bytes) -> ParsedSkill:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        raise SkillBundleError("bundle_refused", "Not a readable zip.") from exc
    with archive:
        # Central directory only: nothing below reads an entry body.
        infos = archive.infolist()
        if len(infos) > MAX_BUNDLE_ENTRIES:
            raise SkillBundleError("bundle_too_large", "Too many entries.")
        if sum(info.file_size for info in infos) > MAX_BUNDLE_UNCOMPRESSED_BYTES:
            raise SkillBundleError("bundle_too_large", "The bundle is too large uncompressed.")
        seen = set()
        skill_info = None
        reference_infos = []
        for info in infos:
            if info.filename in seen:
                raise SkillBundleError("bundle_refused", "Duplicate entries are refused.")
            seen.add(info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise SkillBundleError("bundle_refused", "Symlinks are refused.")
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SkillBundleError("bundle_refused", "Special files are refused.")
            if info.flag_bits & 0x1:
                raise SkillBundleError("bundle_refused", "Encrypted entries are refused.")
            kind = _entry_kind(info.filename)
            if info.file_size > RATIO_FLOOR_BYTES and info.file_size > MAX_COMPRESSION_RATIO * max(
                info.compress_size, 1
            ):
                raise SkillBundleError("bundle_too_large", "An entry is over-compressed.")
            if kind == "references_dir":
                if info.file_size:
                    raise SkillBundleError("bundle_refused", "A directory entry has content.")
                continue
            if kind == "skill":
                if info.file_size > int(settings.SKILLS_BUNDLE_MAX_BYTES):
                    raise SkillBundleError("bundle_too_large", "SKILL.md is too large.")
                skill_info = info
            else:
                if info.file_size > MAX_REFERENCE_BYTES:
                    raise SkillBundleError("bundle_too_large", "A reference is too large.")
                reference_infos.append(info)
        if skill_info is None:
            raise SkillBundleError("bundle_refused", "The bundle has no SKILL.md.")
        if len(reference_infos) > MAX_REFERENCES:
            raise SkillBundleError("bundle_refused", "Too many references.")
        # Limits passed: read each entry, capped at its declared size.
        skill_bytes = _read_capped(archive, skill_info)
        references = {}
        for info in reference_infos:
            raw = _read_capped(archive, info)
            if _looks_binary(raw):
                raise SkillBundleError("host_execution_refused", "A binary file is refused.")
            references[info.filename] = _decode(raw)
    if _looks_binary(skill_bytes):
        raise SkillBundleError("bundle_refused", "SKILL.md is not text.")
    return parse_skill_md(_decode(skill_bytes), references=references)
