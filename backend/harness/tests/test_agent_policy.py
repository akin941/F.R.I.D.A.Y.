"""`HARNESS_AGENT_POLICY` (Phase 5 D11): agent resolution, the boot-time
self-pairing check and `agent_routing_reason` on job rows."""

import pytest
from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import SystemCheckError

from accounts.services import register_user
from audit.models import AuditLog
from harness import policy
from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo
from harness.services import (
    HarnessInvalidTransition,
    create_harness_implementation_job,
    create_harness_job,
)
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
KNOWN = {"claude_code", "codex_cli"}

SHIPPED = {
    "discovery": {"default": "claude_code", "selectable": ["claude_code"]},
    "implementation": {
        "default": "claude_code",
        "selectable": ["claude_code"],
        "reviewers": {"claude_code": "codex_cli"},
    },
}


def _with_implementation(**entry):
    return {**SHIPPED, "implementation": {**SHIPPED["implementation"], **entry}}


# --- policy_errors (pure) ---


def test_shipped_settings_policy_is_valid(settings):
    assert settings.HARNESS_AGENT_POLICY == SHIPPED
    assert policy.policy_errors(settings.HARNESS_AGENT_POLICY, known_agents=KNOWN) == []


def test_self_pairing_is_an_error():
    errors = policy.policy_errors(
        _with_implementation(reviewers={"claude_code": "claude_code"}), known_agents=KNOWN
    )
    assert any("Self-pairing" in e for e in errors)


def test_selectable_implementer_without_reviewer_is_an_error():
    errors = policy.policy_errors(
        _with_implementation(selectable=["claude_code", "codex_cli"]), known_agents=KNOWN
    )
    assert any("'codex_cli' has no cross-vendor reviewer" in e for e in errors)


def test_turning_on_the_codex_slot_with_a_pairing_is_valid():
    turned_on = _with_implementation(
        selectable=["claude_code", "codex_cli"],
        reviewers={"claude_code": "codex_cli", "codex_cli": "claude_code"},
    )
    assert policy.policy_errors(turned_on, known_agents=KNOWN) == []


@pytest.mark.parametrize(
    "broken",
    [
        None,
        {},
        {**SHIPPED, "discovery": {"default": "gemini_cli", "selectable": ["gemini_cli"]}},
        {**SHIPPED, "discovery": {"default": "claude_code", "selectable": []}},
        _with_implementation(reviewers={"claude_code": "gemini_cli"}),
        _with_implementation(reviewers=["codex_cli"]),
    ],
)
def test_malformed_policies_are_errors(broken):
    assert policy.policy_errors(broken, known_agents=KNOWN)


# --- the system check refuses to boot ---


def test_system_check_passes_on_shipped_policy():
    assert [e for e in run_checks() if e.id == "harness.E001"] == []


def test_system_check_fails_boot_on_self_pairing(settings):
    settings.HARNESS_AGENT_POLICY = _with_implementation(reviewers={"claude_code": "claude_code"})
    errors = [e for e in run_checks() if e.id == "harness.E001"]
    assert errors and all(e.is_serious() for e in errors)
    with pytest.raises(SystemCheckError):
        call_command("check")


# --- resolve_agent / reviewer_for ---


def test_resolve_agent_default_and_owner_selected():
    assert policy.resolve_agent("discovery") == ("claude_code", "policy:default")
    assert policy.resolve_agent("discovery", "") == ("claude_code", "policy:default")
    assert policy.resolve_agent("discovery", "claude_code") == ("claude_code", "owner_selected")
    with pytest.raises(policy.AgentNotSelectable):
        policy.resolve_agent("discovery", "codex_cli")
    with pytest.raises(policy.AgentNotSelectable):
        policy.resolve_agent("implementation", "codex_cli")  # slot off


def test_reviewer_for_follows_the_pairing_and_never_self_pairs(settings):
    assert policy.reviewer_for("claude_code") == "codex_cli"
    assert policy.reviewer_for("codex_cli") is None
    settings.HARNESS_AGENT_POLICY = _with_implementation(reviewers={"claude_code": "claude_code"})
    assert policy.reviewer_for("claude_code") is None


# --- agent_routing_reason on job rows (services + API) ---


@pytest.fixture
def owner(db):
    return register_user(email="owner@test.dev", password=PASSWORD, name="Owner")


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="friday",
        git_url="git@example.com:x/y.git",
        enabled=True,
        test_command="pytest -q",
    )


@pytest.fixture(autouse=True)
def no_dispatch(settings, monkeypatch):
    monkeypatch.setattr("harness.services.dispatch_harness_job", lambda job_id: None)


@pytest.mark.django_db
class TestJobRoutingReasons:
    def test_discovery_default(self, workspace, owner, repo):
        job = create_harness_job(workspace=workspace, actor=owner, repo=repo, goal="look")
        assert job.agent == HarnessJob.Agent.CLAUDE_CODE
        assert job.agent_routing_reason == "policy:default"
        entry = AuditLog.objects.get(action="harness.job_created")
        assert entry.metadata["agent_routing_reason"] == "policy:default"

    def test_discovery_owner_selected(self, workspace, owner, repo):
        job = create_harness_job(
            workspace=workspace, actor=owner, repo=repo, goal="look", agent="claude_code"
        )
        assert job.agent_routing_reason == "owner_selected"

    def test_discovery_unselectable_agent_refused(self, workspace, owner, repo):
        with pytest.raises(HarnessInvalidTransition):
            create_harness_job(
                workspace=workspace, actor=owner, repo=repo, goal="look", agent="codex_cli"
            )
        assert not HarnessJob.objects.exists()

    def test_implementation_default(self, workspace, owner, repo):
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="build"
        )
        assert job.agent == HarnessImplementationJob.Agent.CLAUDE_CODE
        assert job.agent_routing_reason == "policy:default"
        assert job.gate_a_request.payload["reviewer_agent"] == "codex_cli"

    def test_implementation_follows_a_changed_policy_default(
        self, workspace, owner, repo, settings
    ):
        settings.HARNESS_AGENT_POLICY = _with_implementation(
            default="codex_cli",
            selectable=["codex_cli"],
            reviewers={"codex_cli": "claude_code"},
        )
        job = create_harness_implementation_job(
            workspace=workspace, actor=owner, repo=repo, goal="build"
        )
        assert job.agent == "codex_cli"
        assert job.gate_a_request.payload["reviewer_agent"] == "claude_code"

    def test_api_omitted_agent_is_policy_default(self, workspace, owner, repo, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/jobs",
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "look"},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["agent"] == "claude_code"
        assert response.json()["agent_routing_reason"] == "policy:default"

    def test_api_named_agent_is_owner_selected(self, workspace, owner, repo, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/jobs",
            {
                "workspace": str(workspace.id),
                "repo": str(repo.id),
                "goal": "look",
                "agent": "claude_code",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["agent_routing_reason"] == "owner_selected"

    def test_api_implementation_job_exposes_reason(self, workspace, owner, repo, web_client):
        response = web_client(owner).post(
            "/api/v1/harness/implementation-jobs",
            {"workspace": str(workspace.id), "repo": str(repo.id), "goal": "build"},
            format="json",
        )
        assert response.status_code == 201, response.content
        assert response.json()["agent_routing_reason"] == "policy:default"
        assert response.json()["reviewer_agent"] == "codex_cli"
