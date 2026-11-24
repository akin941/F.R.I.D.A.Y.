"""Phase 12 D17 "Reuse in the harness": attaching owner-approved skill
versions to an implementation request (validated through `skills.ports`,
Gate A lists name + hash), writing them read-only outside the clone, the
read-only mounts, the `.git/info/exclude` line, failing closed when a
version became unavailable before the run or when the produced commit
touches `.claude/skills/`. No real git/Docker except a local `git init`."""

import json
import os
import stat
import subprocess
from unittest import mock

import pytest

from accounts.services import register_user
from approvals.models import ApprovalRequest
from core.channels import CHANNEL_WEB
from harness import implementation_execution, runner
from harness.models import HarnessImplementationJob, HarnessRepo
from harness.services import create_harness_implementation_job
from harness.tools import describe_harness_implement_request
from skills import ports, services
from skills.models import InstructionSkill
from workspaces.models import Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ngDevPass!42"
BASE = "/api/v1/harness/implementation-jobs"


def skill_md(name="pr-yaz", body="Önce özet yaz. Bak: references/check.md"):
    return f'---\nname: {name}\ndescription: "PR yazarken."\n---\n{body}\n'


@pytest.fixture(autouse=True)
def git_push_credential(settings):
    settings.HARNESS_GIT_SSH_KEY_PATH = "/tmp/fake-deploy-key"


@pytest.fixture
def owner():
    user = register_user(email="owner@test.dev", password=PASSWORD, name="Owner")
    user.is_owner = True
    user.save()
    return user


@pytest.fixture
def workspace(owner):
    return Workspace.objects.get(owner=owner, type=Workspace.Type.PERSONAL)


@pytest.fixture
def firm(owner):
    firm = Workspace.objects.create(name="Acme", type=Workspace.Type.COMPANY, owner=owner)
    WorkspaceMember.objects.create(workspace=firm, user=owner, role=WorkspaceMember.Role.OWNER)
    return firm


@pytest.fixture
def repo(workspace):
    return HarnessRepo.objects.create(
        workspace=workspace,
        name="acme-api",
        git_url="git@example.com:acme/api.git",
        enabled=True,
        test_command="make test",
    )


def active_version(workspace, owner, name="pr-yaz", references=None):
    parsed = services.parse_text(skill_md(name=name))
    if references:
        from skills.skill_md import parse_skill_md

        parsed = parse_skill_md(skill_md(name=name), references=references)
    version = services.propose_from_owner(
        actor=owner, channel=CHANNEL_WEB, workspace=workspace, parsed=parsed, origin="upload"
    )
    return services.approve_version(
        actor=owner, channel=CHANNEL_WEB, version=version, expected_sha256=version.content_sha256
    )


# --- the request ----------------------------------------------------------------


