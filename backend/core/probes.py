"""The probe registry (Phase 12 D1, ADR-016).

A probe is a small, deterministic function, registered at startup by the
app that owns the data, that tells a proactive run what changed: a count,
up to five labelled items and the opaque keys of every current item. The
`proactive` app runs probes; it never imports the apps that own the data
(D19), so the registry lives here, importable by every app.

This module imports only the standard library: the workspace and the
owner are passed to `collect` as `Any` (no `TYPE_CHECKING` import of
`workspaces` or `accounts` either), so the "core must stay independent of
kernel apps" contract holds. Each owning app's `collect` annotates its own
concrete types.

What a probe may say is closed:

- `name` is `<owner_app>.<snake_case>`; `digest_section` and the notice
  `link` paths come from closed sets below; `notice_kind` is the probe's
  own name (one notice kind per probe that emits notices) or "".
- An item `label` is built by the owning app from Friday-authored
  templates and owner-authored fields only (a task title, a repo name, an
  owner-organised calendar title) -- never connector, tool or model
  output, never a coding session's notification text. `validate_result`
  refuses the obvious shapes of foreign text (markup, URLs, control
  characters) as a belt; the owning app's own rule is the braces.
- An item `key` is an opaque id, never text.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DATA_CLASS_FIRM = "firm"
DATA_CLASS_PERSONAL = "personal"
DATA_CLASSES = frozenset({DATA_CLASS_FIRM, DATA_CLASS_PERSONAL})

# D6's section order; empty sections are omitted from a digest.
DIGEST_SECTIONS = (
    "approvals",
    "calendar",
    "tasks",
    "sessions",
    "harness",
    "learning",
    "nodes",
    "budgets",
    "n8n",
    "followups",
    "skills",
)

# D5: the closed set of notice/item links. `<id>` is an opaque id.
_ID = r"[A-Za-z0-9-]{1,64}"
LINK_PATTERNS = (
    re.compile(r"^/approvals$"),
    re.compile(rf"^/approvals/{_ID}$"),
    re.compile(r"^/tasks$"),
    re.compile(r"^/today$"),
    re.compile(r"^/memory/learning$"),
    re.compile(r"^/sessions$"),
    re.compile(rf"^/harness/{_ID}$"),
    # Branch 2: the implementation-job page (Gate A / Gate B) and the console.
    re.compile(r"^/harness$"),
    re.compile(rf"^/harness/implementations/{_ID}$"),
    re.compile(r"^/devices$"),
    re.compile(rf"^/chat/{_ID}$"),
    re.compile(r"^/skills$"),
    re.compile(rf"^/skills/{_ID}$"),
)

MAX_ITEMS = 5
MAX_LABEL_CHARS = 120
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,47}$")
_APP_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# A label is plain, short, Friday-template or owner text: no markup, no
# link, no control or invisible formatting characters.
_LABEL_REFUSED = re.compile(
    r"[<>\x00-\x1f\x7f​-‏‪-‮⁠-⁤﻿]"
    r"|://|\bwww\.|\[[^\]]*\]\(",
    re.IGNORECASE,
)


class ProbeResultInvalid(ValueError):
    """A probe returned something outside its contract. The run counts the
    probe `probe_failed` and moves on; nothing of the result is used."""


def is_allowed_link(link: str) -> bool:
    return isinstance(link, str) and any(p.match(link) for p in LINK_PATTERNS)


@dataclass(frozen=True)
class ProbeItem:
    key: str
    label: str
    link: str = ""


@dataclass(frozen=True)
class ProbeResult:
    """`count`: how many items the probe sees now. `items`: at most five,
    labelled. `item_keys`: the opaque key of every current item (not only
    the labelled ones), the D5 delta baseline; empty means "the labelled
    items' keys". `data_class`: the class of the data the probe read."""

    count: int
    data_class: str
    items: tuple = ()
    item_keys: tuple = ()

    def all_keys(self) -> tuple:
        if self.item_keys:
            return tuple(self.item_keys)
        return tuple(item.key for item in self.items)


@dataclass(frozen=True)
class ProbeSpec:
    name: str  # "approvals.pending", closed snake_case
    owner_app: str  # "approvals"
    # (workspace, owner, now) -> ProbeResult; typed Any so core imports no
    # Django model.
    collect: Callable[[Any, Any, datetime], ProbeResult]
    digest_section: str  # one of DIGEST_SECTIONS
    notice_kind: str = ""  # "" = digest only; otherwise the probe's name
    link: str = ""  # the notice link when an item carries none
    # Friday-authored notice title template: "{label}" and "{count}" only.
    notice_template: str = field(default="")


_REGISTRY: dict[str, ProbeSpec] = {}


