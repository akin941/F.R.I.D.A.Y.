"""Phase 8B branch 5 (brief D10): the Graphify producer's pieces -- the
captured `graph.json` fixture (`graphifyy==0.9.103` over
`fixtures/graphify/package_v1`, and a second snapshot of the same package),
the schema, the mapping, the hostile-`/out` reader, the network-denied
launch argv and the image files. No real Graphify run and no Docker daemon:
`subprocess.run` is replaced through the `harness.runner` module seam."""

import copy
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest

from harness import graphify, runner, safe_read

FIXTURES = Path(__file__).parent / "fixtures" / "graphify"
DOCKER_DIR = Path(__file__).resolve().parents[1] / "docker"
REPO_ROOT = Path(__file__).resolve().parents[3]
PINNED_WHEEL_SHA256 = "7948c030f8b04153d88bc1ee23a485ddaa0c2d6aaa03a748c177e3c217bcfe34"


def load(name="graph_0.9.103.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- schema --------------------------------------------------------------------


def test_the_captured_fixtures_match_the_pinned_schema():
    schema = graphify.load_schema()
    for name in ("graph_0.9.103.json", "graph_0.9.103_v2.json"):
        graphify.validate(load(name), schema)


def test_the_validator_agrees_with_jsonschema_on_the_schema_file():
    jsonschema = pytest.importorskip("jsonschema")
    schema = graphify.load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(load(), schema)
    bad = load()
    bad["telemetry"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    with pytest.raises(graphify.GraphifyRefused):
        graphify.validate(bad, schema)


def test_the_schema_uses_only_keywords_the_validator_implements():
    def walk(node):
        if isinstance(node, dict):
            keys = set(node)
            if "properties" in node or "type" in node:
                assert keys <= graphify.SUPPORTED_KEYWORDS, keys - graphify.SUPPORTED_KEYWORDS
            for key, value in node.items():
                if key == "properties":
                    for sub in value.values():
                        walk(sub)
                elif isinstance(value, dict):
                    walk(value)

    walk(graphify.load_schema())


def _mutations():
    def unknown_top(g):
        g["schema_version"] = 2

    def missing_top(g):
        del g["hyperedges"]

    def missing_node_field(g):
        del g["nodes"][0]["label"]

    def missing_link_field(g):
        del g["links"][0]["confidence"]

    def wrong_type(g):
        g["nodes"][0]["id"] = 7

    def long_label(g):
        g["nodes"][0]["label"] = "x" * 3000

    def long_id(g):
        g["nodes"][0]["id"] = "x" * 513

    def too_many_nodes(g):
        g["nodes"] = [dict(g["nodes"][0], id=f"n{i}") for i in range(200_001)]

    return [
        unknown_top,
        missing_top,
        missing_node_field,
        missing_link_field,
        wrong_type,
        long_label,
        long_id,
        too_many_nodes,
    ]


@pytest.mark.parametrize("mutate", _mutations(), ids=lambda f: f.__name__)
def test_schema_refusals(mutate):
    raw = load()
    mutate(raw)
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.map_graph(raw)
    assert excinfo.value.reason == "schema"


def test_a_refusal_message_never_quotes_a_value():
    raw = load()
    raw["nodes"][0]["label"] = "SECRET-LABEL-" + "x" * 3000
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.map_graph(raw)
    assert "SECRET-LABEL" not in str(excinfo.value)
    assert "$.nodes[0].label" in str(excinfo.value)


def test_duplicate_node_ids_are_refused():
    raw = load()
    raw["nodes"].append(dict(raw["nodes"][1]))
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.map_graph(raw)
    assert excinfo.value.reason == "duplicate_node"


@pytest.mark.parametrize(
    "data,reason",
    [
        (b"\xff\xfe", "invalid_json"),
        (b"{not json", "invalid_json"),
        (b"[" * 100_000, "invalid_json"),
    ],
)
def test_parse_graph_bytes_refuses_garbage(data, reason):
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.parse_graph_bytes(data)
    assert excinfo.value.reason == reason


def test_parse_graph_bytes_refuses_oversize():
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.parse_graph_bytes(b" " * (graphify.MAX_GRAPH_BYTES + 1))
    assert excinfo.value.reason == "oversize"


# --- mapping -------------------------------------------------------------------


def _by_kind(snapshot):
    kinds = {}
    for item in snapshot["entities"]:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    return kinds


def _by_predicate(snapshot):
    counts = {}
    for item in snapshot["facts"]:
        counts[item["predicate"]] = counts.get(item["predicate"], 0) + 1
    return counts


def test_fixture_maps_to_the_expected_entities_and_facts():
    snapshot, stats = graphify.map_graph(load())
    assert _by_kind(snapshot) == {"code_module": 7, "code_class": 3, "code_function": 8, "tool": 4}
    assert _by_predicate(snapshot) == {"imports": 13, "contains": 11, "calls": 4, "inherits": 2}
    assert stats["entities"] == 22 and stats["facts"] == 30
    modules = sorted(e["name"] for e in snapshot["entities"] if e["kind"] == "code_module")
    assert modules == [
        "sample/__init__.py",
        "sample/billing/__init__.py",
        "sample/billing/models.py",
        "sample/billing/services.py",
        "sample/core/__init__.py",
        "sample/core/models.py",
        "sample/core/util.py",
    ]
    names = {e["name"] for e in snapshot["entities"]}
    assert "Invoice (sample/billing/models.py)" in names
    assert ".render() (sample/billing/models.py)" in names
    assert {e["name"] for e in snapshot["entities"] if e["kind"] == "tool"} == {
        "decimal",
        "json",
        "os",
        "re",
    }


def test_inferred_and_non_ast_items_are_dropped():
    raw = load()
    snapshot, stats = graphify.map_graph(raw)
    inferred = [link for link in raw["links"] if link["confidence"] == "INFERRED"]
    assert stats["links_inferred"] == len(inferred) == 5
    kept = {(f["subject"], f["object"]) for f in snapshot["facts"]}
    for link in inferred:
        if link["relation"] != "calls":
            continue
        assert (link["source"], link["target"]) not in kept or any(
            other["confidence"] == "EXTRACTED"
            and (other["source"], other["target"]) == (link["source"], link["target"])
            for other in raw["links"]
        )
    # the docstring ("rationale") node is not code
    assert stats["nodes_dropped"] == 1
    assert "sample_init_rationale_1" not in {e["key"] for e in snapshot["entities"]}

    # a node and a link that did not come from the AST pass are dropped too
    raw = load()
    raw["nodes"][3]["_origin"] = "llm"
    raw["links"][1]["_origin"] = "llm"
    snapshot2, stats2 = graphify.map_graph(raw)
    assert raw["nodes"][3]["id"] not in {e["key"] for e in snapshot2["entities"]}
    assert stats2["links_not_ast"] == 1


def test_externals_become_tools_only_as_a_modules_top_level_import():
    snapshot, stats = graphify.map_graph(load())
    tool_edges = [f for f in snapshot["facts"] if f["object"].startswith("tool:")]
    assert {f["predicate"] for f in tool_edges} == {"imports"}
    module_keys = {e["key"] for e in snapshot["entities"] if e["kind"] == "code_module"}
    assert {f["subject"] for f in tool_edges} <= module_keys
    # `normalise()` calls `re` -- a call to an external, dropped
    assert stats["links_external_dropped"] == 1


def test_second_snapshot_differs_by_the_expected_edges():
    first = {tuple(f.values()) for f in graphify.map_graph(load())[0]["facts"]}
    second = {
        tuple(f.values()) for f in graphify.map_graph(load("graph_0.9.103_v2.json"))[0]["facts"]
    }
    assert sorted(first - second) == [
        ("sample_billing_services", "imports", "sample_core_util"),
        ("sample_billing_services", "imports", "sample_core_util_normalise"),
        ("sample_billing_services_issue", "calls", "sample_core_util_normalise"),
    ]
    assert sorted(second - first) == [
        ("sample_billing_services", "contains", "sample_billing_services_refund"),
        ("sample_billing_services_refund", "calls", "sample_billing_services_issue"),
    ]


def test_mapping_caps(monkeypatch):
    monkeypatch.setattr(graphify, "MAX_ENTITIES", 5)
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.map_graph(load())
    assert excinfo.value.reason == "entity_cap"
    monkeypatch.setattr(graphify, "MAX_ENTITIES", 50)
    monkeypatch.setattr(graphify, "MAX_FACTS", 5)
    with pytest.raises(graphify.GraphifyRefused) as excinfo:
        graphify.map_graph(load())
    assert excinfo.value.reason == "fact_cap"


def test_the_mapping_never_carries_unknown_node_keys():
    raw = load()
    raw["nodes"][3]["metadata"] = {"anything": "SHOULD-NOT-TRAVEL"}
    snapshot, _ = graphify.map_graph(raw)
    assert "SHOULD-NOT-TRAVEL" not in json.dumps(snapshot)
    for item in snapshot["entities"]:
        assert set(item) == {"key", "kind", "name"}


def test_the_mapping_does_not_mutate_its_input():
    raw = load()
    before = copy.deepcopy(raw)
    graphify.map_graph(raw)
    assert raw == before


# --- the hostile /out read -----------------------------------------------------------


@pytest.fixture
def out_dir(tmp_path):
    path = tmp_path / "out"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def read_spy(monkeypatch):
    calls = []
    real = safe_read._read

    def spy(fd, size):
        calls.append(size)
        return real(fd, size)

    monkeypatch.setattr(safe_read, "_read", spy)
    return calls


CANARY = "CANARY-host-file-content-7d1f"


def test_a_regular_file_is_read(out_dir, read_spy):
    (out_dir / "graph.json").write_bytes(b'{"ok": 1}')
    assert safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=100) == b'{"ok": 1}'
    assert read_spy


def test_a_symlink_to_a_host_file_is_refused_before_any_read(out_dir, tmp_path, read_spy):
    host_file = tmp_path / "host-secret.txt"
    host_file.write_text(CANARY)
    os.symlink(host_file, out_dir / "graph.json")
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=10_000)
    assert excinfo.value.reason == "symlink"
    assert read_spy == [] and CANARY not in str(excinfo.value)


