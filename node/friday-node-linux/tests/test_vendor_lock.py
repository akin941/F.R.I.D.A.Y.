"""`scripts/vendor.py --check` (D10): the vendored copy and `vendor.lock`
match the backend; a one-byte change anywhere fails the check."""

import importlib.util
import shutil
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def load_vendor():
    spec = importlib.util.spec_from_file_location(
        "friday_linux_vendor_script", PACKAGE_ROOT / "scripts" / "vendor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    """The script pointed at a copy of the vendored tree and lock."""
    module = load_vendor()
    copy = tmp_path / "_vendor"
    shutil.copytree(module.VENDOR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    lock = tmp_path / "vendor.lock"
    shutil.copy(module.LOCK, lock)
    monkeypatch.setattr(module, "VENDOR", copy)
    monkeypatch.setattr(module, "LOCK", lock)
    return module


def test_the_checked_in_vendor_tree_matches_the_backend():
    module = load_vendor()
    assert module.check() == []
    assert module.main(["--check"]) == 0


def test_a_one_byte_change_to_a_vendored_file_fails(vendor):
    target = vendor.VENDOR / "nodes" / "reference" / "node.py"
    data = bytearray(target.read_bytes())
    data[-2] = ord("#") if data[-2] != ord("#") else ord(" ")
    target.write_bytes(bytes(data))
    assert vendor.check() == ["differs from backend/nodes/reference/node.py"]
    assert vendor.main(["--check"]) == 1


def test_a_changed_schema_fails(vendor):
    target = vendor.VENDOR / "nodes" / "protocol" / "schemas" / "v1" / "invoke.schema.json"
    target.write_bytes(target.read_bytes() + b" ")
    assert "differs from backend/nodes/protocol/schemas/v1/invoke.schema.json" in vendor.check()


def test_an_extra_or_missing_file_fails(vendor):
    (vendor.VENDOR / "nodes" / "reference" / "extra.py").write_text("x = 1\n")
    (vendor.VENDOR / "nodes" / "reference" / "spool.py").unlink()
    problems = vendor.check()
    assert "not a vendored file: nodes/reference/extra.py" in problems
    assert "missing: nodes/reference/spool.py" in problems


def test_a_stale_lock_fails(vendor):
    vendor.LOCK.write_text(vendor.LOCK.read_text().replace("a", "b", 1))
    assert vendor.check() == ["vendor.lock is stale"]


def test_writing_restores_a_clean_tree(vendor):
    (vendor.VENDOR / "nodes" / "reference" / "node.py").write_text("tampered\n")
    (vendor.VENDOR / "stray.py").write_text("")
    vendor.write()
    assert vendor.check() == []


def test_the_lock_names_the_backend_source_and_the_copy():
    module = load_vendor()
    lines = [line for line in module.LOCK.read_text().splitlines() if not line.startswith("#")]
    entries = {line.split("  ")[2]: line.split("  ")[:2] for line in lines}
    assert "nodes/reference/node.py" in entries
    assert "nodes/reference/owner_turns.py" in entries
    assert not any(path.startswith("integrations/") for path in entries)
    source, copy = entries["nodes/protocol/schemas/v1/hello.schema.json"]
    assert source == copy  # data files are byte-identical
    source, copy = entries["nodes/reference/node.py"]
    assert source != copy  # header + import rewrite


def test_the_conformance_node_id_is_checked(vendor, monkeypatch, tmp_path):
    fake = tmp_path / "conformance.py"
    fake.write_text('CONFORMANCE_NODE_ID = "00000000-0000-4000-8000-000000000000"\n')
    monkeypatch.setattr(vendor, "CONFORMANCE", fake)
    assert any("CONFORMANCE_NODE_ID" in problem for problem in vendor.check())
