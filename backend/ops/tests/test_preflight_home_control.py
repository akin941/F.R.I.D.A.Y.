"""Phase 13 closeout (brief D15 and the boundary "no new protocol surface"):
`check_home_control` -- `nodes_labels`, `ha_entities`, `linux_node_vendor`
and `node_protocol_frozen`, each pass / warn / fail, read-only.

No test reaches a real Home Assistant (the bridge's `httpx.MockTransport`
seam, `connectors.tests.fake_home_assistant`), a node or a model; the Linux
node and `friday-voice` checkouts are the repository's own files or
temporary copies."""

import ast
import shutil
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from ai_core.models import ModelUsage
from connectors.models import HomeEntity
from connectors.tests.fake_home_assistant import allow, ha, salon_tv_state  # noqa: F401
from core.channels import CHANNEL_WEB
from nodes import bus
from nodes.models import Node, NodeInvocation
from nodes.services import relabel_node
from nodes.tests.two_nodes import cleanup, make_two_nodes
from ops import preflight
from ops.preflight import FAIL, PASS, WARN
from workspaces.models import Workspace

BACKEND = Path(django_settings.BASE_DIR).resolve()
REPO = BACKEND.parent
LINUX_NODE = REPO / "node" / "friday-node-linux"
FRIDAY_VOICE = REPO / "node" / "friday-voice"
ROWS = ["nodes_labels", "ha_entities", "linux_node_vendor", "node_protocol_frozen"]
TV = "media_player.salon_tv"


def _rows():
    return {r.name: r for r in preflight.check_home_control()}


