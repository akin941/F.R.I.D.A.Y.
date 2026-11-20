"""Deterministic `self` block (Phase 8A branch 2, D8): the source snapshot,
`build_self_sources --check`, and the assembler (no model call, every line
traceable to the template, the snapshot or the registries)."""

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.services import register_user
from ai_core.gateway import InferenceGateway
from ai_core.models import ModelProvider, ModelUsage
from assistant import self_block
from assistant.self_block import (
    CAPABILITY_PREFIXES,
    TEMPLATES,
    assemble_self_content,
    build_self_content,
    refresh_self_block,
    strip_capability_lines,
)
from assistant.self_sources import SNAPSHOT_PATH, build_snapshot, load_snapshot, render_snapshot
from assistant.tasks import assemble_self_block_task
from memory.models import CoreMemoryBlock
from tools.registry import list_read_tool_names, list_tool_names
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"

CLAUDE_MD = """# CLAUDE.md — Test Rules

Friday OS is a **personal-first** assistant. It has more sentences.

## Non-negotiable architecture rules

1. **Workspace scoping is inviolable.** Every table carries `workspace_id`.
2. **The AI Gateway is the single door.** All LLM calls go through it.

## Commands
"""

ADR_ACCEPTED = """# ADR-001: Workspace Is the Data Axis

## Status
Accepted

## Decision
Every business table carries a `workspace_id`. More text follows.
"""

ADR_PROPOSED = """# ADR-002: Something Not Yet Decided

## Status
Proposed

## Decision
Never quoted.
"""

ADR_LIST = """# ADR-003: Listed Decision

## Status
Accepted (2026-11-20)

## Decision
| Class | Who |
|---|---|
| a | b |

1. **Anything not listed is gate-class.** Rest.
2. Second item.
"""