def test_a_fifo_is_refused_without_blocking(out_dir, read_spy):
    os.mkfifo(out_dir / "graph.json")
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=10_000)
    assert excinfo.value.reason == "not_regular_file" and read_spy == []


def test_a_directory_is_refused(out_dir, read_spy):
    (out_dir / "graph.json").mkdir()
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=10_000)
    assert excinfo.value.reason == "not_regular_file" and read_spy == []


def test_a_unix_socket_is_refused(out_dir, read_spy):
    path = out_dir / "graph.json"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(path))
    except OSError:
        pytest.skip("path too long for a unix socket on this host")
    try:
        with pytest.raises(safe_read.UnsafeOutput):
            safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=10_000)
    finally:
        sock.close()
    assert read_spy == []


def test_an_oversize_file_is_refused_before_any_read(out_dir, read_spy):
    with open(out_dir / "graph.json", "wb") as handle:
        handle.truncate(graphify.MAX_GRAPH_BYTES + 1)  # sparse: > 20 MB, no disk
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=graphify.MAX_GRAPH_BYTES)
    assert excinfo.value.reason == "oversize" and read_spy == []


def test_a_file_that_grows_after_the_stat_is_still_refused(out_dir, monkeypatch):
    path = out_dir / "graph.json"
    path.write_bytes(b"x" * 10)
    real = safe_read._read

    def grow_then_read(fd, size):
        with open(path, "ab") as handle:
            handle.write(b"y" * 100)
        return real(fd, size)

    monkeypatch.setattr(safe_read, "_read", grow_then_read)
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=50)
    assert excinfo.value.reason == "oversize"