def test_request_with_an_active_skill_lists_it_on_gate_a(workspace, owner, repo, web_client):
    version = active_version(workspace, owner)
    response = web_client(owner).post(
        BASE,
        {
            "workspace": str(workspace.id),
            "repo": str(repo.id),
            "goal": "add a migration",
            "skill_versions": [str(version.id)],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    job = HarnessImplementationJob.objects.get()
    assert job.skill_version_ids == [str(version.id)]
    gate_a = job.gate_a_request
    assert gate_a.payload["skills"] == [
        {"id": str(version.id), "name": "pr-yaz", "version": 1, "sha256": version.content_sha256}
    ]
    summary = describe_harness_implement_request(gate_a)
    assert summary["skills"] == [{"name": "pr-yaz", "version": 1, "sha256": version.content_sha256}]
    # Never the skill text.
    assert "Önce özet yaz" not in json.dumps(gate_a.payload, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["other_workspace", "proposed", "archived", "unknown"])
def test_non_attachable_versions_are_a_400_and_create_nothing(
    workspace, firm, owner, repo, web_client, kind
):
    if kind == "other_workspace":
        version_id = str(active_version(firm, owner).id)
    elif kind == "proposed":
        version_id = str(
            services.propose_from_owner(
                actor=owner,
                channel=CHANNEL_WEB,
                workspace=workspace,
                parsed=services.parse_text(skill_md()),
                origin="upload",
            ).id
        )
    elif kind == "archived":
        version = active_version(workspace, owner)
        services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=version.skill)
        version_id = str(version.id)
    else:
        version_id = "00000000-0000-0000-0000-000000000000"
    good = active_version(workspace, owner, name="iyi")
    response = web_client(owner).post(
        BASE,
        {
            "workspace": str(workspace.id),
            "repo": str(repo.id),
            "goal": "add a migration",
            "skill_versions": [str(good.id), version_id],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "harness.skill_not_attachable"
    assert version_id in response.json()["detail"]
    assert not HarnessImplementationJob.objects.exists()
    assert not ApprovalRequest.objects.exists()


@pytest.mark.parametrize("role", [WorkspaceMember.Role.MEMBER, WorkspaceMember.Role.VIEWER])
def test_non_curators_cannot_attach(firm, owner, web_client, role):
    repo = HarnessRepo.objects.create(
        workspace=firm,
        name="r",
        git_url="git@example.com:a/b.git",
        enabled=True,
        test_command="make test",
    )
    version = active_version(firm, owner)
    user = register_user(email=f"{role}@test.dev", password=PASSWORD, name=role)
    WorkspaceMember.objects.create(workspace=firm, user=user, role=role)
    response = web_client(user).post(
        BASE,
        {
            "workspace": str(firm.id),
            "repo": str(repo.id),
            "goal": "g",
            "skill_versions": [str(version.id)],
        },
        format="json",
    )
    assert response.status_code in (403, 404)
    assert not HarnessImplementationJob.objects.exists()


# --- the runner primitives -------------------------------------------------------


def _attachable(workspace, owner, **kwargs):
    version = active_version(workspace, owner, **kwargs)
    return ports.get_attachable_versions(workspace=workspace, version_ids=[version.id])


def test_skill_files_are_written_read_only_outside_the_clone(workspace, owner, tmp_path):
    (tmp_path / "repo").mkdir()
    versions = _attachable(workspace, owner, references={"references/check.md": "- make test\n"})
    with mock.patch.object(subprocess, "run", side_effect=AssertionError("no exec")):
        skills_dir = runner.write_skill_files(str(tmp_path), versions)
    assert skills_dir == str(tmp_path / "skills")
    skill_file = tmp_path / "skills" / "pr-yaz" / "SKILL.md"
    reference = tmp_path / "skills" / "pr-yaz" / "references" / "check.md"
    assert skill_file.read_text("utf-8") == versions[0].skill_md
    assert reference.read_text("utf-8") == "- make test\n"
    for path in (skill_file, reference):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o444
    for directory in (tmp_path / "skills", tmp_path / "skills" / "pr-yaz"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o555
    assert not any((tmp_path / "repo").iterdir())
    # The workspace cleanup still removes everything.
    runner.cleanup_directory(str(tmp_path))
    assert not tmp_path.exists()


def test_no_skills_writes_nothing(tmp_path):
    assert runner.write_skill_files(str(tmp_path), []) is None
    assert not (tmp_path / "skills").exists()


def test_invalid_names_or_paths_are_refused(tmp_path):
    bad = ports.AttachableSkillVersion(
        id="x", skill_name="../evil", version=1, content_sha256="0", skill_md="x"
    )
    with pytest.raises(runner.RunnerError):
        runner.write_skill_files(str(tmp_path), [bad])
    bad_ref = ports.AttachableSkillVersion(
        id="x",
        skill_name="ok-name",
        version=1,
        content_sha256="0",
        skill_md="x",
        references=(("../../repo/x.md", "y"),),
    )
    with pytest.raises(runner.RunnerError):
        runner.write_skill_files(str(tmp_path / "w2"), [bad_ref])


def test_exclude_line_is_added_to_the_clone(tmp_path):
    repo_dir = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo_dir)], check=True)
    runner.exclude_skill_paths(str(repo_dir))
    assert ".claude/skills/" in (repo_dir / ".git" / "info" / "exclude").read_text().splitlines()
    (repo_dir / ".claude" / "skills" / "x").mkdir(parents=True)
    (repo_dir / ".claude" / "skills" / "x" / "SKILL.md").write_text("x")
    status = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ".claude/skills" not in status.stdout


def test_mount_args_are_read_only(settings):
    settings.HARNESS_CONTAINER_SKILLS_PATH = "/home/harness/.claude/skills"
    assert runner.skill_mount_args(None) == []
    args = runner.skill_mount_args("/w/skills")
    assert args == [
        "--mount",
        "type=bind,source=/w/skills,target=/workspace/skills,readonly",
        "--mount",
        "type=bind,source=/w/skills,target=/home/harness/.claude/skills,readonly",
    ]


def test_launches_carry_the_read_only_mounts():
    with mock.patch.object(runner.subprocess, "run") as run:
        run.return_value = mock.Mock(stdout="cid\n")
        runner.launch_implementation_container(
            image="img",
            workspace_dir="/w",
            goal="g",
            test_command="make test",
            base_sha="b",
            cli_env={},
            name="n",
            skills_dir="/w/skills",
        )
    argv = run.call_args.args[0]
    assert "type=bind,source=/w/skills,target=/workspace/skills,readonly" in argv
    assert argv.index("type=bind,source=/w,target=/workspace") < argv.index(
        "type=bind,source=/w/skills,target=/workspace/skills,readonly"
    )
    with mock.patch.object(runner.subprocess, "Popen") as popen:
        runner.launch_interactive_container(
            image="img",
            workspace_dir="/w",
            goal="g",
            test_command="make test",
            base_sha="b",
            cli_env={},
            name="n",
            driver="app_server",
            skills_dir="/w/skills",
        )
    argv = popen.call_args.args[0]
    assert any(item.endswith("/.claude/skills,readonly") for item in argv)
    with mock.patch.object(runner.subprocess, "run") as run:
        run.return_value = mock.Mock(stdout="cid\n")
        runner.launch_implementation_container(
            image="img",
            workspace_dir="/w",
            goal="g",
            test_command="t",
            base_sha="b",
            cli_env={},
            name="n",
        )
    assert not any("skills" in item for item in run.call_args.args[0])


# --- the run ----------------------------------------------------------------------


def _job_with_skill(workspace, owner, repo, **skill_kwargs):
    version = active_version(workspace, owner, **skill_kwargs)
    job = create_harness_implementation_job(
        workspace=workspace,
        actor=owner,
        repo=repo,
        goal="add a migration",
        skill_version_ids=[str(version.id)],
    )
    HarnessImplementationJob.objects.filter(pk=job.pk).update(
        status=HarnessImplementationJob.Status.IMPLEMENTING
    )
    job.refresh_from_db()
    return job, version


def _frame():
    import base64

    payload = json.dumps(
        {"total_cost_usd": "0.01", "usage": {"input_tokens": 10, "output_tokens": 5}}
    )
    encoded = base64.b64encode(payload.encode()).decode()
    return f"{implementation_execution.CLI_OUTPUT_PREFIX} {encoded}\n"


def _patch_runner(monkeypatch, workspace_dir, *, changed_files):
    def clone(**kwargs):
        os.makedirs(os.path.join(workspace_dir, "repo", ".git", "info"), exist_ok=True)
        return workspace_dir

    r = implementation_execution.runner
    monkeypatch.setattr(r, "clone_repo_writable", mock.Mock(side_effect=clone))
    monkeypatch.setattr(r, "resolve_commit_sha", mock.Mock(return_value="base-sha"))
    monkeypatch.setattr(r, "image_for_agent", mock.Mock(return_value="img:1"))
    monkeypatch.setattr(r, "launch_implementation_container", mock.Mock())
    monkeypatch.setattr(r, "container_is_running", mock.Mock(side_effect=[False]))
    monkeypatch.setattr(r, "container_exit_code", mock.Mock(return_value=0))
    monkeypatch.setattr(r, "capture_logs", mock.Mock(return_value=_frame()))
    monkeypatch.setattr(
        r,
        "read_implementation_result",
        mock.Mock(
            return_value={
                "tests": {"ran": True, "exit_code": 0, "passed": True, "command": "make test"},
                "commit_sha": "abc123",
                "changed_files": changed_files,
            }
        ),
    )
    monkeypatch.setattr(r, "remove_container", mock.Mock())
    monkeypatch.setattr(r, "cleanup_directory", mock.Mock())
    monkeypatch.setattr(r, "diff_contains_any", mock.Mock(return_value=False))
    monkeypatch.setattr(r, "create_bundle", mock.Mock())


def test_the_runner_mounts_attached_skills(workspace, owner, repo, monkeypatch, tmp_path):
    job, version = _job_with_skill(workspace, owner, repo)
    workspace_dir = str(tmp_path / "w")
    _patch_runner(monkeypatch, workspace_dir, changed_files=["backend/x.py"])
    with mock.patch.object(implementation_execution, "dispatch_created_review"):
        implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    launch = implementation_execution.runner.launch_implementation_container
    assert launch.call_args.kwargs["skills_dir"] == os.path.join(workspace_dir, "skills")
    skill_file = tmp_path / "w" / "skills" / "pr-yaz" / "SKILL.md"
    assert skill_file.exists() and stat.S_IMODE(skill_file.stat().st_mode) == 0o444
    assert not (tmp_path / "w" / "repo" / ".claude").exists()
    exclude = (tmp_path / "w" / "repo" / ".git" / "info" / "exclude").read_text()
    assert ".claude/skills/" in exclude
    assert job.changed_files == ["backend/x.py"]


def test_a_version_archived_after_the_request_fails_before_the_agent_starts(
    workspace, owner, repo, monkeypatch, tmp_path
):
    job, version = _job_with_skill(workspace, owner, repo)
    services.archive_skill(actor=owner, channel=CHANNEL_WEB, skill=version.skill)
    _patch_runner(monkeypatch, str(tmp_path / "w"), changed_files=["x.py"])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.SKILL_VERSION_UNAVAILABLE
    implementation_execution.runner.launch_implementation_container.assert_not_called()
    assert not (tmp_path / "w" / "skills").exists()


def test_a_hash_that_differs_from_gate_a_fails_closed(
    workspace, owner, repo, monkeypatch, tmp_path
):
    job, _version = _job_with_skill(workspace, owner, repo)
    gate_a = job.gate_a_request
    payload = dict(gate_a.payload)
    payload["skills"] = [dict(payload["skills"][0], sha256="0" * 64)]
    ApprovalRequest.objects.filter(pk=gate_a.pk).update(payload=payload)
    _patch_runner(monkeypatch, str(tmp_path / "w"), changed_files=["x.py"])
    implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.failure_reason == HarnessImplementationJob.FailureReason.SKILL_VERSION_UNAVAILABLE
    implementation_execution.runner.launch_implementation_container.assert_not_called()


def test_a_commit_touching_the_skills_prefix_fails_closed(
    workspace, owner, repo, monkeypatch, tmp_path
):
    """A fake agent copied the mounted skill into the repo: the wrapper
    committed it (it rebuilds `.git/info`), so the host fails the job before
    review, bundle, Gate B or push."""
    job, _version = _job_with_skill(workspace, owner, repo)
    _patch_runner(
        monkeypatch,
        str(tmp_path / "w"),
        changed_files=["backend/x.py", ".claude/skills/pr-yaz/SKILL.md"],
    )
    with mock.patch.object(implementation_execution, "dispatch_created_review") as dispatch:
        implementation_execution.run_harness_implementation_job(job.id)
        dispatch.assert_not_called()
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.FAILED
    assert job.failure_reason == HarnessImplementationJob.FailureReason.SKILL_PATH_IN_COMMIT
    assert job.changed_files == [] and job.produced_commit_sha == ""
    assert not job.reviews.exists() and job.gate_b_request is None
    implementation_execution.runner.create_bundle.assert_not_called()


def test_without_skills_a_claude_skills_path_is_an_ordinary_change(
    workspace, owner, repo, monkeypatch, tmp_path
):
    job = create_harness_implementation_job(
        workspace=workspace, actor=owner, repo=repo, goal="edit the repo's own skills"
    )
    HarnessImplementationJob.objects.filter(pk=job.pk).update(
        status=HarnessImplementationJob.Status.IMPLEMENTING
    )
    _patch_runner(monkeypatch, str(tmp_path / "w"), changed_files=[".claude/skills/a/SKILL.md"])
    with mock.patch.object(implementation_execution, "dispatch_created_review"):
        implementation_execution.run_harness_implementation_job(job.id)
    job.refresh_from_db()
    assert job.status == HarnessImplementationJob.Status.PENDING_REVIEW
    assert "skills" not in job.gate_a_request.payload
    assert not (tmp_path / "w" / "skills").exists()
    assert InstructionSkill.objects.count() == 0