MASTER_PLAN = """# Master plan

### Friday 3.0 phases (accepted 2026-11-20)

| Order | Phase | Scope | Usable outcome | Status |
|---|---|---|---|---|
| 1 | **6 Voice** | voice | Push-to-talk | **done (2026-11-20)** |
| 2 | **8A Learning memory core** | memory | Knows itself | planned (brief drafted) |
| 3 | **9 Node** | node | Sessions | planned |

### Next section
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text(CLAUDE_MD)
    (tmp_path / "docs" / "adr" / "ADR-001-axis.md").write_text(ADR_ACCEPTED)
    (tmp_path / "docs" / "adr" / "ADR-002-proposed.md").write_text(ADR_PROPOSED)
    (tmp_path / "docs" / "adr" / "ADR-003-list.md").write_text(ADR_LIST)
    (tmp_path / "docs" / "MASTER_PLAN.md").write_text(MASTER_PLAN)
    return tmp_path


# --- snapshot ---


def test_snapshot_quotes_with_anchors(repo):
    snapshot = build_snapshot(repo)
    assert snapshot["identity"] == {
        "file": "CLAUDE.md",
        "line": 3,
        "text": "Friday OS is a personal-first assistant.",
    }
    assert [(r["line"], r["text"]) for r in snapshot["rules"]] == [
        (7, "Workspace scoping is inviolable."),
        (8, "The AI Gateway is the single door."),
    ]
    assert [a["id"] for a in snapshot["adrs"]] == ["ADR-001", "ADR-003"]
    first, listed = snapshot["adrs"]
    assert first["decision"] == {
        "line": 7,
        "text": "Every business table carries a `workspace_id`.",
    }
    assert first["status"] == {"line": 4, "text": "Accepted"}
    assert listed["decision"]["text"] == "Anything not listed is gate-class."
    assert snapshot["phase"]["phase"] == "8A Learning memory core"
    assert snapshot["phase"]["outcome"] == "Knows itself"
    assert snapshot["phase"]["line"] == 8
    assert len(snapshot["content_hash"]) == 64


def test_in_progress_row_wins_over_planned(repo):
    plan = repo / "docs" / "MASTER_PLAN.md"
    plan.write_text(
        plan.read_text().replace("| Sessions | planned |", "| Sessions | in progress |")
    )
    assert build_snapshot(repo)["phase"]["phase"] == "9 Node"


def test_check_passes_then_fails_on_a_changed_claude_md_line(repo, tmp_path_factory):
    output = tmp_path_factory.mktemp("out") / "self_sources.json"
    call_command("build_self_sources", root=str(repo), output=str(output))
    call_command("build_self_sources", "--check", root=str(repo), output=str(output))
    claude = repo / "CLAUDE.md"
    claude.write_text(
        claude.read_text().replace("Workspace scoping is inviolable.", "Workspaces are optional.")
    )
    with pytest.raises(CommandError, match="out of date"):
        call_command("build_self_sources", "--check", root=str(repo), output=str(output))
    # --check never writes.
    assert "Workspace scoping is inviolable." in output.read_text()


def test_check_fails_when_the_snapshot_is_missing(repo, tmp_path_factory):
    output = tmp_path_factory.mktemp("out") / "missing.json"
    with pytest.raises(CommandError):
        call_command("build_self_sources", "--check", root=str(repo), output=str(output))


def test_missing_docs_fail_loudly(tmp_path):
    with pytest.raises(CommandError, match="CLAUDE.md"):
        call_command("build_self_sources", "--check", root=str(tmp_path))


def test_committed_snapshot_matches_the_repository_docs():
    """The CI gate in-process too: the committed snapshot is current."""
    repo_root = Path(settings.BASE_DIR).parent
    if not (repo_root / "CLAUDE.md").is_file():  # pragma: no cover - image without docs
        pytest.skip("docs are not part of this checkout")
    assert SNAPSHOT_PATH.read_text(encoding="utf-8") == render_snapshot(build_snapshot(repo_root))


# --- assembler ---


def _slot_regex(template):
    pattern = re.escape(template)
    for name in re.findall(r"\{(\w+)\}", template):
        pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>.+?)", 1)
    return re.compile(f"^{pattern}$")


def _source_values(snapshot, tools, read_tools, providers):
    return {
        "identity": {snapshot["identity"]["text"]},
        "phase": {snapshot["phase"]["phase"]},
        "outcome": {snapshot["phase"]["outcome"]},
        "rule": {r["text"] for r in snapshot["rules"]},
        "adr_id": {a["id"] for a in snapshot["adrs"]},
        "adr_title": {a["title"] for a in snapshot["adrs"]},
        "adr_decision": {a["decision"]["text"] for a in snapshot["adrs"]},
        "tools": {", ".join(tools)},
        "read_tools": {", ".join(read_tools)},
        "providers": {", ".join(providers)},
    }


def assert_every_line_traces(content, snapshot, tools, read_tools, providers):
    values = _source_values(snapshot, tools, read_tools, providers)
    regexes = [_slot_regex(t) for t in TEMPLATES.values()]
    for line in content.split("\n"):
        traced = any(
            match is not None
            and all(value in values[slot] for slot, value in match.groupdict().items())
            for match in (r.match(line) for r in regexes)
        )
        assert traced, f"line not traceable to the template + a source: {line!r}"


def test_every_line_of_the_real_block_traces_to_a_source(db):
    snapshot = load_snapshot()
    ModelProvider.objects.create(
        name="local-ollama",
        provider_type="openai_compatible",
        model_id="m",
        base_url="http://secret:1",
    )
    content = build_self_content(snapshot)
    assert content
    assert len(content) <= 1500
    assert_every_line_traces(
        content, snapshot, list_tool_names(), list_read_tool_names(), ["local-ollama"]
    )
    # Names only: never a base URL or key reference.
    assert "secret" not in content and "http" not in content


def test_assembled_block_from_fixture_snapshot(repo):
    snapshot = build_snapshot(repo)
    content = assemble_self_content(
        snapshot, tools=["vault_export"], read_tools=["github_read"], providers=["fake-local"]
    )
    assert content.split("\n") == [
        'Ben Friday\'im. Kimliğim (CLAUDE.md): "Friday OS is a personal-first assistant."',
        'Şu anki faz: 8A Learning memory core. Hedef: "Knows itself"',
        TEMPLATES["developer"],
        "Kullanabildiğim araçlar: vault_export.",
        "Okuma araçlarım: github_read.",
        "Etkin model sağlayıcılarım: fake-local.",
        "Uyduğum kurallar:",
        "- Workspace scoping is inviolable.",
        "- The AI Gateway is the single door.",
        "Kabul edilmiş mimari kararlarım:",
        "- ADR-001 Workspace Is the Data Axis: Every business table carries a `workspace_id`.",
        "- ADR-003 Listed Decision: Anything not listed is gate-class.",
    ]
    assert_every_line_traces(content, snapshot, ["vault_export"], ["github_read"], ["fake-local"])


def test_budget_skips_lines_that_do_not_fit(repo):
    snapshot = build_snapshot(repo)
    tools = [f"tool_{i:03d}" for i in range(200)]
    content = assemble_self_content(snapshot, tools=tools, read_tools=[], providers=[])
    assert len(content) <= 1500
    assert "Kullanabildiğim araçlar" not in content  # too long, skipped whole
    assert "- Workspace scoping is inviolable." in content


def test_no_snapshot_still_yields_template_only_block():
    content = assemble_self_content(None, tools=[], read_tools=[], providers=[])
    assert content == TEMPLATES["developer"]


def test_strip_capability_lines(repo):
    content = assemble_self_content(
        build_snapshot(repo), tools=["vault_export"], read_tools=["r"], providers=["p"]
    )
    stripped = strip_capability_lines(content)
    assert not any(line.startswith(CAPABILITY_PREFIXES) for line in stripped.split("\n"))
    assert "vault_export" not in stripped and "Uyduğum kurallar:" in stripped


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def gateway_spy(monkeypatch):
    calls = []

    def spy(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"assembler called the gateway ({name})")

        return fail

    for name in ("complete", "embed", "transcribe", "synthesize"):
        monkeypatch.setattr(InferenceGateway, name, spy(name))
    return calls


def test_refresh_writes_self_with_zero_model_calls(owner, gateway_spy):
    usage_before = ModelUsage.objects.count()
    block, changed = refresh_self_block()
    assert changed and block.label == "self" and block.version == 1
    assert block.workspace == Workspace.objects.get(owner=owner)
    assert gateway_spy == []
    assert ModelUsage.objects.count() == usage_before


def test_self_changes_only_when_its_sources_do(owner, repo, gateway_spy):
    snapshot = build_snapshot(repo)
    block, changed = refresh_self_block(snapshot)
    assert changed
    block, changed = refresh_self_block(snapshot)
    assert not changed and block.version == 1
    claude = repo / "CLAUDE.md"
    claude.write_text(claude.read_text().replace("personal-first", "owner-first"))
    block, changed = refresh_self_block(build_snapshot(repo))
    assert changed and block.version == 2
    assert "owner-first" in block.content


def test_refresh_without_owner_is_a_noop(db):
    register_user(email="x@test.dev", password=PASSWORD)
    assert refresh_self_block() == (None, False)
    assert not CoreMemoryBlock.objects.exists()


def test_beat_task(owner, gateway_spy):
    assert assemble_self_block_task() is True
    assert assemble_self_block_task() is False


def test_post_migrate_handler_never_raises(owner, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("snapshot broken")

    monkeypatch.setattr(self_block, "refresh_self_block", boom)
    self_block.refresh_self_block_safely(sender=None)  # logged, not raised


def test_post_migrate_handler_is_connected_and_assembles_self(owner):
    from django.apps import apps
    from django.db.models.signals import post_migrate

    from assistant.apps import _refresh_self_block

    sender = apps.get_app_config("assistant")
    assert any(entry[0][0] == "assistant.refresh_self_block" for entry in post_migrate.receivers)
    _refresh_self_block(sender=sender)
    assert CoreMemoryBlock.objects.filter(label="self", workspace__owner=owner).exists()


def test_snapshot_file_is_valid_json_with_hash():
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 1 and len(data["content_hash"]) == 64