@pytest.fixture
def owner(db):
    from accounts.services import register_user

    user = register_user(email="owner@test.dev", password="Str0ngDevPass!42", name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def personal(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def home(owner, personal):
    world = make_two_nodes(owner, personal)
    yield world
    cleanup(world)


@pytest.fixture
def ha_env(ha, settings):  # noqa: F811
    settings.HA_ALLOWED_DOMAINS = []
    ha.states[TV] = salon_tv_state()
    return ha


# --- nodes_labels ----------------------------------------------------------------


@pytest.mark.django_db
def test_nodes_labels_warns_while_no_node_is_paired():
    row = _rows()["nodes_labels"]
    assert row.status == WARN and "no node paired" in row.detail


@pytest.mark.django_db
def test_two_labelled_nodes_with_rooms_pass(home):
    row = _rows()["nodes_labels"]
    assert row.status == PASS, row
    assert row.detail == "2 node(s) of 1 owner(s), names unique, rooms set"


@pytest.mark.django_db
def test_a_node_without_a_room_in_a_two_node_home_warns(home):
    relabel_node(actor=home.owner, node=home.desk, channel=CHANNEL_WEB, room_id=None)
    row = _rows()["nodes_labels"]
    assert row.status == WARN
    assert "no room set: Masaüstü" in row.detail


@pytest.mark.django_db
def test_a_single_node_needs_no_room(owner, personal):
    from nodes.tests.two_nodes import add_node

    node = add_node(owner, name="MacBook", platform="macos", role="laptop")
    try:
        assert _rows()["nodes_labels"].status == PASS
    finally:
        bus.clear_presence(node.pk)


@pytest.mark.django_db
def test_a_duplicate_name_key_fails(home):
    # Pairing and PATCH refuse it; a direct write is what the row guards.
    Node.objects.filter(pk=home.desk.pk).update(name="MacBook", name_key=home.mac.name_key)
    row = _rows()["nodes_labels"]
    assert row.status == FAIL
    assert "duplicate names: MacBook, MacBook" in row.detail


@pytest.mark.django_db
def test_a_revoked_node_does_not_count(home):
    from accounts import devices

    Node.objects.filter(pk=home.desk.pk).update(name="MacBook", name_key=home.mac.name_key)
    devices.revoke_device(device=Node.objects.get(pk=home.desk.pk).device, actor=home.owner)
    row = _rows()["nodes_labels"]
    assert row.status == PASS and row.detail.startswith("1 node(s)")


# --- ha_entities -----------------------------------------------------------------


@pytest.mark.django_db
def test_ha_entities_warns_with_an_empty_allowlist(ha_env):
    row = _rows()["ha_entities"]
    assert row.status == WARN and "no entity allowlisted" in row.detail
    assert ha_env.requests == []  # nothing to check, nothing read


@pytest.mark.django_db
def test_an_allowlisted_entity_present_in_ha_passes_with_one_read(ha_env, personal):
    allow(personal, TV, ["turn_on"], label="TV")
    row = _rows()["ha_entities"]
    assert row.status == PASS, row
    assert row.detail == "1 enabled entity, each inside the code maximum and present in HA"
    assert [(method, path) for method, path, _, _ in ha_env.requests] == [("GET", "/api/states")]
    assert ha_env.posts == []


@pytest.mark.django_db
def test_an_entity_gone_from_ha_warns(ha_env, personal):
    allow(personal, TV, ["turn_on"], label="TV")
    allow(personal, "light.mutfak", ["turn_on"], label="mutfak")
    row = _rows()["ha_entities"]
    assert row.status == WARN
    assert row.detail == "2 enabled entities; not found in Home Assistant: light.mutfak"


@pytest.mark.django_db
def test_a_disabled_entity_is_not_checked(ha_env, personal):
    allow(personal, TV, ["turn_on"], label="TV")
    allow(personal, "light.mutfak", ["turn_on"], label="mutfak", enabled=False)
    assert _rows()["ha_entities"].status == PASS


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("entity_id", "services", "code"),
    [
        ("lock.front_door", ["unlock"], "domain_not_allowed"),
        ("script.everything", ["turn_on"], "domain_not_allowed"),
        ("sensor.salon_temp", ["turn_on"], "domain_not_allowed"),
        (TV, ["play_media"], "service_not_allowed"),
    ],
)
def test_an_entity_outside_the_code_maximum_fails(ha_env, personal, entity_id, services, code):
    row = allow(personal, TV, ["turn_on"], label="TV")
    # `HomeEntity.save` refuses these; a queryset update is the bypass the
    # row exists to catch.
    HomeEntity.objects.filter(pk=row.pk).update(entity_id=entity_id, services=services)
    result = _rows()["ha_entities"]
    assert result.status == FAIL
    assert f"{entity_id}={code}" in result.detail
    assert ha_env.requests == []  # refused before any read


@pytest.mark.django_db
def test_ha_not_configured_warns(ha_env, personal, monkeypatch):
    from connectors.bridges import home_assistant as bridge

    allow(personal, TV, ["turn_on"], label="TV")
    monkeypatch.delenv(bridge.TOKEN_ENV)
    row = _rows()["ha_entities"]
    assert row.status == WARN
    assert row.detail == "1 enabled entity; ha_states read failed: not_configured"


@pytest.mark.django_db
def test_ha_unreachable_fails(ha_env, personal, monkeypatch):
    import httpx

    from connectors.bridges import home_assistant as bridge

    allow(personal, TV, ["turn_on"], label="TV")

    def refuse(request):
        raise httpx.ConnectError("down")

    transport = httpx.MockTransport(refuse)
    monkeypatch.setattr(bridge, "_http_transport", lambda: transport)
    row = _rows()["ha_entities"]
    assert row.status == FAIL
    assert row.detail == "1 enabled entity; ha_states read failed: transport_error"


@pytest.mark.django_db
def test_an_entity_narrowed_away_by_the_domain_setting_warns_without_a_read(
    ha_env, personal, settings
):
    allow(personal, TV, ["turn_on"], label="TV")
    settings.HA_ALLOWED_DOMAINS = ["light"]
    row = _rows()["ha_entities"]
    assert row.status == WARN
    assert "narrowed away by HA_ALLOWED_DOMAINS: media_player.salon_tv" in row.detail
    assert ha_env.requests == []


@pytest.mark.django_db
def test_the_ha_detail_never_carries_a_token_or_a_state(ha_env, personal):
    from connectors.tests.fake_home_assistant import BASE_URL, TOKEN_CANARY

    allow(personal, TV, ["turn_on"], label="TV")
    allow(personal, "light.mutfak", ["turn_on"], label="mutfak")
    detail = _rows()["ha_entities"].detail
    for secret in (TOKEN_CANARY, BASE_URL, "SECRET-TITLE", "Samsung"):
        assert secret not in detail


# --- linux_node_vendor -----------------------------------------------------------


def _vendor_tree_constants():
    tree = ast.parse((LINUX_NODE / "scripts" / "vendor.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets] == ["TREES"]:
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("TREES not found in scripts/vendor.py")


def test_the_vendored_trees_match_the_package_script():
    if not LINUX_NODE.is_dir():
        pytest.skip("no node/friday-node-linux checkout")
    assert preflight.LINUX_NODE_VENDORED_TREES == _vendor_tree_constants()


@pytest.mark.django_db
def test_the_checked_in_linux_node_passes(settings):
    if not LINUX_NODE.is_dir():
        pytest.skip("no node/friday-node-linux checkout")
    settings.PREFLIGHT_LINUX_NODE_DIR = ""
    row = _rows()["linux_node_vendor"]
    assert row.status == PASS, row
    assert row.detail.endswith("vendored file(s) match vendor.lock")


@pytest.mark.django_db
def test_no_linux_node_checkout_warns(settings, tmp_path):
    settings.PREFLIGHT_LINUX_NODE_DIR = str(tmp_path / "absent")
    row = _rows()["linux_node_vendor"]
    assert row.status == WARN and "no node/friday-node-linux" in row.detail


@pytest.fixture
def linux_copy(settings, tmp_path):
    if not LINUX_NODE.is_dir():
        pytest.skip("no node/friday-node-linux checkout")
    root = tmp_path / "friday-node-linux"
    shutil.copytree(
        LINUX_NODE / "friday_node_linux" / "_vendor",
        root / "friday_node_linux" / "_vendor",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy(LINUX_NODE / "vendor.lock", root / "vendor.lock")
    settings.PREFLIGHT_LINUX_NODE_DIR = str(root)
    return root


@pytest.mark.django_db
def test_a_changed_vendored_copy_fails(linux_copy):
    target = linux_copy / "friday_node_linux" / "_vendor" / "nodes" / "reference" / "node.py"
    target.write_bytes(target.read_bytes() + b"\n# drift\n")
    row = _rows()["linux_node_vendor"]
    assert row.status == FAIL
    assert "_vendor/nodes/reference/node.py" in row.detail


@pytest.mark.django_db
def test_a_lock_naming_a_changed_backend_source_fails(linux_copy):
    lock = linux_copy / "vendor.lock"
    lines = lock.read_text(encoding="utf-8").splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if line.endswith("nodes/reference/spool.py\n"))
    lines[index] = "0" * 64 + lines[index][64:]
    lock.write_text("".join(lines), encoding="utf-8")
    row = _rows()["linux_node_vendor"]
    assert row.status == FAIL and "backend/nodes/reference/spool.py" in row.detail


@pytest.mark.django_db
def test_a_backend_file_left_out_of_the_lock_fails(linux_copy):
    lock = linux_copy / "vendor.lock"
    kept = [
        line
        for line in lock.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.endswith("nodes/capabilities/v1/git.status.json\n")
    ]
    lock.write_text("".join(kept), encoding="utf-8")
    row = _rows()["linux_node_vendor"]
    assert row.status == FAIL
    assert "backend/nodes/capabilities/v1/git.status.json (not vendored)" in row.detail


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["", "# only a comment\n", "not a lock line\n"])
def test_a_missing_or_malformed_lock_fails(linux_copy, body):
    (linux_copy / "vendor.lock").write_text(body, encoding="utf-8")
    assert _rows()["linux_node_vendor"].status == FAIL
    (linux_copy / "vendor.lock").unlink()
    row = _rows()["linux_node_vendor"]
    assert row.status == FAIL and "vendor.lock missing" in row.detail


# --- node_protocol_frozen --------------------------------------------------------


@pytest.mark.django_db
def test_the_protocol_surface_is_the_phase_12_closeout_one(settings):
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = ""
    digest, count = preflight.node_protocol_digest()
    assert (digest, count) == (
        preflight.PHASE12_NODE_PROTOCOL_SHA256,
        preflight.PHASE12_NODE_PROTOCOL_FILES,
    )
    row = _rows()["node_protocol_frozen"]
    if not FRIDAY_VOICE.is_dir():
        assert row.status == WARN
        return
    assert row.status == PASS, row
    assert row.detail == (
        "196 node protocol files and the friday-voice lock unchanged since the Phase 12 closeout"
    )


def _backend_copy(tmp_path):
    copy = tmp_path / "backend"
    for pattern in preflight.NODE_PROTOCOL_GLOBS:
        for path in BACKEND.glob(pattern):
            if path.is_file():
                target = copy / path.relative_to(BACKEND)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(path, target)
    return copy


def test_the_digest_moves_with_any_schema_or_contract_change(tmp_path):
    copy = _backend_copy(tmp_path)
    assert preflight.node_protocol_digest(copy) == preflight.node_protocol_digest()
    schema = next((copy / "nodes" / "protocol" / "schemas").rglob("*.json"))
    schema.write_bytes(schema.read_bytes().replace(b"{", b"{ ", 1))
    changed, count = preflight.node_protocol_digest(copy)
    assert changed != preflight.PHASE12_NODE_PROTOCOL_SHA256 and count == 196
    new = copy / "nodes" / "capabilities" / "v1" / "screen.capture.json"
    new.write_text("{}", encoding="utf-8")
    assert preflight.node_protocol_digest(copy)[1] == 197


@pytest.mark.django_db
def test_a_changed_node_protocol_fails(monkeypatch, settings):
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = ""
    monkeypatch.setattr(preflight, "PHASE12_NODE_PROTOCOL_SHA256", "0" * 64)
    row = _rows()["node_protocol_frozen"]
    assert row.status == FAIL
    assert row.detail.startswith("the node protocol surface changed (196 files, was 196;")
    assert row.detail.endswith("(a protocol change needs an ADR)")


@pytest.mark.django_db
def test_a_changed_friday_voice_lock_fails(settings, tmp_path):
    root = tmp_path / "friday-voice"
    root.mkdir()
    (root / "conformance.lock").write_text("# changed\n", encoding="utf-8")
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = str(root)
    row = _rows()["node_protocol_frozen"]
    assert row.status == FAIL and "conformance.lock changed" in row.detail
    (root / "conformance.lock").unlink()
    row = _rows()["node_protocol_frozen"]
    assert row.status == FAIL and "conformance.lock missing" in row.detail


@pytest.mark.django_db
def test_without_a_friday_voice_checkout_only_the_node_half_is_checked(settings, tmp_path):
    settings.PREFLIGHT_FRIDAY_VOICE_DIR = str(tmp_path / "absent")
    row = _rows()["node_protocol_frozen"]
    assert row.status == WARN
    assert "196 node protocol files unchanged" in row.detail
    assert "friday-voice lock not checkable here" in row.detail


# --- wiring ----------------------------------------------------------------------


@pytest.mark.django_db
def test_the_closeout_rows_are_registered_last(home, ha_env, personal):
    allow(personal, TV, ["turn_on"], label="TV")
    names = [name for name, _ in preflight.CHECKS]
    assert names[-1] == "home_control"
    assert ("home_control", preflight.check_home_control) in preflight.CHECKS
    results = preflight.run_preflight(checks=(("home_control", preflight.check_home_control),))
    assert [r.name for r in results] == ROWS
    # Read-only: no model call, nothing sent to a node, no HA write.
    assert not ModelUsage.objects.exists()
    assert not NodeInvocation.objects.exists()
    for node in home.nodes:
        assert bus.drain_inbox(node.pk) == []
    assert ha_env.posts == []


@pytest.mark.django_db
def test_one_crashing_row_does_not_hide_the_others(monkeypatch):
    def boom():
        raise RuntimeError("table missing")

    monkeypatch.setattr(preflight, "_check_ha_entities", boom)
    rows = _rows()
    assert rows["ha_entities"].status == FAIL
    assert rows["ha_entities"].detail == "check crashed: RuntimeError"
    assert list(rows) == ROWS