def test_a_symlinked_out_directory_is_refused(tmp_path, read_spy):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "graph.json").write_text(CANARY)
    os.symlink(real_dir, tmp_path / "link")
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(tmp_path / "link", "graph.json", limit=10_000)
    assert excinfo.value.reason == "directory_unavailable" and read_spy == []


@pytest.mark.parametrize("name", ["", "../graph.json", "a/graph.json", ".."])
def test_only_a_plain_name_is_accepted(out_dir, name):
    with pytest.raises(safe_read.UnsafeOutput):
        safe_read.read_bounded_regular_file(out_dir, name, limit=10)


def test_a_missing_file_is_refused(out_dir):
    with pytest.raises(safe_read.UnsafeOutput) as excinfo:
        safe_read.read_bounded_regular_file(out_dir, "graph.json", limit=10)
    assert excinfo.value.reason == "missing"


# --- the launch argv (no real docker) -------------------------------------------------


@pytest.fixture
def docker_argv(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="cid123\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return calls


def _flag_values(argv, flag):
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


def test_graphify_launch_is_network_denied_and_read_only(docker_argv, settings, tmp_path):
    settings.HARNESS_DOCKER_NETWORK = "friday-harness-egress"
    container = runner.launch_graphify_container(
        image="friday-harness-graphify:0.9.103",
        clone_dir=str(tmp_path / "clone"),
        out_dir=str(tmp_path / "out"),
        name="friday-graphify-x",
    )
    assert container == "cid123"
    ((argv, kwargs),) = docker_argv
    assert argv[:3] == ["docker", "run", "--detach"]
    assert _flag_values(argv, "--network") == ["none"]
    assert "--read-only" in argv
    assert _flag_values(argv, "--pull") == ["never"]
    assert _flag_values(argv, "--cap-drop") == ["ALL"]
    assert _flag_values(argv, "--security-opt") == ["no-new-privileges"]
    assert _flag_values(argv, "--user") == [f"{os.getuid()}:{os.getgid()}"]
    mounts = _flag_values(argv, "--mount")
    assert mounts == [
        f"type=bind,source={tmp_path / 'clone'},target=/repo,readonly",
        f"type=bind,source={tmp_path / 'out'},target=/out",
    ]
    writable = [m for m in mounts if not m.endswith(",readonly")]
    assert len(writable) == 1 and writable[0].endswith("target=/out")
    tmpfs = _flag_values(argv, "--tmpfs")
    assert any(t.startswith("/work:") and "noexec" in t and "size=" in t for t in tmpfs)
    assert all("noexec" in t for t in tmpfs)
    # never the egress network, never an env file, never an env var, no shell
    assert "friday-harness-egress" not in argv
    assert "--env-file" not in argv and "--env" not in argv and "-e" not in argv
    assert not any(arg.startswith("--network=") for arg in argv)
    assert "--privileged" not in argv
    assert kwargs.get("shell") is not True
    assert argv[-1] == "friday-harness-graphify:0.9.103"
    for limit in ("--memory", "--cpus", "--pids-limit"):
        assert _flag_values(argv, limit)


def test_graphify_launch_failure_is_a_runner_error(monkeypatch, tmp_path):
    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(125, argv, stderr="boom")

    monkeypatch.setattr(runner.subprocess, "run", fail)
    with pytest.raises(runner.RunnerError):
        runner.launch_graphify_container(
            image="i", clone_dir=str(tmp_path), out_dir=str(tmp_path), name="n"
        )


def test_head_commit_and_remote_head_against_a_local_repo(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    sha = git("rev-parse", "HEAD")
    head_sha, committed = runner.head_commit(str(repo))
    assert head_sha == sha and re.match(r"\d{4}-\d{2}-\d{2}T", committed)
    assert runner.remote_head(git_url=f"file://{repo}", branch="main") == sha
    assert runner.remote_head(git_url=f"file://{repo}", branch="nope") == ""
    with pytest.raises(runner.RunnerError):
        runner.remote_head(git_url=f"file://{tmp_path / 'missing'}", branch="main")
    with pytest.raises(runner.RunnerError):
        runner.head_commit(str(tmp_path))


# --- the image files ---------------------------------------------------------------------


def _requirements(lock_text):
    """`{name: (version, [sha256, ...])}` of a hash-locked requirements file."""
    joined = lock_text.replace("\\\n", " ")
    pins = {}
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s]+)\s+(.*)$", line)
        assert match, f"unpinned or malformed lock line: {line[:60]}"
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", match.group(3))
        assert hashes, f"{match.group(1)} has no sha256 hash"
        pins[match.group(1).lower()] = (match.group(2), hashes)
    return pins


