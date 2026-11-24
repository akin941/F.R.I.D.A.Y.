"""Phase 12 D1: the probe registry's boot validation and the run-time
result contract (names, sections, notice kinds, closed links, labels)."""

import ast
import sys
from pathlib import Path

import pytest

from core import probes
from core.probes import ProbeItem, ProbeResult, ProbeResultInvalid, ProbeSpec


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    monkeypatch.setattr(probes, "_REGISTRY", {})


def _collect(workspace, owner, now):
    return ProbeResult(count=0, data_class="personal")


def spec(**overrides):
    values = {
        "name": "tasks.due_today",
        "owner_app": "tasks",
        "collect": _collect,
        "digest_section": "tasks",
        "notice_kind": "tasks.due_today",
        "link": "/tasks",
        "notice_template": "Bugün: {label}",
    }
    values.update(overrides)
    return ProbeSpec(**values)


def test_a_valid_probe_registers_and_lists_in_section_order():
    probes.register_probe(spec())
    probes.register_probe(
        spec(
            name="approvals.pending",
            owner_app="approvals",
            digest_section="approvals",
            notice_kind="approvals.pending",
        )
    )
    assert [s.name for s in probes.list_probes()] == ["approvals.pending", "tasks.due_today"]
    assert probes.get_probe("tasks.due_today").owner_app == "tasks"


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Tasks.Due"},
        {"name": "tasks"},
        {"name": "tasks.due-today"},
        {"name": "memory.due_today"},  # name does not start with its owner app
        {"owner_app": "Tasks"},
        {"digest_section": "weather"},
        {"notice_kind": "tasks.other"},
        {"link": "https://evil.example/x"},
        {"link": "/admin"},
        {"link": "/approvals/../admin"},
        {"notice_template": ""},
        {"notice_template": "{label} <b>bold</b>"},
        {"notice_template": "{title}"},
        {"collect": "not callable"},
    ],
)
def test_registration_refuses_anything_outside_the_closed_sets(overrides):
    with pytest.raises(ValueError):
        probes.register_probe(spec(**overrides))
    assert probes.list_probes() == []


def test_a_name_registers_once():
    probes.register_probe(spec())
    with pytest.raises(ValueError):
        probes.register_probe(spec(notice_template="Başka: {label}"))


def test_a_digest_only_probe_needs_no_template():
    probes.register_probe(spec(notice_kind="", notice_template=""))


@pytest.mark.parametrize(
    "link",
    [
        "/approvals",
        "/approvals/2b1f6c0e-6c39-4b8e-9d55-0d2c1f0a9e11",
        "/tasks",
        "/today",
        "/memory/learning",
        "/sessions",
        "/harness/42",
        "/devices",
        "/chat/abc-123",
        "/skills",
        "/skills/7",
    ],
)
def test_every_closed_link_is_allowed(link):
    assert probes.is_allowed_link(link)


@pytest.mark.parametrize(
    "link", ["/tasks/1", "/chat/", "//evil", "/approvals?x=1", "javascript:alert(1)", "/skills/a/b"]
)
def test_links_outside_the_set_are_refused(link):
    assert not probes.is_allowed_link(link)


def _result(**overrides):
    values = {
        "count": 1,
        "data_class": "personal",
        "items": (ProbeItem(key="t1", label="Faturayı öde", link="/tasks"),),
        "item_keys": ("t1",),
    }
    values.update(overrides)
    return ProbeResult(**values)


def test_a_valid_result_passes():
    assert probes.validate_result(spec(), _result(), max_keys=200).count == 1


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"count": -1}, "count_invalid"),
        ({"count": True}, "count_invalid"),
        ({"data_class": "secret"}, "data_class_invalid"),
        (
            {"items": tuple(ProbeItem(key=f"k{i}", label="x") for i in range(6))},
            "too_many_items",
        ),
        ({"item_keys": ("t1", "t1")}, "duplicate_key"),
        ({"item_keys": ("t 1",)}, "key_invalid"),
        ({"item_keys": ("other",)}, "item_key_unlisted"),
        ({"items": (ProbeItem(key="t1", label="<script>x</script>"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="bkz. https://evil.example"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="www.evil.example"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="[tıkla](/x)"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="gizli​metin"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="satır\nsonu"),)}, "label_refused"),
        ({"items": (ProbeItem(key="t1", label="x" * 121),)}, "label_too_long"),
        ({"items": (ProbeItem(key="t1", label="  "),)}, "label_empty"),
        ({"items": (ProbeItem(key="t1", label="ok", link="/admin"),)}, "link_refused"),
    ],
)
def test_invalid_results_are_refused_with_a_closed_code(overrides, code):
    with pytest.raises(ProbeResultInvalid) as excinfo:
        probes.validate_result(spec(), _result(**overrides), max_keys=200)
    assert str(excinfo.value) == code


def test_too_many_keys_is_refused():
    keys = tuple(f"k{i}" for i in range(4))
    with pytest.raises(ProbeResultInvalid):
        probes.validate_result(spec(), _result(items=(), item_keys=keys), max_keys=3)


def test_a_non_result_is_refused():
    with pytest.raises(ProbeResultInvalid):
        probes.validate_result(spec(), {"count": 1}, max_keys=200)


def test_item_keys_default_to_the_labelled_items():
    result = ProbeResult(count=1, data_class="firm", items=(ProbeItem(key="a", label="A"),))
    assert result.all_keys() == ("a",)


def test_notice_titles_fill_the_template_and_are_bounded():
    title = probes.render_notice_title(spec(notice_template="{count} iş: {label}"), label="X", count=3)
    assert title == "3 iş: X"
    long = probes.render_notice_title(spec(), label="y" * 120, count=1, max_chars=20)
    assert len(long) == 20


@pytest.mark.parametrize("module", ["probes", "unattended"])
def test_core_registry_modules_import_only_the_standard_library(module):
    """D19: `core.probes` and `core.unattended` import nothing outside the
    standard library (no Django, no kernel app, not even a TYPE_CHECKING
    import)."""
    source = Path(__file__).resolve().parents[1] / f"{module}.py"
    tree = ast.parse(source.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)
