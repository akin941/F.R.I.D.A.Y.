import re
from urllib.parse import urlsplit

from rest_framework import serializers

from harness.models import HarnessImplementationJob, HarnessJob, HarnessRepo, HarnessReview

_MAX_NAME_CHARS = 255
_MAX_URL_CHARS = 2000
# Mirrors `agents.serializers.AgentCreateSerializer.goal`'s identical cap --
# the same shape of free-text natural-language instruction, one field over.
_MAX_GOAL_CHARS = 2000
# Phase 3 branch 2 (brief §boundary): a curator-authored shell command,
# possibly a multiline heredoc (branch 1's own wrapper scripts are written
# assuming exactly that shape) -- generous but bounded, matching
# `HarnessRepo.test_command`'s own TextField without imposing a DB-level cap.
_MAX_TEST_COMMAND_CHARS = 10000

# The conventional, non-secret SSH username every git host expects
# ("git@github.com:...", "ssh://git@github.com/..."). Anything else in the
# userinfo slot of a git_url is a credential, not a fixed protocol username.
_SSH_USER_PLACEHOLDER = "git"
# scp-like syntax has no "://" scheme, so urlsplit() can't parse its
# "user@host:path" shape -- matched separately before falling back to
# urlsplit for every schemed form (https://, ssh://, ...).
_SCP_LIKE_RE = re.compile(r"^([^@/:]+)@[^/:]+:")


def _reject_embedded_credentials(value):
    """CLAUDE.md 'secrets only via env' / brief §1 'no credentials field':
    a repo URL with an embedded token or password would persist and
    serialize a real credential through this row despite HarnessRepo
    deliberately having no credentials field. The conventional credential-
    free SSH forms (`git@host:path`, `ssh://git@host/path`) are explicitly
    allowed -- their "git@" is the fixed protocol username, not a secret.

    Covers both places a credential can hide in a URL (Codex code-review
    P2, branch 1 rounds 2-3): userinfo (`https://token@host/...`,
    `https://user:pass@host/...`) and query/fragment carriers
    (`https://host/repo.git?token=...`, a signed URL's `#`-fragment) --
    a git clone URL needs neither, so both are rejected outright rather
    than pattern-matched for known credential-parameter names, which would
    always be one vendor convention behind. Malformed input that `urlsplit`
    itself can't parse (e.g. an unbalanced `[` in a bracketed IPv6 host) is
    a validation error, not an unhandled 500."""
    # Checked before the scp-like branch below, not just for schemed URLs
    # (Codex code-review P2, branch 1 round 4): the scp-like branch used to
    # return early, so `git@host:path?token=...`/`#access_token=...` slipped
    # through the same carrier the schemed-URL check already rejects.
    if "?" in value or "#" in value:
        raise serializers.ValidationError(
            "git_url must not include a query string or fragment "
            "(a possible credential carrier); use a plain repo URL."
        )
    scp_match = _SCP_LIKE_RE.match(value)
    if scp_match:
        if scp_match.group(1) != _SSH_USER_PLACEHOLDER:
            raise serializers.ValidationError(
                "git_url must not embed credentials; use the conventional "
                f"'{_SSH_USER_PLACEHOLDER}@host:path' SSH form."
            )
        return value
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise serializers.ValidationError("git_url is not a valid URL.") from exc
    if parsed.password:
        raise serializers.ValidationError("git_url must not embed a password or token.")
    if parsed.username and parsed.username != _SSH_USER_PLACEHOLDER:
        raise serializers.ValidationError(
            "git_url must not embed credentials in the URL; use the "
            f"conventional '{_SSH_USER_PLACEHOLDER}@host' SSH form or a "
            "credential-free HTTPS URL."
        )
    # No separate parsed.query/parsed.fragment check needed here -- the
    # up-front "?"/"#" reject above already covers every URL shape,
    # schemed or scp-like, before this point is ever reached.
    return value