def validate_spec(spec: ProbeSpec) -> None:
    """Boot-time checks (a programming error raises `ValueError`)."""
    if not isinstance(spec.name, str) or not _NAME_RE.match(spec.name):
        raise ValueError(f"Probe name {spec.name!r} is not <app>.<snake_case>.")
    if not isinstance(spec.owner_app, str) or not _APP_RE.match(spec.owner_app):
        raise ValueError(f"{spec.name!r}: invalid owner app {spec.owner_app!r}.")
    if spec.name.split(".", 1)[0] != spec.owner_app:
        raise ValueError(f"{spec.name!r}: the name must start with its owner app.")
    if not callable(spec.collect):
        raise ValueError(f"{spec.name!r}: collect is not callable.")
    if spec.digest_section not in DIGEST_SECTIONS:
        raise ValueError(f"{spec.name!r}: unknown digest section {spec.digest_section!r}.")
    if spec.notice_kind not in ("", spec.name):
        raise ValueError(f"{spec.name!r}: the notice kind is the probe's own name or empty.")
    if spec.link and not is_allowed_link(spec.link):
        raise ValueError(f"{spec.name!r}: link {spec.link!r} is outside the closed set.")
    if spec.notice_kind:
        if not spec.notice_template:
            raise ValueError(f"{spec.name!r}: a notice probe needs a title template.")
        stripped = spec.notice_template.replace("{label}", "").replace("{count}", "")
        if "{" in stripped or "}" in stripped or _LABEL_REFUSED.search(stripped):
            raise ValueError(f"{spec.name!r}: the title template may use {{label}}/{{count}}.")


def register_probe(spec: ProbeSpec) -> None:
    validate_spec(spec)
    if spec.name in _REGISTRY and _REGISTRY[spec.name] is not spec:
        raise ValueError(f"Probe {spec.name!r} is already registered.")
    _REGISTRY[spec.name] = spec


def unregister_probe(name: str) -> None:
    """Test-only."""
    _REGISTRY.pop(name, None)


def get_probe(name: str) -> ProbeSpec | None:
    return _REGISTRY.get(name)


def list_probes() -> list[ProbeSpec]:
    """Every registered probe, in D6 section order, then by name."""
    order = {section: index for index, section in enumerate(DIGEST_SECTIONS)}
    return sorted(_REGISTRY.values(), key=lambda s: (order[s.digest_section], s.name))


def validate_label(label: str) -> None:
    if not isinstance(label, str) or not label.strip():
        raise ProbeResultInvalid("label_empty")
    if len(label) > MAX_LABEL_CHARS:
        raise ProbeResultInvalid("label_too_long")
    if _LABEL_REFUSED.search(label):
        raise ProbeResultInvalid("label_refused")


def validate_result(spec: ProbeSpec, result: Any, *, max_keys: int) -> ProbeResult:
    """The run-time contract of one probe result; raises
    `ProbeResultInvalid` (a closed code, never the offending text)."""
    if not isinstance(result, ProbeResult):
        raise ProbeResultInvalid("not_a_result")
    if isinstance(result.count, bool) or not isinstance(result.count, int) or result.count < 0:
        raise ProbeResultInvalid("count_invalid")
    if result.data_class not in DATA_CLASSES:
        raise ProbeResultInvalid("data_class_invalid")
    items = tuple(result.items)
    if len(items) > MAX_ITEMS:
        raise ProbeResultInvalid("too_many_items")
    keys = result.all_keys()
    if len(keys) > max_keys:
        raise ProbeResultInvalid("too_many_keys")
    if len(set(keys)) != len(keys):
        raise ProbeResultInvalid("duplicate_key")
    for key in keys:
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise ProbeResultInvalid("key_invalid")
    key_set = set(keys)
    for item in items:
        if not isinstance(item, ProbeItem):
            raise ProbeResultInvalid("item_invalid")
        if item.key not in key_set:
            raise ProbeResultInvalid("item_key_unlisted")
        validate_label(item.label)
        if item.link and not is_allowed_link(item.link):
            raise ProbeResultInvalid("link_refused")
    return result


def render_notice_title(spec: ProbeSpec, *, label: str, count: int, max_chars: int = 160) -> str:
    """The Friday-authored template filled with one D1 label (already
    validated); bounded."""
    title = spec.notice_template.replace("{count}", str(int(count))).replace("{label}", label)
    return title[:max_chars]


def safe_label(text: Any, fallback: str, *, prefix: str = "") -> str:
    """An owner-authored field as a D1 label, or the Friday `fallback` when
    the field is empty or has a shape `validate_label` refuses (markup, a
    URL, control or invisible characters). Whitespace is collapsed and the
    result bounded, so one odd task title never fails a whole probe."""
    cleaned = " ".join(str(text or "").split())
    candidate = f"{prefix}{cleaned}" if cleaned else ""
    if len(candidate) > MAX_LABEL_CHARS:
        candidate = candidate[: MAX_LABEL_CHARS - 1].rstrip() + "…"
    try:
        validate_label(candidate)
    except ProbeResultInvalid:
        return f"{prefix}{fallback}"[:MAX_LABEL_CHARS]
    return candidate


def data_class_of(workspace: Any) -> str:
    """The D1 data class of a workspace: `personal` for the personal
    workspace, `firm` for every other kind."""
    return DATA_CLASS_PERSONAL if getattr(workspace, "type", "") == "personal" else DATA_CLASS_FIRM