def test_dockerfile_is_hash_pinned_and_binary_only():
    dockerfile = (DOCKER_DIR / "graphify.Dockerfile").read_text()
    instructions = [
        line
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    body = "\n".join(instructions)
    froms = [line for line in instructions if line.startswith("FROM ")]
    assert len(froms) == 1 and re.search(r"@sha256:[0-9a-f]{64}$", froms[0])
    assert "--require-hashes" in body
    assert "--only-binary=:all:" in body
    assert "--no-deps" in body
    assert not re.search(r"curl[^\n]*\|\s*(ba)?sh", body)
    assert not re.search(r"wget[^\n]*\|\s*(ba)?sh", body)
    assert "pip install graphify" not in body
    assert "USER 10001" in body
    assert "GRAPHIFY_BACKEND=none" in body and "GRAPHIFY_QUERY_LOG_DISABLE=1" in body
    assert "ENTRYPOINT" in body


def test_lock_pins_graphifyy_by_name_version_and_the_manifest_hash():
    pins = _requirements((DOCKER_DIR / "graphify.lock.txt").read_text())
    version, hashes = pins["graphifyy"]
    assert version == "0.9.103" and hashes == [PINNED_WHEEL_SHA256]
    # the typosquat-prone neighbours are never installed
    assert "graphify" not in pins
    manifest = (REPO_ROOT / "docs" / "mcp" / "graphify.md").read_text()
    assert "graphifyy==0.9.103" in manifest and PINNED_WHEEL_SHA256 in manifest
    # every transitive wheel is locked, and no LLM SDK is in the set
    for needed in ("networkx", "numpy", "rapidfuzz", "pyyaml", "tree-sitter", "tree-sitter-python"):
        assert needed in pins
    for forbidden in ("openai", "anthropic", "litellm", "google-genai", "requests", "httpx"):
        assert forbidden not in pins


def test_entrypoint_copies_without_git_and_bounds_the_output():
    script = (DOCKER_DIR / "graphify_run.sh").read_text()
    assert script.startswith("#!/bin/sh")
    assert "set -eu" in script
    assert "--exclude=./.git" in script
    assert "rm -rf /work/repo/graphify-out" in script
    assert "graphify update repo --no-cluster" in script
    assert "MAX_BYTES=20971520" in script
    assert "/out/graph.json" in script
    assert "GRAPHIFY_BACKEND=none" in script
    assert os.access(DOCKER_DIR / "graphify_run.sh", os.X_OK)


# --- the opt-in real image run (nightly / owner-run) -----------------------------------


@pytest.mark.skipif(
    os.environ.get("HARNESS_GRAPHIFY_IMAGE_TEST") != "1",
    reason="builds and runs the real Graphify image; set HARNESS_GRAPHIFY_IMAGE_TEST=1",
)
def test_real_image_over_the_fixture_package(tmp_path, settings):  # pragma: no cover
    image = "friday-harness-graphify:test"
    subprocess.run(
        ["docker", "build", "-f", "graphify.Dockerfile", "-t", image, "."],
        cwd=DOCKER_DIR,
        check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(["cp", "-r", str(FIXTURES / "package_v1"), str(clone)], check=True)
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    name = "friday-graphify-image-test"
    runner.launch_graphify_container(image=image, clone_dir=str(clone), out_dir=str(out), name=name)
    try:
        subprocess.run(["docker", "wait", name], check=True, timeout=600)
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", name],
            capture_output=True,
            text=True,
            check=True,
        )
        assert inspect.stdout.strip() == "none"
        data = safe_read.read_bounded_regular_file(
            out, "graph.json", limit=graphify.MAX_GRAPH_BYTES
        )
        snapshot, stats = graphify.map_graph(graphify.parse_graph_bytes(data))
        assert stats["entities"] == 22 and stats["facts"] == 30
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False)