class HarnessRepoSerializer(serializers.ModelSerializer):
    class Meta:
        model = HarnessRepo
        fields = [
            "id",
            "workspace",
            "name",
            "git_url",
            "default_branch",
            "runner",
            "enabled",
            "sensitive",
            "test_command",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class HarnessRepoCreateSerializer(serializers.Serializer):
    # `workspace` is resolved through `for_actor()` before the service runs,
    # never trusted raw (mirrors `tasks.serializers.TaskCreateSerializer`).
    workspace = serializers.UUIDField()
    name = serializers.CharField(max_length=_MAX_NAME_CHARS)
    git_url = serializers.CharField(max_length=_MAX_URL_CHARS)
    default_branch = serializers.CharField(
        max_length=_MAX_NAME_CHARS, required=False, default="main"
    )
    runner = serializers.ChoiceField(
        choices=HarnessRepo.Runner.choices, required=False, default=HarnessRepo.Runner.MAC_LOCAL
    )
    # A repo is inert until explicitly enabled, never auto-enabled -- but the
    # curator registering it may still choose to enable it in the same call.
    enabled = serializers.BooleanField(required=False, default=False)
    sensitive = serializers.BooleanField(required=False, default=False)
    # Codex code-review P2, branch 2 round 1: an earlier version added the
    # `test_command` field/hard-check to the model and
    # `create_harness_implementation_job` without ever exposing it here or
    # in `create_repo` -- every repo registered through the actual
    # curator-facing API/service was permanently unable to run
    # implementation jobs at all, since the field could only ever be set
    # by editing the row out of band.
    test_command = serializers.CharField(
        max_length=_MAX_TEST_COMMAND_CHARS, required=False, default="", allow_blank=True
    )

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("name must not be empty.")
        return value

    def validate_git_url(self, value):
        if not value.strip():
            raise serializers.ValidationError("git_url must not be empty.")
        return _reject_embedded_credentials(value)


class HarnessRepoEditSerializer(serializers.Serializer):
    """PATCH body -- every field optional, only supplied fields are changed
    (mirrors `tasks.serializers.TaskEditSerializer`'s partial-update shape)."""

    name = serializers.CharField(max_length=_MAX_NAME_CHARS, required=False)
    git_url = serializers.CharField(max_length=_MAX_URL_CHARS, required=False)
    default_branch = serializers.CharField(max_length=_MAX_NAME_CHARS, required=False)
    runner = serializers.ChoiceField(choices=HarnessRepo.Runner.choices, required=False)
    enabled = serializers.BooleanField(required=False)
    sensitive = serializers.BooleanField(required=False)
    test_command = serializers.CharField(
        max_length=_MAX_TEST_COMMAND_CHARS, required=False, allow_blank=True
    )

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("name must not be empty.")
        return value

    def validate_git_url(self, value):
        if not value.strip():
            raise serializers.ValidationError("git_url must not be empty.")
        return _reject_embedded_credentials(value)


class HarnessJobSerializer(serializers.ModelSerializer):
    """List/create/cancel response shape -- no `briefing` (kept light for
    the list view; `HarnessJobDetailSerializer` below adds it for the one
    job a curator is actually looking at). Mirrors
    `agents.serializers.AgentSerializer`'s identical light/detail split."""

    class Meta:
        model = HarnessJob
        fields = [
            "id",
            "workspace",
            "repo",
            "goal",
            "agent",
            "sensitivity",
            "status",
            "failure_reason",
            "created_by",
            "started_at",
            "elapsed_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class HarnessJobDetailSerializer(HarnessJobSerializer):
    """Adds `briefing` -- the R2 mitigation's structural guarantee extends
    to this render path too (brief §4): `summary`/`findings` are the
    gateway-produced, trustworthy fields; `raw_transcript` inside the same
    JSON blob is the CLI's own low-trust output, and the frontend must
    render it visibly distinguished, never presented as equally
    trustworthy prose."""

    class Meta(HarnessJobSerializer.Meta):
        fields = [*HarnessJobSerializer.Meta.fields, "briefing"]
        read_only_fields = fields


class HarnessJobCreateSerializer(serializers.Serializer):
    # `workspace`/`repo` are resolved through `for_actor()`/
    # `create_harness_job`'s own re-lock before the service runs, never
    # trusted raw (mirrors `HarnessRepoCreateSerializer`'s identical
    # shape).
    workspace = serializers.UUIDField()
    repo = serializers.UUIDField()
    goal = serializers.CharField(max_length=_MAX_GOAL_CHARS)
    agent = serializers.ChoiceField(
        choices=HarnessJob.Agent.choices, required=False, default=HarnessJob.Agent.CLAUDE_CODE
    )

    def validate_goal(self, value):
        if not value.strip():
            raise serializers.ValidationError("goal must not be empty.")
        return value


# --- Implementation jobs (Phase 3 branch 6) ---------------------------------


def _gate_summary(approval_request):
    """Only `{id, status, decided_at}` of a linked gate -- never its
    `payload` (plan-review round 1 of Phase 3; the approvals API's own
    `display_summary` is the one place a decider sees gate content). A
    gate revoked by cancel/worktree expiry reports `expired`."""
    if approval_request is None:
        return None
    return {
        "id": str(approval_request.id),
        "status": approval_request.status,
        "decided_at": approval_request.decided_at,
    }


class HarnessReviewSerializer(serializers.ModelSerializer):
    """`summary`/`findings` are gateway-produced; `raw_transcript` is the
    reviewing CLI's own low-trust output -- the UI renders it fenced behind
    a disclosure (R2), never as Jarvis's own words."""

    class Meta:
        model = HarnessReview
        fields = [
            "id",
            "agent",
            "status",
            "verdict",
            "summary",
            "findings",
            "raw_transcript",
            "elapsed_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class HarnessImplementationJobSerializer(serializers.ModelSerializer):
    """List/create/cancel shape. Never exposes `worktree_path` or
    `container_id` (runner-host internals)."""

    repo_name = serializers.CharField(source="repo.name", read_only=True)
    reviewer_agent = serializers.SerializerMethodField()
    gate_a = serializers.SerializerMethodField()
    gate_b = serializers.SerializerMethodField()

    class Meta:
        model = HarnessImplementationJob
        fields = [
            "id",
            "workspace",
            "repo",
            "repo_name",
            "discovery_job",
            "goal",
            "agent",
            "reviewer_agent",
            "status",
            "failure_reason",
            "cancel_requested",
            "branch_name",
            "created_by",
            "started_at",
            "elapsed_seconds",
            "created_at",
            "updated_at",
            "gate_a",
            "gate_b",
        ]
        read_only_fields = fields

    def get_reviewer_agent(self, obj):
        from harness.review_execution import select_review_agent
        from harness.services import HarnessInvalidTransition

        try:
            return str(select_review_agent(obj.agent))
        except HarnessInvalidTransition:
            return ""

    def get_gate_a(self, obj):
        return _gate_summary(obj.gate_a_request)

    def get_gate_b(self, obj):
        return _gate_summary(obj.gate_b_request)


class HarnessImplementationJobDetailSerializer(HarnessImplementationJobSerializer):
    reviews = HarnessReviewSerializer(many=True, read_only=True)

    class Meta(HarnessImplementationJobSerializer.Meta):
        fields = [
            *HarnessImplementationJobSerializer.Meta.fields,
            "base_commit_sha",
            "produced_commit_sha",
            "changed_files",
            "diff_summary",
            "test_result",
            "push_result",
            "worktree_expires_at",
            "raw_transcript",
            "reviews",
        ]
        read_only_fields = fields


class HarnessImplementationJobCreateSerializer(serializers.Serializer):
    # Same resolution discipline as `HarnessJobCreateSerializer`: ids are
    # resolved through `for_actor()` before the service runs. The agent is
    # not client-selectable -- the service's own default (the only
    # selectable implementation agent) applies, and the reviewer is picked
    # server-side as a different vendor.
    workspace = serializers.UUIDField()
    repo = serializers.UUIDField()
    goal = serializers.CharField(max_length=_MAX_GOAL_CHARS)
    discovery_job = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_goal(self, value):
        if not value.strip():
            raise serializers.ValidationError("goal must not be empty.")
        return value
