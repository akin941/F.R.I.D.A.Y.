"""Shared deploy fixtures. No test here touches a real SSH server or URL:
`deploys.ssh._run_subprocess` and `deploys.health._http_get` fail loudly
unless a test replaces them, and the runner's `_sleep` is a no-op on a fake
clock (`fake_clock`)."""

import base64
import secrets

import pytest
from rest_framework.test import APIClient

from accounts.authentication import TOKEN_PREFIX, hash_token
from accounts.models import PersonalAccessToken, User
from accounts.services import register_user
from deploys.models import DeployTarget
from deploys.ssh import SSHResult
from workspaces.models import Workspace

PASSWORD = "Str0ngDevPass!42"
SHA_A = "9f8e7d6" + "a" * 33
SHA_B = "a1b2c3d" + "b" * 33
SHA_C = "c0ffee0" + "c" * 33
REPO = "git@github.com:acme/adia-web.git"
SELF_REPO = "git@github.com:akin941/jarvis-os.git"


def _ed25519_public_key():
    blob = (
        len(b"ssh-ed25519").to_bytes(4, "big")
        + b"ssh-ed25519"
        + (32).to_bytes(4, "big")
        + bytes(range(32))
    )
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


HOST_KEY = _ed25519_public_key()


def _forbidden(*args, **kwargs):
    raise AssertionError("a test reached a real SSH/HTTP call")


@pytest.fixture(autouse=True)
def _deploy_settings(settings, tmp_path):
    settings.HARNESS_SELF_REPO_IDENTIFIERS = [SELF_REPO]
    key = tmp_path / "deploy_key"
    key.write_text("placeholder, never read by a test\n")
    settings.DEPLOY_SSH_KEY_PATH = str(key)
    settings.DEPLOY_HEALTH_CHECKS_PER_WINDOW = 4


@pytest.fixture(autouse=True)
def _no_real_remote(monkeypatch):
    monkeypatch.setattr("deploys.ssh._run_subprocess", _forbidden)
    monkeypatch.setattr("deploys.health._http_get", _forbidden)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("deploys.execution._sleep", clock.sleep)
    monkeypatch.setattr("deploys.execution._monotonic", clock.monotonic)
    return clock


class FakeRemote:
    """Scripted stand-in for `deploys.ssh.run_verb`. `health` is a list of
    booleans consumed per health verb (then `health_default`); `hooks` maps
    a verb to a callable run before it answers (to interleave a cancel or
    an edit)."""

    def __init__(self):
        self.calls = []
        self.live_sha = SHA_A
        self.remote = REPO
        self.app = "adia-web"
        self.status_exit = 0
        self.status_extra = ""
        self.deploy_exit = 0
        self.deploy_output = "deployed"
        self.rollback_exit = 0
        self.health = []
        self.health_default = True
        self.transport_fail = set()
        self.hooks = {}

    def __call__(self, target, verb, sha=None, *, timeout):
        self.calls.append((verb, sha))
        hook = self.hooks.get(verb)
        if hook is not None:
            hook()
        if verb in self.transport_fail:
            return SSHResult(exit_code=255, output="ssh: connect failed")
        if verb == "status":
            return SSHResult(
                exit_code=self.status_exit,
                output=(
                    f"app={self.app}\nsha={self.live_sha}\nbranch=main\n"
                    f"remote={self.remote}\nprevious=\n{self.status_extra}"
                ),
            )
        if verb == "deploy":
            if self.deploy_exit in (0, 1):
                self.live_sha = sha
            return SSHResult(exit_code=self.deploy_exit, output=self.deploy_output)
        if verb == "rollback":
            if self.rollback_exit == 0:
                self.live_sha = sha
            return SSHResult(exit_code=self.rollback_exit, output="rolled back")
        if verb == "health":
            ok = self.health.pop(0) if self.health else self.health_default
            return SSHResult(exit_code=0 if ok else 1, output="healthy" if ok else "unhealthy")
        raise AssertionError(verb)

    def verbs(self):
        return [verb for verb, _ in self.calls]


@pytest.fixture
def remote(monkeypatch):
    fake = FakeRemote()
    monkeypatch.setattr("deploys.ssh.run_verb", fake)
    return fake


@pytest.fixture
def url_results(monkeypatch):
    """`health.check_url` answers from this list (then True)."""
    results = []
    monkeypatch.setattr("deploys.health.check_url", lambda url: results.pop(0) if results else True)
    return results


@pytest.fixture
def owner(db):
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner)


def make_target(workspace, owner, **overrides):
    fields = {
        "name": "ADİA",
        "environment": "production",
        "ssh_host": "203.0.113.7",
        "ssh_port": 22,
        "ssh_user": "deploy",
        "host_public_key": HOST_KEY,
        "remote_app": "adia-web",
        "repo_git_url": REPO,
        "tracked_branch": "main",
        "health_url": "",
        "health_timeout_seconds": 120,
        "require_ci_success": False,
        "auto_rollback": True,
        "enabled": True,
        "current_sha": SHA_A,
        **overrides,
    }
    return DeployTarget.objects.create(workspace=workspace, created_by=owner, **fields)


@pytest.fixture
def target(workspace, owner):
    return make_target(workspace, owner)


def pat_client(user, workspace, kind=PersonalAccessToken.Kind.CLI):
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    PersonalAccessToken.objects.create(
        user=user, workspace=workspace, name=f"{kind} token", kind=kind, token_hash=hash_token(raw)
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def agent_user():
    return User.objects.create_user(email="agent@test.dev", is_agent=True)


@pytest.fixture
def workspace_target_with_url(workspace, owner):
    return make_target(workspace, owner, health_url="https://adia.example/healthz")
